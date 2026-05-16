"""Voyage AI embedder.

Two entry points:
  - `embed_chunks(chunks)`  — embeds Chunk records, returns EmbeddedChunk list.
                              Uses input_type="document" (the model is asymmetric).
  - `embed_query(text)`     — embeds a user query.
                              Uses input_type="query".

Batches respect Voyage's 128-input-per-call limit and retry transient failures
with exponential backoff.
"""

from __future__ import annotations

import voyageai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.logging_setup import get_logger
from src.schemas import Chunk, EmbeddedChunk

log = get_logger(__name__)

_BATCH = 128


class VoyageEmbedder:
    def __init__(self) -> None:
        s = get_settings()
        self._client = voyageai.Client(api_key=s.voyage_api_key)
        self._model = s.voyage_model
        self._dim = s.voyage_embed_dim

    @retry(
        retry=retry_if_exception_type(Exception),  # voyageai exposes plain Exception subclasses
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _embed_batch(self, texts: list[str], input_type: str) -> list[list[float]]:
        res = self._client.embed(texts=texts, model=self._model, input_type=input_type)
        vectors = res.embeddings
        # Defensive: every model has a fixed dim. If Voyage returns the wrong shape
        # we want to fail loudly here rather than at Pinecone upsert time.
        for v in vectors:
            if len(v) != self._dim:
                raise ValueError(
                    f"Voyage returned vector of dim {len(v)}, expected {self._dim} "
                    f"(check VOYAGE_MODEL / VOYAGE_EMBED_DIM in .env)"
                )
        return vectors

    def embed_chunks(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        if not chunks:
            return []
        out: list[EmbeddedChunk] = []
        for start in range(0, len(chunks), _BATCH):
            batch = chunks[start : start + _BATCH]
            vectors = self._embed_batch([c.text for c in batch], input_type="document")
            for c, v in zip(batch, vectors):
                out.append(EmbeddedChunk(chunk=c, vector=v))
        log.info("voyage.embed_chunks", count=len(out), model=self._model)
        return out

    def embed_query(self, text: str) -> list[float]:
        v = self._embed_batch([text], input_type="query")[0]
        return v
