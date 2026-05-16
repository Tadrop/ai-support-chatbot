"""Pytest fixtures + env stubbing.

Sets dummy env vars BEFORE importing src.config so any test that touches
get_settings() doesn't blow up on missing keys. Tests that hit real services
should be marked `@pytest.mark.integration` and skipped by default in CI.
"""

from __future__ import annotations

import os

# Set fakes BEFORE pydantic-settings tries to read them.
_DEFAULTS = {
    "ANTHROPIC_API_KEY": "sk-ant-test-key-1234567890",
    "VOYAGE_API_KEY": "pa-test-key",
    "PINECONE_API_KEY": "pcsk-test-key-1234567890",
    "CRAWL_ROOT_URL": "https://example.test",
    "APP_ENV": "dev",
    "LOG_LEVEL": "WARNING",
    # Auth + rate limiting: configure once for the whole test session.
    "DASHBOARD_TOKEN": "test-dashboard-token",
    "RATE_LIMIT_ENABLED": "false",
}
for k, v in _DEFAULTS.items():
    os.environ.setdefault(k, v)

# Make sure src is importable when running pytest from project root.
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
