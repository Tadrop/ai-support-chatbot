"""FastAPI chat backend.

Endpoints
---------
POST /session/start   — capture name + email, show GDPR notice, return session_id
POST /chat            — receive message, run RAG pipeline, return answer or handoff
GET  /health          — liveness probe

Dashboard router mounted at /dashboard.

Dependency injection
--------------------
`get_embedder`, `get_index`, `get_claude`, `get_handoff`, `get_turn_logger`
are FastAPI dependencies that return singleton instances of the real clients.
In tests, override them with fakes via `app.dependency_overrides`.

Chat flow
---------
1. Retrieve top-k chunks from the index → retrieval_confidence.
2. If retrieval_confidence < threshold → handoff (skip Claude entirely).
3. Call Claude. On exception → handoff.
4. Cited-URL self-check. If any cited URL was not in retrieved context → handoff.
5. If Claude says in_scope=False → handoff.
6. If in_scope=True but no citations were provided → handoff
   (per CLAUDE.md: "never let an answer ship without citation").
7. Otherwise return the answer.

Turn logging happens exactly once per request, at the end.

NOTE: this file deliberately does NOT use `from __future__ import annotations`.
slowapi's `@limiter.limit` decorator wraps endpoint functions, and PEP 563's
stringified annotations break FastAPI's ability to resolve Pydantic body
models after the wrap (`PydanticUndefinedAnnotation: name '...' not defined`).
The modern `X | Y` syntax used below works natively in Python 3.10+ without
the future import.
"""

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from src.chat.retrieval import is_confident, retrieve
from src.chat.session import (
    SessionEntry,
    append_turn,
    create_session,
    get_session,
    session_cleanup_loop,
)
from src.config import get_settings
from src.dashboard.app import router as dashboard_router
from src.dashboard.logger import TurnLogger
from src.handoff.handler import (
    HandoffHandlerProtocol,
    HandoffPayload,
    get_handoff_handler,
)
from src.logging_setup import configure_logging, get_logger
from src.protocols import ClaudeClientProtocol, EmbedderProtocol, VectorIndexProtocol
from src.schemas import (
    GDPR_NOTICE,
    AnswerFlag,
    ChatApiResponse,
    ChatRequest,
    ChatResponse,
    SessionStartRequest,
    SessionStartResponse,
)
from src.validator.url_check import validate_citations
import time

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Singletons (real implementations, constructed lazily once per process)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _real_embedder() -> EmbedderProtocol:
    from src.ingest.embedder import VoyageEmbedder
    return VoyageEmbedder()


@lru_cache(maxsize=1)
def _real_index() -> VectorIndexProtocol:
    from src.index.pinecone_client import PineconeIndex
    return PineconeIndex()


@lru_cache(maxsize=1)
def _real_claude() -> ClaudeClientProtocol:
    from src.chat.claude_client import ClaudeClient
    return ClaudeClient()


@lru_cache(maxsize=1)
def _real_logger() -> TurnLogger:
    return TurnLogger()


@lru_cache(maxsize=1)
def _real_handoff() -> HandoffHandlerProtocol:
    return get_handoff_handler()


# FastAPI dependency functions — override these in tests.
def get_embedder() -> EmbedderProtocol:
    return _real_embedder()


def get_index() -> VectorIndexProtocol:
    return _real_index()


def get_claude() -> ClaudeClientProtocol:
    return _real_claude()


def get_turn_logger() -> TurnLogger:
    return _real_logger()


def get_handoff() -> HandoffHandlerProtocol:
    return _real_handoff()


