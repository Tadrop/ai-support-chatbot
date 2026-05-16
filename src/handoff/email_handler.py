"""Email handoff via SMTP (TLS)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from src.config import get_settings
from src.handoff.handler import HandoffPayload
from src.logging_setup import get_logger

log = get_logger(__name__)

_SUBJECT = "GreenLeaf Chatbot Escalation — customer needs help"

_BODY_TEMPLATE = """\
A customer has been escalated from the GreenLeaf chatbot.

Name:  {name}
Email: {email}

Last question:
  {query}

Full conversation transcript:
{transcript}

---
Sent automatically by the GreenLeaf Customer Service Chatbot.
"""


class EmailHandler:
    """Sends the transcript to the support inbox over SMTP/TLS."""

    def send(self, payload: HandoffPayload) -> None:
        s = get_settings()
        if not s.smtp_user or not s.smtp_password:
            log.warning("handoff.email_skipped", reason="SMTP credentials not configured")
            return

        msg = EmailMessage()
        msg["Subject"] = _SUBJECT
        msg["From"] = s.smtp_from or s.smtp_user
        msg["To"] = s.handoff_email_to
        msg["Reply-To"] = payload.email
        msg.set_content(
            _BODY_TEMPLATE.format(
                name=payload.name,
                email=payload.email,
                query=payload.query,
                transcript=payload.transcript,
            )
        )

        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
            log.info("handoff.email_sent", to=s.handoff_email_to, customer=payload.email)
        except smtplib.SMTPException as e:
            log.error("handoff.email_failed", error=str(e))
            raise
