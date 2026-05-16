"""End-to-end /chat endpoint tests with all dependencies injected as fakes.

Uses fastapi.testclient.TestClient so the full FastAPI stack is exercised —
routing, validation, dependency injection, JSON serialisation — without any
network calls.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.chat.app import (
    app,
    get_claude,
    get_embedder,
    get_handoff,
    get_index,
    get_turn_logger,
)
from src.dashboard.logger import TurnLogger
from tests.fakes import (
    FakeClaudeClient,
    FakeEmbedder,
    FakeHandoffHandler,
    FakeVectorIndex,
    SAMPLE_PAGES,
    script_response,
)


# ---------------------------------------------------------------------------
# Test setup — wire fakes into the live FastAPI app via dependency_overrides
# ---------------------------------------------------------------------------

@pytest.fixture
def fakes(tmp_path):
    """Fresh fakes for every test. Returns a dict for easy access in assertions."""
    embedder = FakeEmbedder()
    index = FakeVectorIndex()
    claude = FakeClaudeClient()
    handoff = FakeHandoffHandler()
    turn_logger = TurnLogger(db_path=str(tmp_path / "test.db"))

    # Pre-populate the index with the sample corpus so retrieval has something to find.
    from src.ingest.chunker import chunk_page
    from src.ingest.extractor import extract_page
    from src.schemas import EmbeddedChunk

    all_chunks = [
        chunk
        for page in SAMPLE_PAGES
        for chunk in chunk_page(extract_page(page), target_tokens=200, overlap_tokens=20)
    ]
    embedded = [
        EmbeddedChunk(chunk=c, vector=embedder.embed_query(c.text))
        for c in all_chunks
    ]
    index.upsert(embedded)
    return {
        "embedder": embedder,
        "index": index,
        "claude": claude,
        "handoff": handoff,
        "turn_logger": turn_logger,
    }


@pytest.fixture
def client(fakes):
    """TestClient with all fakes wired via dependency_overrides."""
    app.dependency_overrides[get_embedder] = lambda: fakes["embedder"]
    app.dependency_overrides[get_index] = lambda: fakes["index"]
    app.dependency_overrides[get_claude] = lambda: fakes["claude"]
    app.dependency_overrides[get_handoff] = lambda: fakes["handoff"]
    app.dependency_overrides[get_turn_logger] = lambda: fakes["turn_logger"]
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _start_session(client: TestClient) -> str:
    """Create a session and return its ID."""
    r = client.post(
        "/session/start",
        json={"name": "Alice", "email": "alice@example.com", "gdpr_consent": True},
    )
    assert r.status_code == 200
    return r.json()["session_id"]


# ---------------------------------------------------------------------------
# /health and /session/start
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_session_start_requires_gdpr_consent(client):
    r = client.post(
        "/session/start",
        json={"name": "Alice", "email": "alice@example.com", "gdpr_consent": False},
    )
    assert r.status_code == 400


def test_session_start_returns_session_id_and_notice(client):
    r = client.post(
        "/session/start",
        json={"name": "Alice", "email": "alice@example.com", "gdpr_consent": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"]
    assert "GreenLeaf" in body["gdpr_notice"]


def test_session_start_rejects_invalid_email(client):
    r = client.post(
        "/session/start",
        json={"name": "Alice", "email": "not-an-email", "gdpr_consent": True},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /chat — successful answer
# ---------------------------------------------------------------------------

def test_chat_returns_grounded_answer(client, fakes):
    session_id = _start_session(client)
    # Default FakeClaudeClient cites the top chunk's URL and sets in_scope=True.
    r = client.post("/chat", json={"session_id": session_id, "message": "How long does shipping take?"})
    assert r.status_code == 200
    body = r.json()
    assert body["handoff"] is False
    assert body["answer_flag"] == "in_kb"
    assert len(body["cited_urls"]) >= 1
    # Citation must be one of the URLs from SAMPLE_PAGES.
    for url in body["cited_urls"]:
        assert any(url.startswith(p) for p in [
            "https://example.test/shipping",
            "https://example.test/returns",
            "https://example.test/products",
            "https://example.test/faq",
        ])
    assert fakes["claude"].calls, "Claude should have been called"
    assert not fakes["handoff"].sent, "Handoff should NOT fire for grounded answer"


# ---------------------------------------------------------------------------
# /chat — handoff paths
# ---------------------------------------------------------------------------

def test_chat_handoffs_when_claude_says_out_of_scope(client, fakes):
    fakes["claude"]._scripter = script_response(
        answer="I don't know.", cited_urls=[], confidence=0.2, in_scope=False
    )
    session_id = _start_session(client)
    r = client.post("/chat", json={"session_id": session_id, "message": "What's the meaning of life?"})
    assert r.status_code == 200
    body = r.json()
    assert body["handoff"] is True
    assert body["answer_flag"] == "handoff"
    assert fakes["handoff"].sent, "Handoff payload should have been dispatched"


def test_chat_handoffs_when_claude_cites_hallucinated_url(client, fakes):
    """The cited-URL self-check must reject answers that cite URLs not in context."""
    fakes["claude"]._scripter = script_response(
        answer="Per our docs you get a 50% discount.",
        cited_urls=["https://made-up.test/coupons"],
        confidence=0.95,
        in_scope=True,
    )
    session_id = _start_session(client)
    r = client.post("/chat", json={"session_id": session_id, "message": "discount?"})
    body = r.json()
    assert body["handoff"] is True
    assert body["answer_flag"] == "handoff"
    # The fabricated answer must NOT have been shipped.
    assert "50%" not in body["answer"]


def test_chat_handoffs_when_in_scope_true_but_no_citations(client, fakes):
    """CLAUDE.md: 'never let an answer ship without citation'."""
    fakes["claude"]._scripter = script_response(
        answer="Yes we offer free shipping.",
        cited_urls=[],
        confidence=0.9,
        in_scope=True,
    )
    session_id = _start_session(client)
    r = client.post("/chat", json={"session_id": session_id, "message": "free shipping?"})
    body = r.json()
    assert body["handoff"] is True


def test_chat_handoffs_when_claude_raises(client, fakes):
    fakes["claude"].raise_next = RuntimeError("API down")
    session_id = _start_session(client)
    r = client.post("/chat", json={"session_id": session_id, "message": "hi"})
    body = r.json()
    assert body["handoff"] is True
    assert fakes["handoff"].sent


def test_chat_handoffs_when_retrieval_confidence_low(client, fakes):
    """Empty index → top score is 0.0 → below threshold → handoff without Claude call."""
    fakes["index"]._store.clear()
    session_id = _start_session(client)
    r = client.post("/chat", json={"session_id": session_id, "message": "anything"})
    body = r.json()
    assert body["handoff"] is True
    assert not fakes["claude"].calls, "Claude must NOT be called when retrieval is unconfident"


# ---------------------------------------------------------------------------
# /chat — error cases
# ---------------------------------------------------------------------------

def test_chat_unknown_session_returns_404(client):
    r = client.post("/chat", json={"session_id": "does-not-exist", "message": "hi"})
    assert r.status_code == 404


def test_chat_rejects_empty_message(client):
    session_id = _start_session(client)
    r = client.post("/chat", json={"session_id": session_id, "message": ""})
    assert r.status_code == 422