# ---------------------------------------------------------------------------
# Lifespan — background session-cleanup task
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(session_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Handoff dispatch (separate from logging)
# ---------------------------------------------------------------------------

_HANDOFF_ANSWER = (
    "I don't have that information in my knowledge base. "
    "I've connected you with our customer service team — they'll be in touch shortly."
)
_ERROR_ANSWER = (
    "Something went wrong on my end. I've alerted our team and they'll follow up with you."
)


def _send_handoff(
    handler: HandoffHandlerProtocol,
    session: SessionEntry,
    query: str,
) -> None:
    """Build a HandoffPayload and dispatch it. Never raises — logs and swallows."""
    transcript = "\n".join(
        f"{t['role'].upper()}: {t['content']}" for t in session.history
    )
    payload = HandoffPayload(
        name=session.name,
        email=session.email,
        query=query,
        transcript=transcript,
    )
    try:
        handler.send(payload)
    except Exception as e:
        log.error("handoff.failed", error=str(e))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)

    app = FastAPI(
        title="GreenLeaf Customer Service Chatbot",
        version="1.0.0",
        docs_url="/docs" if s.app_env != "prod" else None,
        lifespan=_lifespan,
    )

    # Rate limiter — per-IP, configurable, can be disabled in tests via
    # RATE_LIMIT_ENABLED=false. Limits are read from settings at startup.
    limiter = Limiter(
        key_func=get_remote_address,
        enabled=s.rate_limit_enabled,
        default_limits=[],
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    origins = [o.strip() for o in s.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["POST", "GET"],
        allow_headers=["Content-Type", "Authorization"],
    )

    app.include_router(dashboard_router, prefix="/dashboard")

    # --- /health ---
    @app.get("/health")
    def health():
        return {"status": "ok"}

    # --- /session/start ---
    @app.post("/session/start", response_model=SessionStartResponse)
    @limiter.limit(s.rate_limit_session)
    def session_start(request: Request, body: SessionStartRequest):
        if not body.gdpr_consent:
            raise HTTPException(status_code=400, detail="GDPR consent required.")
        entry = create_session(name=body.name, email=body.email)
        return SessionStartResponse(session_id=entry.session_id, gdpr_notice=GDPR_NOTICE)

    # --- /chat ---
    @app.post("/chat", response_model=ChatApiResponse)
    @limiter.limit(s.rate_limit_chat)
    def chat(
        request: Request,
        body: ChatRequest,
        embedder: EmbedderProtocol = Depends(get_embedder),
        index: VectorIndexProtocol = Depends(get_index),
        claude: ClaudeClientProtocol = Depends(get_claude),
        handoff: HandoffHandlerProtocol = Depends(get_handoff),
        turn_logger: TurnLogger = Depends(get_turn_logger),
    ) -> ChatApiResponse:
        t0 = time.perf_counter()
        session = get_session(body.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found or expired.")

        query = body.message.strip()
        append_turn(body.session_id, "user", query)

        # 1. Retrieve
        chunks, retrieval_confidence = retrieve(query, embedder, index)

        # 2. Low retrieval confidence → handoff (skip Claude entirely)
        if not is_confident(retrieval_confidence):
            return _finalise_handoff(
                handoff_handler=handoff,
                session=session,
                query=query,
                turn_logger=turn_logger,
                t0=t0,
                retrieval_confidence=retrieval_confidence,
                llm_confidence=None,
                reason="low_retrieval_confidence",
            )

        # 3. Call Claude
        try:
            llm_response: ChatResponse = claude.complete(query, chunks)
        except Exception as e:
            log.error("chat.claude_error", error=str(e))
            return _finalise_handoff(
                handoff_handler=handoff,
                session=session,
                query=query,
                turn_logger=turn_logger,
                t0=t0,
                retrieval_confidence=retrieval_confidence,
                llm_confidence=None,
                reason="claude_error",
                error_answer=_ERROR_ANSWER,
            )

        # 4. Cited-URL self-check (returns None if any cited URL was hallucinated)
        validated = validate_citations(llm_response, chunks)

        # 5. Decide: ship the answer or hand off?
        if validated is None:
            reason = "hallucinated_citation"
        elif not llm_response.in_scope:
            reason = "out_of_scope"
        elif not llm_response.cited_urls:
            # CLAUDE.md: "never let an answer ship without citation"
            reason = "no_citations_provided"
        else:
            reason = None

        if reason is not None:
            return _finalise_handoff(
                handoff_handler=handoff,
                session=session,
                query=query,
                turn_logger=turn_logger,
                t0=t0,
                retrieval_confidence=retrieval_confidence,
                llm_confidence=llm_response.confidence,
                reason=reason,
            )

        # 6. Answer is grounded and in scope — ship it
        assert validated is not None  # narrow type for the type checker
        latency_ms = int((time.perf_counter() - t0) * 1000)
        answer = validated.answer
        cited = validated.cited_urls

        turn_logger.log(
            session_id=body.session_id,
            customer_name=session.name,
            customer_email=session.email,
            query=query,
            answer=answer,
            cited_urls=cited,
            retrieval_confidence=retrieval_confidence,
            llm_confidence=llm_response.confidence,
            answer_flag="in_kb",
            latency_ms=latency_ms,
        )
        append_turn(body.session_id, "assistant", answer)
        log.info(
            "chat.turn_done",
            flag="in_kb",
            latency_ms=latency_ms,
            session_id=body.session_id,
        )

        return ChatApiResponse(
            answer=answer,
            cited_urls=cited,
            confidence=llm_response.confidence,
            handoff=False,
            answer_flag="in_kb",
            session_id=body.session_id,
        )

    return app


def _finalise_handoff(
    *,
    handoff_handler: HandoffHandlerProtocol,
    session: SessionEntry,
    query: str,
    turn_logger: TurnLogger,
    t0: float,
    retrieval_confidence: float,
    llm_confidence: float | None,
    reason: str,
    error_answer: str | None = None,
) -> ChatApiResponse:
    """Run the handoff path: dispatch payload, log once, return ChatApiResponse."""
    answer = error_answer if error_answer is not None else _HANDOFF_ANSWER
    flag: AnswerFlag = "handoff"

    _send_handoff(handoff_handler, session, query)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    turn_logger.log(
        session_id=session.session_id,
        customer_name=session.name,
        customer_email=session.email,
        query=query,
        answer=answer,
        cited_urls=[],
        retrieval_confidence=retrieval_confidence,
        llm_confidence=llm_confidence,
        answer_flag=flag,
        latency_ms=latency_ms,
    )
    append_turn(session.session_id, "assistant", answer)
    log.info(
        "chat.handoff",
        reason=reason,
        latency_ms=latency_ms,
        session_id=session.session_id,
    )
    return ChatApiResponse(
        answer=answer,
        cited_urls=[],
        confidence=llm_confidence if llm_confidence is not None else retrieval_confidence,
        handoff=True,
        answer_flag=flag,
        session_id=session.session_id,
    )


app = create_app()
