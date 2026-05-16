"""HTTP-level tests for the dashboard router — verifies Bearer-token auth.

The metrics SQL is already covered by test_dashboard.py; here we only assert
the auth dependency behaves correctly under realistic header conditions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.chat.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------

def test_no_authorization_header_returns_401(client):
    r = client.get("/dashboard/top-questions")
    assert r.status_code == 401
    # The standards-compliant challenge header.
    assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_wrong_scheme_returns_401(client):
    r = client.get(
        "/dashboard/top-questions",
        headers={"Authorization": "Basic dGVzdDp0ZXN0"},
    )
    assert r.status_code == 401


def test_wrong_token_returns_401(client):
    r = client.get(
        "/dashboard/top-questions",
        headers={"Authorization": "Bearer not-the-real-token"},
    )
    assert r.status_code == 401


def test_correct_token_returns_200(client):
    """conftest sets DASHBOARD_TOKEN=test-dashboard-token."""
    r = client.get(
        "/dashboard/top-questions",
        headers={"Authorization": "Bearer test-dashboard-token"},
    )
    assert r.status_code == 200
    # Empty list is fine — no turns have been logged in this isolated test process.
    assert isinstance(r.json(), list)


def test_auth_applies_to_every_dashboard_endpoint(client):
    """The auth dependency is wired on the router itself, so it must cover
    every current AND future endpoint."""
    for path in [
        "/dashboard/top-questions",
        "/dashboard/doc-gaps",
        "/dashboard/escalation-rate",
        "/dashboard/latency",
    ]:
        r = client.get(path)
        assert r.status_code == 401, f"{path} should require auth"


# ---------------------------------------------------------------------------
# Fail-safe: empty DASHBOARD_TOKEN → 503
# ---------------------------------------------------------------------------

def test_empty_token_returns_503(monkeypatch, client):
    monkeypatch.setenv("DASHBOARD_TOKEN", "")
    from src.config import get_settings
    get_settings.cache_clear()

    r = client.get(
        "/dashboard/top-questions",
        headers={"Authorization": "Bearer anything"},
    )
    assert r.status_code == 503

    # Restore for downstream tests.
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-dashboard-token")
    get_settings.cache_clear()
