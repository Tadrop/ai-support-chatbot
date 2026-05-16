"""Handoff handler tests — fast, no SMTP / Crisp network calls."""

from __future__ import annotations

from unittest.mock import patch

from src.handoff.crisp_handler import CrispHandler
from src.handoff.email_handler import EmailHandler
from src.handoff.handler import HandoffPayload, get_handoff_handler


def _payload() -> HandoffPayload:
    return HandoffPayload(
        name="Alice",
        email="alice@example.com",
        query="Can I return a dead orchid?",
        transcript="USER: Can I return a dead orchid?",
    )


# ---------------------------------------------------------------------------
# Channel routing
# ---------------------------------------------------------------------------

def test_get_handoff_handler_returns_email_by_default():
    # conftest sets HANDOFF_CHANNEL absent, so default ("email") applies.
    # Clear the lru_cache so settings reload reflects the env we control.
    from src.config import get_settings
    get_settings.cache_clear()
    handler = get_handoff_handler()
    assert isinstance(handler, EmailHandler)


def test_get_handoff_handler_returns_crisp_when_configured(monkeypatch):
    monkeypatch.setenv("HANDOFF_CHANNEL", "crisp")
    from src.config import get_settings
    get_settings.cache_clear()
    handler = get_handoff_handler()
    assert isinstance(handler, CrispHandler)
    # Restore default for downstream tests.
    monkeypatch.delenv("HANDOFF_CHANNEL", raising=False)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# EmailHandler — credentials gating + SMTP send
# ---------------------------------------------------------------------------

def test_email_handler_skips_when_smtp_user_missing(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    from src.config import get_settings
    get_settings.cache_clear()

    handler = EmailHandler()
    # Should NOT raise and should NOT touch smtplib.
    with patch("smtplib.SMTP") as smtp_mock:
        handler.send(_payload())
        smtp_mock.assert_not_called()


def test_email_handler_sends_via_smtp_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "bot@example.com")
    monkeypatch.setenv("HANDOFF_EMAIL_TO", "ops@example.com")
    from src.config import get_settings
    get_settings.cache_clear()

    handler = EmailHandler()
    with patch("smtplib.SMTP") as smtp_mock:
        instance = smtp_mock.return_value.__enter__.return_value
        handler.send(_payload())
        instance.login.assert_called_once_with("bot@example.com", "secret")
        instance.send_message.assert_called_once()
        # The message should be addressed to the support inbox.
        sent_msg = instance.send_message.call_args.args[0]
        assert sent_msg["To"] == "ops@example.com"
        assert sent_msg["Reply-To"] == "alice@example.com"

    # Restore env for other tests.
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("HANDOFF_EMAIL_TO", raising=False)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# CrispHandler — credentials gating
# ---------------------------------------------------------------------------

def test_crisp_handler_skips_when_credentials_missing(monkeypatch):
    monkeypatch.setenv("CRISP_WEBSITE_ID", "")
    monkeypatch.setenv("CRISP_API_IDENTIFIER", "")
    monkeypatch.setenv("CRISP_API_KEY", "")
    from src.config import get_settings
    get_settings.cache_clear()

    handler = CrispHandler()
    with patch("httpx.post") as post_mock:
        handler.send(_payload())
        post_mock.assert_not_called()
