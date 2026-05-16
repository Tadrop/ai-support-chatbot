"""Dashboard metrics — SQL aggregations over the turn log."""

from __future__ import annotations

import sqlite3


def top_questions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Most frequent customer queries (exact match grouping)."""
    rows = conn.execute(
        """
        SELECT query, COUNT(*) AS count
        FROM   turns
        GROUP  BY query
        ORDER  BY count DESC
        LIMIT  ?
        """,
        (limit,),
    ).fetchall()
    return [{"query": r["query"], "count": r["count"]} for r in rows]


def doc_gaps(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Queries where retrieval confidence was below threshold — likely missing docs."""
    from src.config import get_settings
    threshold = get_settings().retrieval_confidence_threshold
    rows = conn.execute(
        """
        SELECT query, retrieval_confidence, created_at
        FROM   turns
        WHERE  retrieval_confidence < ?
        ORDER  BY created_at DESC
        LIMIT  ?
        """,
        (threshold, limit),
    ).fetchall()
    return [
        {
            "query": r["query"],
            "retrieval_confidence": r["retrieval_confidence"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def escalation_rate_by_day(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    """Handoff count and total turns per day for the last N days."""
    rows = conn.execute(
        """
        SELECT
            DATE(created_at) AS day,
            COUNT(*) AS total,
            SUM(CASE WHEN answer_flag = 'handoff' THEN 1 ELSE 0 END) AS handoffs
        FROM   turns
        WHERE  created_at >= DATE('now', ?)
        GROUP  BY day
        ORDER  BY day DESC
        """,
        (f"-{days} days",),
    ).fetchall()
    return [
        {
            "day": r["day"],
            "total": r["total"],
            "handoffs": r["handoffs"],
            "escalation_rate": round(r["handoffs"] / r["total"], 3) if r["total"] else 0,
        }
        for r in rows
    ]


def avg_latency(conn: sqlite3.Connection) -> dict:
    """Average and p95 response latency across all turns."""
    row = conn.execute(
        """
        SELECT
            ROUND(AVG(latency_ms), 1)                                  AS avg_ms,
            ROUND(AVG(CASE WHEN pct >= 0.95 THEN latency_ms END), 1)  AS p95_ms
        FROM (
            SELECT latency_ms,
                   PERCENT_RANK() OVER (ORDER BY latency_ms) AS pct
            FROM   turns
        )
        """
    ).fetchone()
    return {
        "avg_ms": row["avg_ms"] if row else None,
        "p95_ms": row["p95_ms"] if row else None,
    }
