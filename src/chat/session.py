"""In-memory session store.

Holds name, email, gdpr consent timestamp, and conversation history for the
duration of a chat session.  Sessions expire after SESSION_TTL_MINUTES of
inactivity — a background task (run by the FastAPI lifespan) cleans them up.

For production at scale, replace _STORE with a Redis-backed equivalent.
The public API (get_session / create_session / append_turn) is the same.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.logging_setup import get_logger

log = get_logger(__name__)

SESSION_TTL_MINUTES = 60

@dataclass
class SessionEntry:
    session_id: str
    name: str
    email: str
    gdpr_consent_at: datetime
    history: list[dict] = field(default_factory=list)   # [{role, content}, ...]
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Module-level store — one dict per process.
_STORE: dict[str, SessionEntry] = {}


def create_session(name: str, email: str) -> SessionEntry:
    sid = str(uuid.uuid4())
    entry = SessionEntry(
        session_id=sid,
        name=name,
        email=email,
        gdpr_consent_at=datetime.now(timezone.utc),
    )
    _STORE[sid] = entry
    log.info("session.created", session_id=sid)
    return entry


def get_session(session_id: str) -> SessionEntry | None:
    entry = _STORE.get(session_id)
    if entry is not None:
        entry.last_active = datetime.now(timezone.utc)
    return entry


def append_turn(session_id: str, role: str, content: str) -> None:
    entry = _STORE.get(session_id)
    if entry:
        entry.history.append({"role": role, "content": content})
        entry.last_active = datetime.now(timezone.utc)


def _prune_expired() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=SESSION_TTL_MINUTES)
    expired = [sid for sid, e in _STORE.items() if e.last_active < cutoff]
    for sid in expired:
        del _STORE[sid]
    if expired:
        log.info("session.pruned", count=len(expired))
    return len(expired)


async def session_cleanup_loop() -> None:
    """Periodically remove expired sessions. Run as a background task."""
    while True:
        await asyncio.sleep(300)  # prune every 5 minutes
        _prune_expired()
