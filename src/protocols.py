"""Structural protocols for every I/O boundary in the system.

Using `typing.Protocol` (PEP 544) means real classes satisfy these implicitly —
no inheritance needed. Test fakes also just implement the methods.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol, runtime_checkable

from src.schemas import (
    Chunk,
    CrawledPage,
    EmbeddedChunk,
    ChatResponse,
    RetrievedChunk,
)


@runtime_checkable
class EmbedderProtocol(Protocol):
    def embed_chunks(self, chunks: list[Chunk]) -> list[EmbeddedChunk]: ...
    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class VectorIndexProtocol(Protocol):
    def upsert(self, embedded: list[EmbeddedChunk]) -> int: ...
    def delete(self, ids: Iterable[str]) -> int: ...
    def query(self, vector: list[float], top_k: int) -> list[RetrievedChunk]: ...
    def fetch_hashes(self, ids: list[str]) -> dict[str, str]: ...
    def list_all_ids(self) -> list[str]: ...


@runtime_checkable
class ClaudeClientProtocol(Protocol):
    def complete(self, query: str, chunks: list[RetrievedChunk]) -> ChatResponse: ...


# The crawler is a zero-argument async callable.
CrawlerFn = Callable[[], Awaitable[list[CrawledPage]]]
