"""Retrieve top-k chunks from the vector index and compute retrieval confidence.

Retrieval confidence = cosine score of the top-1 result.
If no results are returned or the top score is below the threshold, the caller
should trigger a handoff without even calling Claude.
"""

from __future__ import annotations

from src.config import get_settings
from src.logging_setup import get_logger
from src.protocols import EmbedderProtocol, VectorIndexProtocol
from src.schemas import RetrievedChunk

log = get_logger(__name__)


def retrieve(
    query: str,
    embedder: EmbedderProtocol,
    index: VectorIndexProtocol,
    top_k: int | None = None,
) -> tuple[list[RetrievedChunk], float]:
    """Embed `query` and return (chunks, retrieval_confidence).

    retrieval_confidence is the cosine score of the best matching chunk,
    or 0.0 if no chunks were found.
    """
    s = get_settings()
    k = top_k if top_k is not None else s.retrieval_top_k

    vector = embedder.embed_query(query)
    chunks = index.query(vector, top_k=k)

    confidence = chunks[0].score if chunks else 0.0
    log.info(
        "retrieval.done",
        query_len=len(query),
        chunks_returned=len(chunks),
        top_score=round(confidence, 4),
    )
    return chunks, confidence


def is_confident(confidence: float) -> bool:
    """True if retrieval confidence meets the configured threshold."""
    return confidence >= get_settings().retrieval_confidence_threshold
