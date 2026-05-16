"""Turn logger — writes one row per chat turn to SQLite."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache

from src.config import get_settings
from src.dashboard.db import get_connection
from src.logging_setup import get_logger
from src.schemas import AnswerFlag

log = get_logger(__name__)


class TurnLogger:
    def __init__(self, db_path: str | None = None) -> None:
        path = db_path if db_path is not None else get_settings().db_path
        self._conn: sqlite3.Connection = get_connection(path)

    def log(
        self,
        *,
        session_id: str,
        customer_name: str,
        customer_email: str,
        query: str,
        answer: str | None,
        cited_urls: list[str],
        retrieval_confidence: float,
        llm_confidence: float | None,
        answer_flag: AnswerFlag,
        latency_ms: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO turns
                  (session_id, customer_name, customer_email, query, answer,
                   cited_urls, retrieval_confidence, llm_confidence,
                   answer_flag, latency_ms, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    customer_name,
                    customer_email,
                    query,
                    answer,
                    json.dumps(cited_urls),
                    retrieval_confidence,
                    llm_confidence,
                    answer_flag,
                    latency_ms,
                    now,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            log.error("turn_logger.write_failed", error=str(e))
