"""Shared Pydantic models used across modules."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

ContentType = Literal["faq", "policy", "product", "blog", "other"]
AnswerFlag = Literal["in_kb", "fallback", "handoff"]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class CrawledPage(BaseModel):
    url: HttpUrl
    title: str = ""
    content_type: ContentType = "other"
    text: str
    last_modified: datetime | None = None
    fetched_at: datetime


class Chunk(BaseModel):
    id: str
    url: HttpUrl
    title: str
    content_type: ContentType
    chunk_index: int
    text: str
    content_hash: str
    last_modified: datetime | None = None


class EmbeddedChunk(BaseModel):
    chunk: Chunk
    vector: list[float]


class RetrievedChunk(BaseModel):
    id: str
    score: float
    url: str
    title: str
    content_type: ContentType
    text: str
    chunk_index: int = 0


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------

GDPR_NOTICE = (
    "Before we begin: GreenLeaf Garden Supplies will store your name and email "
    "address to follow up on your enquiry and improve our service. "
    "Your conversation may be reviewed by our team. "
    "See our Privacy Policy for details. By continuing you consent to this use."
)


class SessionStartRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    gdpr_consent: bool


class SessionStartResponse(BaseModel):
    session_id: str
    gdpr_notice: str


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    """Schema Claude must conform to. The cited-URL self-check enforces grounding."""
    answer: str = Field(..., min_length=1)
    cited_urls: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    in_scope: bool


class ChatApiResponse(BaseModel):
    """What the /chat endpoint returns to the widget."""
    answer: str
    cited_urls: list[str]
    confidence: float
    handoff: bool
    answer_flag: AnswerFlag
    session_id: str


# ---------------------------------------------------------------------------
# Turn logging
# ---------------------------------------------------------------------------

class TurnLog(BaseModel):
    session_id: str
    customer_name: str
    customer_email: str
    query: str
    answer: str | None
    cited_urls: list[str]
    retrieval_confidence: float
    llm_confidence: float | None
    answer_flag: AnswerFlag
    latency_ms: int
    created_at: datetime
