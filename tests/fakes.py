"""In-memory fakes for every I/O protocol boundary.

FakeEmbedder        — satisfies EmbedderProtocol, never calls Voyage AI.
FakeVectorIndex     — satisfies VectorIndexProtocol, stores vectors in a dict.
FakeClaudeClient    — satisfies ClaudeClientProtocol, returns scripted ChatResponses.
FakeHandoffHandler  — satisfies HandoffHandlerProtocol, records sent payloads.
make_crawler        — factory that returns a CrawlerFn yielding fixed pages.

All fakes expose call-counters so tests can assert on side-effects without
inspecting external state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from src.handoff.handler import HandoffPayload
from src.protocols import (
    ClaudeClientProtocol,
    CrawlerFn,
    EmbedderProtocol,
    VectorIndexProtocol,
)
from src.schemas import (
    ChatResponse,
    Chunk,
    ContentType,
    CrawledPage,
    EmbeddedChunk,
    RetrievedChunk,
)

# ---------------------------------------------------------------------------
# Deterministic vector helper
# ---------------------------------------------------------------------------

_DIM = 8  # tiny dimension for tests; real dim is 1024


def _det_vector(text: str, dim: int = _DIM) -> list[float]:
    """Deterministic pseudo-vector from text hash — no randomness, no API call."""
    digest = hashlib.sha256(text.encode()).digest()
    values: list[float] = []
    for i in range(dim):
        values.append((digest[i % len(digest)] / 255.0) * 2 - 1)
    return values


# ---------------------------------------------------------------------------
# FakeEmbedder
# ---------------------------------------------------------------------------

class FakeEmbedder:
    """Satisfies EmbedderProtocol. Returns deterministic vectors synchronously."""

    def __init__(self, dim: int = _DIM) -> None:
        self.dim = dim
        self.embed_calls: int = 0          # number of embed_chunks() calls
        self.chunks_embedded: int = 0      # total individual chunks embedded

    def embed_chunks(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        self.embed_calls += 1
        self.chunks_embedded += len(chunks)
        return [
            EmbeddedChunk(chunk=c, vector=_det_vector(c.text, self.dim))
            for c in chunks
        ]

    def embed_query(self, text: str) -> list[float]:
        return _det_vector(text, self.dim)


# ---------------------------------------------------------------------------
# FakeVectorIndex
# ---------------------------------------------------------------------------

class FakeVectorIndex:
    """Satisfies VectorIndexProtocol. Stores everything in memory."""

    def __init__(self, prepopulate: dict[str, dict[str, Any]] | None = None) -> None:
        # { id: {"vector": [...], "metadata": {...}} }
        self._store: dict[str, dict[str, Any]] = dict(prepopulate or {})
        self.upsert_calls: int = 0
        self.delete_calls: int = 0
        self.query_calls: int = 0

    # --- writes ---

    def upsert(self, embedded: list[EmbeddedChunk]) -> int:
        self.upsert_calls += 1
        for ec in embedded:
            self._store[ec.chunk.id] = {
                "vector": ec.vector,
                "metadata": {
                    "url": str(ec.chunk.url),
                    "title": ec.chunk.title,
                    "content_type": ec.chunk.content_type,
                    "chunk_index": ec.chunk.chunk_index,
                    "text": ec.chunk.text,
                    "content_hash": ec.chunk.content_hash,
                },
            }
        return len(embedded)

    def delete(self, ids: Iterable[str]) -> int:
        self.delete_calls += 1
        removed = 0
        for vid in ids:
            if vid in self._store:
                del self._store[vid]
                removed += 1
        return removed

    # --- reads ---

    def query(self, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        """Cosine similarity over in-memory vectors — good enough for test assertions."""
        self.query_calls += 1
        if not self._store:
            return []

        def _cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb + 1e-9)

        scored = [
            (vid, _cosine(vector, payload["vector"]), payload["metadata"])
            for vid, payload in self._store.items()
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [
            RetrievedChunk(
                id=vid,
                score=score,
                url=md.get("url", ""),
                title=md.get("title", ""),
                content_type=md.get("content_type", "other"),
                text=md.get("text", ""),
                chunk_index=int(md.get("chunk_index", 0)),
            )
            for vid, score, md in scored[:top_k]
        ]

    def fetch_hashes(self, ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for vid in ids:
            if vid in self._store:
                h = self._store[vid]["metadata"].get("content_hash", "")
                if h:
                    out[vid] = h
        return out

    def list_all_ids(self) -> list[str]:
        return list(self._store.keys())

    # --- test helpers ---

    def stored_ids(self) -> set[str]:
        return set(self._store.keys())

    def get_hash(self, vid: str) -> str | None:
        entry = self._store.get(vid)
        return entry["metadata"].get("content_hash") if entry else None


# ---------------------------------------------------------------------------
# Fake crawler factory
# ---------------------------------------------------------------------------

def _make_page(
    url: str,
    text: str,
    title: str = "",
    content_type: ContentType = "other",
) -> CrawledPage:
    return CrawledPage(
        url=url,
        title=title or url.split("/")[-1],
        content_type=content_type,
        text=f"<html><body><main><p>{text}</p></main></body></html>",
        last_modified=None,
        fetched_at=datetime.now(timezone.utc),
    )


# A small corpus of realistic-ish page content to use across tests.
SAMPLE_PAGES: list[CrawledPage] = [
    _make_page(
        "https://example.test/shipping",
        "We ship all orders within 2 business days. Standard delivery takes 5–7 days. "
        "Express shipping is available at checkout for an additional fee.",
        title="Shipping Policy",
        content_type="policy",
    ),
    _make_page(
        "https://example.test/returns",
        "You may return any item within 30 days of purchase for a full refund. "
        "Items must be unused and in original packaging. Contact support to start a return.",
        title="Returns & Refunds",
        content_type="policy",
    ),
    _make_page(
        "https://example.test/products/rose-food",
        "GreenLeaf Rose Food is a balanced 10-10-10 NPK fertiliser formulated for roses. "
        "Apply every two weeks during the growing season. Price: £12.99.",
        title="Rose Food",
        content_type="product",
    ),
    _make_page(
        "https://example.test/faq",
        "Q: How do I track my order? A: You will receive a tracking link by email once dispatched.\n\n"
        "Q: Do you ship internationally? A: We currently ship within the UK only.",
        title="FAQ",
        content_type="faq",
    ),
]


def make_crawler(pages: list[CrawledPage] | None = None) -> CrawlerFn:
    """Return a CrawlerFn that yields `pages` (defaults to SAMPLE_PAGES)."""
    corpus = pages if pages is not None else SAMPLE_PAGES

    async def _crawler() -> list[CrawledPage]:
        return list(corpus)

    return _crawler


# ---------------------------------------------------------------------------
# FakeClaudeClient — satisfies ClaudeClientProtocol
# ---------------------------------------------------------------------------

# A scripter: given (query, chunks), return the ChatResponse the test expects.
ClaudeScripter = Callable[[str, list[RetrievedChunk]], ChatResponse]


def _default_scripter(query: str, chunks: list[RetrievedChunk]) -> ChatResponse:
    """Default behaviour: cite the top chunk, in_scope=True, confidence=0.9."""
    if not chunks:
        return ChatResponse(
            answer="I don't have that information.",
            cited_urls=[],
            confidence=0.0,
            in_scope=False,
        )
    return ChatResponse(
        answer=f"Based on the documentation: {chunks[0].text[:120].strip()}...",
        cited_urls=[chunks[0].url],
        confidence=0.9,
        in_scope=True,
    )


class FakeClaudeClient:
    """Satisfies ClaudeClientProtocol. Returns scripted ChatResponses, no API call."""

    def __init__(self, scripter: ClaudeScripter | None = None) -> None:
        self._scripter = scripter or _default_scripter
        self.calls: list[tuple[str, list[RetrievedChunk]]] = []
        self.raise_next: Exception | None = None

    def complete(self, query: str, chunks: list[RetrievedChunk]) -> ChatResponse:
        self.calls.append((query, list(chunks)))
        if self.raise_next is not None:
            err = self.raise_next
            self.raise_next = None
            raise err
        return self._scripter(query, chunks)


def script_response(
    answer: str = "Test answer",
    cited_urls: list[str] | None = None,
    confidence: float = 0.9,
    in_scope: bool = True,
) -> ClaudeScripter:
    """Build a scripter that always returns the same ChatResponse — for one-off tests."""
    resp = ChatResponse(
        answer=answer,
        cited_urls=cited_urls or [],
        confidence=confidence,
        in_scope=in_scope,
    )
    return lambda _q, _c: resp


# ---------------------------------------------------------------------------
# FakeHandoffHandler — satisfies HandoffHandlerProtocol
# ---------------------------------------------------------------------------

class FakeHandoffHandler:
    """Records every handoff payload instead of sending. Optionally raises."""

    def __init__(self, raise_on_send: Exception | None = None) -> None:
        self.sent: list[HandoffPayload] = []
        self.raise_on_send = raise_on_send

    def send(self, payload: HandoffPayload) -> None:
        self.sent.append(payload)
        if self.raise_on_send is not None:
            raise self.raise_on_send


# ---------------------------------------------------------------------------
# Protocol conformance — tests can assert via isinstance(...)
# ---------------------------------------------------------------------------

# Re-export so the import at top of file is not flagged as unused.
__all__ = [
    "ClaudeClientProtocol",
    "ClaudeScripter",
    "EmbedderProtocol",
    "FakeClaudeClient",
    "FakeEmbedder",
    "FakeHandoffHandler",
    "FakeVectorIndex",
    "HandoffPayload",
    "SAMPLE_PAGES",
    "VectorIndexProtocol",
    "_make_page",
    "make_crawler",
    "script_response",
]
