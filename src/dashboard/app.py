"""Admin dashboard FastAPI router — mounted at /dashboard by the main app."""

from __future__ import annotations

import sqlite3
from functools import lru_cache

from fastapi import APIRouter, Depends

from src.config import get_settings
from src.dashboard import metrics as m
from src.dashboard.auth import verify_dashboard_token
from src.dashboard.db import get_connection

# `dependencies=[...]` applies the auth dependency to every route in this
# router — no individual @Depends needed on each endpoint, and no way to
# accidentally forget it on a new endpoint.
router = APIRouter(tags=["dashboard"], dependencies=[Depends(verify_dashboard_token)])


@lru_cache(maxsize=1)
def _get_conn() -> sqlite3.Connection:
    return get_connection(get_settings().db_path)


def get_db() -> sqlite3.Connection:
    return _get_conn()


@router.get("/top-questions")
def top_questions(limit: int = 20, conn: sqlite3.Connection = Depends(get_db)):
    return m.top_questions(conn, limit=limit)


@router.get("/doc-gaps")
def doc_gaps(limit: int = 20, conn: sqlite3.Connection = Depends(get_db)):
    return m.doc_gaps(conn, limit=limit)


@router.get("/escalation-rate")
def escalation_rate(days: int = 14, conn: sqlite3.Connection = Depends(get_db)):
    return m.escalation_rate_by_day(conn, days=days)


@router.get("/latency")
def latency(conn: sqlite3.Connection = Depends(get_db)):
    return m.avg_latency(conn)
