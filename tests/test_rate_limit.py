"""Rate-limiter wiring tests.

The default test environment has RATE_LIMIT_ENABLED=false (set in conftest),
so the test suite as a whole isn't accidentally throttled. This file covers:

  1. With rate-limiting disabled, no 429 is ever returned.
  2. The slowapi limiter is wired onto the app (`app.state.limiter` exists and
     has the expected configuration).

We don't exercise the actual throttling behaviour against the live FastAPI
app because slowapi's in-memory storage is process-wide and would leak
between tests — that's a separate concern best covered by an end-to-end
load test rather than a unit test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.chat.app import app, get_claude, get_embedder, get_handoff, get_index, get_turn_logger
from src.dashboard.logger import TurnLogger
from src.schemas import EmbeddedChunk
from tests.fakes import (
    FakeClaudeClient,
    FakeEmbedder,
    FakeHandoffHandler,
    FakeVectorIndex,
    SAMPLE_PAGES,
)


@pytest.fixture
def client(tmp_path):
    """Wire fakes so /chat actually returns 200 instead of crashing on real deps."""
    embedder = FakeEmbedder()
    index = FakeVectorIndex()

    # Pre-populate so retrieval finds something.
    from src.ingest.chunker import chunk_page
    from src.ingest.extractor import extract_page

    all_chunks = [
        c for page in SAMPLE_PAGES
        for c in chunk_page(extract_page(page), target_tokens=200, overlap_tokens=20)
    ]
    index.upsert([EmbeddedChunk(chunk=c, vector=embedder.embed_query(c.text)) for c in all_chunks])

    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_index] = lambda: index
    app.dependency_overrides[get_claude] = lambda: FakeClaudeClient()
    app.dependency_overrides[get_handoff] = lambda: FakeHandoffHandler()
    app.dependency_overrides[get_turn_logger] = lambda: TurnLogger(db_path=str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_limiter_is_attached_to_app():
    """slowapi wires its limiter onto app.state at app-creation time."""
    assert hasattr(app.state, "limiter")
    # In tests it's disabled — that's deliberate (RATE_LIMIT_ENABLED=false in conftest).
    assert app.state.limiter.enabled is False


def test_no_throttling_when_disabled(client):
    """Hammer the session endpoint well past the configured per-IP limit;
    every request must succeed because the limiter is disabled in tests."""
    for _ in range(20):
        r = client.post(
            "/session/start",
            json={"name": "Alice", "email": "alice@example.com", "gdpr_consent": True},
        )
        assert r.status_code == 200, f"Request unexpectedly throttled: {r.status_code}"
