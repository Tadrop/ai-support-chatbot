"""Thin, well-typed wrapper around the Pinecone SDK.

Responsibilities:
  - Lazy-create the index on first use (idempotent).
  - Upsert embedded chunks in batches with retry/backoff.
  - Query top-k by embedding vector.
  - Delete by ID list (used by the diff detector to purge orphaned chunks).
  - Fetch existing IDs + content hashes for diff detection.

Anything Pinecone-specific lives here. The rest of the codebase only sees
schemas.Chunk / EmbeddedChunk / RetrievedChunk.
"""

from __future__ import annotations

from typing import Iterable

from pinecone import Pinecone, ServerlessSpec
from pinecone.exceptions import PineconeApiException
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.logging_setup import get_logger
from src.schemas import EmbeddedChunk, RetrievedChunk

log = get_logger(__name__)

_UPSERT_BATCH = 100
_FETCH_BATCH = 100


class PineconeIndex:
    """Wrapper holding a single Pinecone Index handle for the configured index name."""

    def __init__(self) -> None:
        s = get_settings()
        self._client = Pinecone(api_key=s.pinecone_api_key)
        self._name = s.pinecone_index_name
        self._dim = s.voyage_embed_dim
        self._cloud = s.pinecone_cloud
        self._region = s.pinecone_region
        self._index = None  # set on first use

    def _ensure(self):
        if self._index is not None:
            return self._index
        existing = {i["name"] for i in self._client.list_indexes()}
        if self._name not in existing:
            log.info("pinecone.create_index", name=self._name, dim=self._dim)
            self._client.create_index(
                name=self._name,
                dimension=self._dim,
                metric="cosine",
                spec=ServerlessSpec(cloud=self._cloud, region=self._region),
            )
        self._index = self._client.Index(self._name)
        return self._index

    # --- writes ---

    @retry(
        retry=retry_if_exception_type(PineconeApiException),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def upsert(self, embedded: list[EmbeddedChunk]) -> int:
        """Upsert in batches; returns number of vectors written."""
        if not embedded:
            return 0
        idx = self._ensure()
        written = 0
        for start in range(0, len(embedded), _UPSERT_BATCH):
            batch = embedded[start : start + _UPSERT_BATCH]
            vectors = [
                {
                    "id": ec.chunk.id,
                    "values": ec.vector,
                    "metadata": {
                        "url": str(ec.chunk.url),
                        "title": ec.chunk.title,
                        "content_type": ec.chunk.content_type,
                        "chunk_index": ec.chunk.chunk_index,
                        "text": ec.chunk.text,
                        "content_hash": ec.chunk.content_hash,
                    },
                }
                for ec in batch
            ]
            idx.upsert(vectors=vectors)
            written += len(batch)
        log.info("pinecone.upsert", count=written)
        return written

    @retry(
        retry=retry_if_exception_type(PineconeApiException),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def delete(self, ids: Iterable[str]) -> int:
        ids_list = list(ids)
        if not ids_list:
            return 0
        idx = self._ensure()
        # Pinecone allows up to 1000 ids per delete call.
        deleted = 0
        for start in range(0, len(ids_list), 1000):
            batch = ids_list[start : start + 1000]
            idx.delete(ids=batch)
            deleted += len(batch)
        log.info("pinecone.delete", count=deleted)
        return deleted

    # --- reads ---

    @retry(
        retry=retry_if_exception_type(PineconeApiException),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def query(self, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        idx = self._ensure()
        res = idx.query(vector=vector, top_k=top_k, include_metadata=True)
        out: list[RetrievedChunk] = []
        for match in res.get("matches", []):
            md = match.get("metadata") or {}
            out.append(
                RetrievedChunk(
                    id=match["id"],
                    score=float(match["score"]),
                    url=md.get("url", ""),
                    title=md.get("title", ""),
                    content_type=md.get("content_type", "other"),
                    text=md.get("text", ""),
                    chunk_index=int(md.get("chunk_index", 0)),
                )
            )
        return out

    def fetch_hashes(self, ids: list[str]) -> dict[str, str]:
        """Return {id: content_hash} for the requested IDs that exist in the index.

        Used by the diff detector to decide which chunks need re-embedding.
        Missing IDs are simply absent from the result.
        """
        if not ids:
            return {}
        idx = self._ensure()
        out: dict[str, str] = {}
        for start in range(0, len(ids), _FETCH_BATCH):
            batch = ids[start : start + _FETCH_BATCH]
            res = idx.fetch(ids=batch)
            vectors = res.get("vectors", {}) if isinstance(res, dict) else getattr(res, "vectors", {})
            for vid, payload in vectors.items():
                md = payload.get("metadata") if isinstance(payload, dict) else getattr(payload, "metadata", None)
                if md and "content_hash" in md:
                    out[vid] = md["content_hash"]
        return out

    def list_all_ids(self) -> list[str]:
        """Return every vector ID currently in the index.

        Used by the diff detector to find orphans (IDs in Pinecone that the latest
        crawl did not produce → page was deleted). Pinecone's `list` paginates.
        """
        idx = self._ensure()
        ids: list[str] = []
        for page in idx.list():
            ids.extend(page)
        return ids
