"""Diff detector — decides which chunks need re-embedding and which to delete.

Pure function; no I/O. Compare what the latest crawl produced (`new_chunks`) to
what's currently in Pinecone (`existing_hashes`).

Returns three sets:
  - to_embed: chunks that are new OR whose content_hash changed → embed + upsert
  - unchanged: chunks already in Pinecone with matching hash → skip (saves cost)
  - to_delete: IDs in Pinecone that the latest crawl did NOT produce → orphans
               (their source page was removed or its URL changed)

Critical: `to_delete` MUST be acted on. Stale chunks in the index = stale answers
in the chatbot, which violates the never-invent rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.schemas import Chunk


@dataclass(frozen=True)
class DiffPlan:
    to_embed: list[Chunk]
    unchanged: list[Chunk]
    to_delete: list[str]


def plan_diff(new_chunks: list[Chunk], existing_hashes: dict[str, str], all_existing_ids: list[str]) -> DiffPlan:
    """Build a diff plan.

    Args:
        new_chunks:        chunks produced by the current crawl.
        existing_hashes:   {id: content_hash} fetched from Pinecone for the IDs
                           in `new_chunks` (the index client only fetches the
                           IDs we ask about, so this dict only contains overlap).
        all_existing_ids:  every vector ID currently in Pinecone, regardless of
                           whether the new crawl produced it. Needed to find orphans.
    """
    to_embed: list[Chunk] = []
    unchanged: list[Chunk] = []
    new_ids: set[str] = set()

    for c in new_chunks:
        new_ids.add(c.id)
        prev_hash = existing_hashes.get(c.id)
        if prev_hash == c.content_hash:
            unchanged.append(c)
        else:
            to_embed.append(c)

    to_delete = [vid for vid in all_existing_ids if vid not in new_ids]
    return DiffPlan(to_embed=to_embed, unchanged=unchanged, to_delete=to_delete)
