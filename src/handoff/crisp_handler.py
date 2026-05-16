"""Crisp handoff — creates a new conversation via the Crisp REST API.

Crisp API docs: https://docs.crisp.chat/references/rest-api/v1/

The Crisp SDK is not used here to avoid an additional dependency; the REST
calls are made with httpx so they are straightforward to stub in tests.
"""

from __future__ import annotations

import httpx

from src.config import get_settings
from src.handoff.handler import HandoffPayload
from src.logging_setup import get_logger

log = get_logger(__name__)

_CRISP_API_BASE = "https://api.crisp.chat/v1"


class CrispHandler:
    """Creates a Crisp conversation pre-populated with the escalation transcript."""

    def send(self, payload: HandoffPayload) -> None:
        s = get_settings()
        if not s.crisp_website_id or not s.crisp_api_identifier or not s.crisp_api_key:
            log.warning("handoff.crisp_skipped", reason="Crisp credentials not configured")
            return

        auth = (s.crisp_api_identifier, s.crisp_api_key)
        headers = {"X-Crisp-Tier": "plugin", "Content-Type": "application/json"}

        # Step 1: create or locate conversation for this email.
        try:
            resp = httpx.post(
                f"{_CRISP_API_BASE}/website/{s.crisp_website_id}/conversation",
                auth=auth,
                headers=headers,
                json={},
                timeout=10,
            )
            resp.raise_for_status()
            session_id = resp.json()["data"]["session_id"]
        except httpx.HTTPError as e:
            log.error("handoff.crisp_create_failed", error=str(e))
            raise

        # Step 2: set visitor info (name + email).
        try:
            httpx.patch(
                f"{_CRISP_API_BASE}/website/{s.crisp_website_id}/conversation/{session_id}/meta",
                auth=auth,
                headers=headers,
                json={"nickname": payload.name, "email": payload.email},
                timeout=10,
            ).raise_for_status()
        except httpx.HTTPError as e:
            log.warning("handoff.crisp_meta_failed", error=str(e))

        # Step 3: post the transcript as the first message.
        note = (
            f"[Chatbot escalation]\n\n"
            f"Customer: {payload.name} <{payload.email}>\n"
            f"Last question: {payload.query}\n\n"
            f"Transcript:\n{payload.transcript}"
        )
        try:
            httpx.post(
                f"{_CRISP_API_BASE}/website/{s.crisp_website_id}/conversation/{session_id}/message",
                auth=auth,
                headers=headers,
                json={"type": "note", "from": "operator", "origin": "chat", "content": note},
                timeout=10,
            ).raise_for_status()
            log.info("handoff.crisp_sent", session_id=session_id, customer=payload.email)
        except httpx.HTTPError as e:
            log.error("handoff.crisp_message_failed", error=str(e))
            raise
