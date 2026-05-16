"""Centralised, validated configuration loaded from environment variables.

Every module imports `settings` from here rather than reading os.environ directly.
This keeps secrets out of code, gives one place to add validation, and makes the
required env vars discoverable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Anthropic / Claude ---
    anthropic_api_key: str = Field(..., min_length=10)
    claude_model: str = "claude-opus-4-7"

    # --- Voyage AI ---
    voyage_api_key: str = Field(..., min_length=5)
    voyage_model: str = "voyage-3"
    voyage_embed_dim: int = 1024

    # --- Pinecone ---
    pinecone_api_key: str = Field(..., min_length=10)
    pinecone_index_name: str = "greenleaf-kb"
    pinecone_cloud: Literal["aws", "gcp", "azure"] = "aws"
    pinecone_region: str = "us-east-1"

    # --- Crawler ---
    crawl_root_url: HttpUrl
    crawl_max_pages: int = 2000
    crawl_concurrency: int = 4
    crawl_request_timeout_s: int = 20
    crawl_user_agent: str = "GreenLeafChatbotCrawler/1.0"

    # --- Chunking ---
    chunk_target_tokens: int = 500
    chunk_overlap_tokens: int = 50

    # --- Retrieval / chat ---
    retrieval_top_k: int = 6
    retrieval_confidence_threshold: float = 0.62
    embed_cache_ttl_seconds: int = 3600

    # --- Handoff ---
    handoff_channel: Literal["email", "crisp"] = "email"
    handoff_email_to: str = "support@greenleaf.example"
    crisp_website_id: str = ""
    crisp_api_identifier: str = ""
    crisp_api_key: str = ""

    # --- Email (SMTP) ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # --- Dashboard / DB ---
    db_path: str = "./greenleaf.db"
    # Required Bearer token for any /dashboard/* request. If empty, the
    # dashboard endpoints refuse with 503 — fail-safe rather than fail-open.
    dashboard_token: str = ""

    # --- Rate limiting (slowapi) ---
    rate_limit_enabled: bool = True
    rate_limit_chat: str = "30/minute"
    rate_limit_session: str = "5/minute"

    # --- CORS (widget origin) ---
    cors_origins: str = "*"

    # --- App ---
    app_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    @field_validator("chunk_overlap_tokens")
    @classmethod
    def _overlap_smaller_than_target(cls, v: int, info) -> int:
        target = info.data.get("chunk_target_tokens", 500)
        if v >= target:
            raise ValueError(
                f"chunk_overlap_tokens ({v}) must be smaller than "
                f"chunk_target_tokens ({target})"
            )
        return v

    @field_validator("retrieval_confidence_threshold")
    @classmethod
    def _threshold_in_unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"retrieval_confidence_threshold must be in [0, 1], got {v}")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process. Raises on missing/invalid env vars."""
    return Settings()  # type: ignore[call-arg]
