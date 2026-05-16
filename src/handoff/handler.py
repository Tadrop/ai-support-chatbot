"""Handoff dispatcher — routes to email or Crisp based on HANDOFF_CHANNEL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.config import get_settings
from src.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class HandoffPayload:
    name: str
    email: str
    query: str
    transcript: str


@runtime_checkable
class HandoffHandlerProtocol(Protocol):
    def send(self, payload: HandoffPayload) -> None: ...


def get_handoff_handler() -> HandoffHandlerProtocol:
    s = get_settings()
    if s.handoff_channel == "crisp":
        from src.handoff.crisp_handler import CrispHandler
        return CrispHandler()
    from src.handoff.email_handler import EmailHandler
    return EmailHandler()
