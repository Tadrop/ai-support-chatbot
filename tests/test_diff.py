"""Diff-detector tests — pure logic, no Pinecone calls."""

from __future__ import annotations

from datetime import datetime, timezone

from src.ingest.diff import plan_diff
from src.schemas import Chunk


def _chunk(id_: str, content_hash: str = "h") -> Chunk:
    return Chunk(
        id=id_,
        url="https://example.test/p",
        title="t",
        content_type="other",
        chunk_index=0,
        text="text",
        content_hash=content_hash,
        last_modified=datetime.now(timezone.utc),
    )


def test_all_new_chunks_get_embedded():
    new = [_chunk("a"), _chunk("b")]
    plan = plan_diff(new_chunks=new, existing_hashes={}, all_existing_ids=[])
    assert {c.id for c in plan.to_embed} == {"a", "b"}
    assert plan.unchanged == []
    assert plan.to_delete == []


def test_unchanged_chunks_are_skipped():
    """Same id + same content_hash → no re-embed."""
    new = [_chunk("a", "h1"), _chunk("b", "h2")]
    plan = plan_diff(new, existing_hashes={"a": "h1", "b": "h2"}, all_existing_ids=["a", "b"])
    assert plan.to_embed == []
    assert {c.id for c in plan.unchanged} == {"a", "b"}
    assert plan.to_delete == []


def test_changed_hash_triggers_reembed():
    new = [_chunk("a", "h1-NEW")]
    plan = plan_diff(new, existing_hashes={"a": "h1-OLD"}, all_existing_ids=["a"])
    assert {c.id for c in plan.to_embed} == {"a"}
    assert plan.unchanged == []


def test_orphans_are_marked_for_deletion():
    """Old IDs not produced by the new crawl must be deleted —
    otherwise the bot can answer from a deleted page (silent staleness bug)."""
    new = [_chunk("a", "h1")]
    plan = plan_diff(
        new,
        existing_hashes={"a": "h1"},
        all_existing_ids=["a", "orphan-1", "orphan-2"],
    )
    assert plan.unchanged and plan.unchanged[0].id == "a"
    assert set(plan.to_delete) == {"orphan-1", "orphan-2"}


def test_mixed_diff_partitions_correctly():
    new = [
        _chunk("keep", "h-keep"),     # unchanged
        _chunk("changed", "h-NEW"),   # re-embed
        _chunk("brand-new", "h-X"),   # new
    ]
    plan = plan_diff(
        new,
        existing_hashes={"keep": "h-keep", "changed": "h-OLD"},
        all_existing_ids=["keep", "changed", "removed"],
    )
    assert {c.id for c in plan.to_embed} == {"changed", "brand-new"}
    assert {c.id for c in plan.unchanged} == {"keep"}
    assert plan.to_delete == ["removed"]
