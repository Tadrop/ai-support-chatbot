"""Dashboard metrics tests — use a real on-disk SQLite DB in tmp_path."""

from __future__ import annotations

import pytest

from src.dashboard.db import get_connection
from src.dashboard.logger import TurnLogger
from src.dashboard import metrics as m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Fresh on-disk SQLite DB per test (in-memory has no shared state across
    connections, which would break the read-from-dashboard / write-from-logger
    split)."""
    return str(tmp_path / "test.db")


@pytest.fixture
def logger(db_path):
    return TurnLogger(db_path=db_path)


@pytest.fixture
def conn(db_path):
    return get_connection(db_path)


def _log(logger: TurnLogger, **overrides):
    """Insert a turn with sensible defaults; override per-test."""
    base = {
        "session_id": "s-1",
        "customer_name": "Alice",
        "customer_email": "alice@example.com",
        "query": "How do I return a plant?",
        "answer": "Within 30 days.",
        "cited_urls": ["https://example.test/returns"],
        "retrieval_confidence": 0.85,
        "llm_confidence": 0.9,
        "answer_flag": "in_kb",
        "latency_ms": 1200,
    }
    base.update(overrides)
    logger.log(**base)


# ---------------------------------------------------------------------------
# top_questions
# ---------------------------------------------------------------------------

def test_top_questions_counts_by_exact_query(logger, conn):
    for _ in range(3):
        _log(logger, query="shipping?")
    for _ in range(2):
        _log(logger, query="returns?")
    _log(logger, query="one-off")

    out = m.top_questions(conn)
    counts = {row["query"]: row["count"] for row in out}
    assert counts["shipping?"] == 3
    assert counts["returns?"] == 2
    assert counts["one-off"] == 1


def test_top_questions_respects_limit(logger, conn):
    for i in range(5):
        _log(logger, query=f"q{i}")
    out = m.top_questions(conn, limit=2)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# doc_gaps
# ---------------------------------------------------------------------------

def test_doc_gaps_returns_low_confidence_queries(logger, conn):
    _log(logger, query="high-conf", retrieval_confidence=0.9)
    _log(logger, query="low-conf",  retrieval_confidence=0.2)

    gaps = m.doc_gaps(conn)
    queries = [g["query"] for g in gaps]
    assert "low-conf" in queries
    assert "high-conf" not in queries


# ---------------------------------------------------------------------------
# escalation_rate_by_day
# ---------------------------------------------------------------------------

def test_escalation_rate_counts_handoffs_per_day(logger, conn):
    _log(logger, answer_flag="in_kb")
    _log(logger, answer_flag="in_kb")
    _log(logger, answer_flag="handoff")

    days = m.escalation_rate_by_day(conn, days=7)
    assert len(days) == 1
    day = days[0]
    assert day["total"] == 3
    assert day["handoffs"] == 1
    assert day["escalation_rate"] == round(1 / 3, 3)


# ---------------------------------------------------------------------------
# avg_latency
# ---------------------------------------------------------------------------

def test_avg_latency_computes_avg_and_p95(logger, conn):
    for latency in [100, 200, 300, 400, 500, 600, 700, 800, 900, 5000]:
        _log(logger, latency_ms=latency)
    out = m.avg_latency(conn)
    assert out["avg_ms"] is not None
    assert out["avg_ms"] > 0
    # p95 should pull from the high end (the 5000 ms outlier).
    assert out["p95_ms"] is not None
    assert out["p95_ms"] >= 900


def test_avg_latency_empty_table_returns_none(conn):
    out = m.avg_latency(conn)
    assert out["avg_ms"] is None
    assert out["p95_ms"] is None
