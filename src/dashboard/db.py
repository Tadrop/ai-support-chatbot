"""SQLite connection and schema management for the turn log.

WAL mode is enabled for concurrent reads from the dashboard while the chat
backend is writing. The DB path comes from settings (DB_PATH) so tests can
pass `:memory:` to get an isolated in-memory database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


_DDL = """\
CREATE TABLE IF NOT EXISTS turns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           TEXT    NOT NULL,
    customer_name        TEXT    NOT NULL,
    customer_email       TEXT    NOT NULL,
    query                TEXT    NOT NULL,
    answer               TEXT,
    cited_urls           TEXT    NOT NULL DEFAULT '[]',
    retrieval_confidence REAL    NOT NULL,
    llm_confidence       REAL,
    answer_flag          TEXT    NOT NULL,
    latency_ms           INTEGER NOT NULL,
    created_at           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_created  ON turns (created_at);
CREATE INDEX IF NOT EXISTS idx_turns_flag     ON turns (answer_flag);
CREATE INDEX IF NOT EXISTS idx_turns_session  ON turns (session_id);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return an open connection with WAL mode and row_factory set."""
    is_memory = db_path == ":memory:"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if not is_memory:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    conn.commit()
    return conn
