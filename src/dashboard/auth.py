"""Bearer-token authentication for /dashboard/* endpoints.

Fail-safe: if DASHBOARD_TOKEN is empty, every request returns 503 — we never
serve dashboard data without a configured token. Uses `secrets.compare_digest`
to prevent timing-attack-based token enumeration.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

from src.config import get_settings


def verify_dashboard_token(request: Request) -> None:
    """FastAPI dependency. Raises 401/503 on failure; returns None on success."""
    expected = get_settings().dashboard_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard authentication is not configured. Set DASHBOARD_TOKEN.",
        )

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Use: 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = header.split(" ", 1)[1].strip()
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
