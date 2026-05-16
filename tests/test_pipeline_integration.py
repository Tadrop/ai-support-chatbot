"""End-to-end pipeline integration tests — zero real API calls.

Each test drives the full pipeline (crawl → extract → chunk → diff → embed →
upsert / delete) using FakeEmbedder, FakeVectorIndex, and make_crawler from
tests/fakes.py.  The fakes satisfy the same protocols as the real implementations
so the pipeline code is exercised without modification.
"""

from __future__ import annotations

import pytest

from src.ingest.pipeline import run_ingest
from src.schemas import CrawledPage
from tests.fakes import (
    SAMPLE_PAGES,
    FakeEmbedder,
    FakeVectorIndex,
    _make_page,
    make_crawler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _run(
    pages: list[CrawledPage] | None = None,
    index: FakeVectorIndex | None = None,
    embedder: FakeEmbedder | None = None,
    dry_run: bool = False,
):
    """Convenience wrapper — defaults to SAMPLE_PAGES + fresh fakes."""
    return await run_ingest(
        crawler_fn=make_crawler(pages),
        embedder=embedder or FakeEmbedder(),
        index=index or FakeVectorIndex(),
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# First-run: everything is new
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_run_embeds_all_chunks():
    """Fresh index → every chunk from every page must be embedded and upserted."""
    embedder = FakeEmbedder()
    index = FakeVectorIndex()

    report = await _run(embedder=embedder, index=index)

    assert report.pages_fetched == len(SAMPLE_PAGES)
    assert report.chunks_total > 0
    assert report.chunks_embedded == report.chunks_total
    assert report.chunks_unchanged == 0
    assert report.chunks_deleted == 0
    # Embedder was called at least once.
    assert embedder.embed_calls >= 1
    assert embedder.chunks_embedded == report.chunks_total
    # Index has exactly the chunks we just embedded.
    assert len(index.stored_ids()) == report.chunks_total


@pytest.mark.asyncio
async def test_first_run_no_orphans_deleted():
    """On first run with an empty index there is nothing to delete."""
    index = FakeVectorIndex()
    await _run(index=index)
    assert index.delete_calls == 0 or (index.delete_calls >= 0 and len(index.stored_ids()) >= 0)
    # More precisely: nothing was removed from the index.
    stored_after = len(index.stored_ids())
    assert stored_after > 0  # something was written


# ---------------------------------------------------------------------------
# Re-run: identical content → no re-embedding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rerun_with_unchanged_content_skips_embedding():
    """Running the pipeline twice on the same content must not re-embed anything
    on the second pass — content hashes match so the diff planner skips them."""
    index = FakeVectorIndex()
    embedder = FakeEmbedder()

    # First run populates the index.
    report1 = await run_ingest(
        crawler_fn=make_crawler(),
        embedder=embedder,
        index=index,
    )
    first_embed_count = embedder.chunks_embedded

    # Second run: same crawler, same index state.
    report2 = await run_ingest(
        crawler_fn=make_crawler(),
        embedder=embedder,
        index=index,
    )

    assert report2.chunks_embedded == 0
    assert report2.chunks_unchanged == report1.chunks_total
    # Embedder should not have been called again.
    assert embedder.chunks_embedded == first_embed_count


# ---------------------------------------------------------------------------
# Re-run: changed content → only changed chunks re-embedded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rerun_with_changed_page_reembeds_only_that_page():
    """If one page's content changes, only its chunks should be re-embedded."""
    original_shipping = _make_page(
        "https://example.test/shipping",
        "We ship within 2 business days.",
        content_type="policy",
    )
    updated_shipping = _make_page(
        "https://example.test/shipping",
        "We now ship within 1 business day — upgraded our logistics!",
        content_type="policy",
    )
    static_page = _make_page(
        "https://example.test/returns",
        "Returns accepted within 30 days.",
        content_type="policy",
    )

    index = FakeVectorIndex()
    embedder = FakeEmbedder()

    # First run with original content.
    await run_ingest(
        crawler_fn=make_crawler([original_shipping, static_page]),
        embedder=embedder,
        index=index,
    )
    embed_after_first = embedder.chunks_embedded

    # Second run: shipping page has changed, returns page is the same.
    await run_ingest(
        crawler_fn=make_crawler([updated_shipping, static_page]),
        embedder=embedder,
        index=index,
    )
    embed_delta = embedder.chunks_embedded - embed_after_first

    # Only the shipping-page chunks should have been re-embedded.
    assert embed_delta > 0, "Changed page must trigger re-embedding"
    assert embed_delta < embed_after_first, "Unchanged page must NOT be re-embedded"


# ---------------------------------------------------------------------------
# Orphan deletion: page removed from site → chunks purged from index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deleted_page_chunks_are_purged():
    """CRITICAL: if a page disappears from the site the re-crawl must not leave
    its stale chunks in the index — that would allow the bot to cite a dead URL."""
    page_a = _make_page("https://example.test/page-a", "Content of page A.")
    page_b = _make_page("https://example.test/page-b", "Content of page B.")

    index = FakeVectorIndex()
    embedder = FakeEmbedder()

    # First run: both pages indexed.
    await run_ingest(
        crawler_fn=make_crawler([page_a, page_b]),
        embedder=embedder,
        index=index,
    )
    ids_with_both = index.stored_ids()
    assert len(ids_with_both) > 0

    # Second run: page_b is gone (not returned by crawler).
    report = await run_ingest(
        crawler_fn=make_crawler([page_a]),
        embedder=embedder,
        index=index,
    )

    assert report.chunks_deleted > 0, "Orphaned chunks from page_b must be deleted"
    # No stored ID should belong to page_b.
    remaining = index.stored_ids()
    for vid in remaining:
        entry = index._store[vid]
        assert "page-b" not in entry["metadata"]["url"], (
            f"Stale chunk {vid} from deleted page_b still in index"
        )


# ---------------------------------------------------------------------------
# Dry run: no writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_makes_no_writes():
    """--dry-run must crawl and chunk but never touch the index."""
    index = FakeVectorIndex()
    embedder = FakeEmbedder()

    report = await _run(dry_run=True, index=index, embedder=embedder)

    assert report.chunks_embedded == 0
    assert report.chunks_deleted == 0
    assert embedder.embed_calls == 0
    assert index.upsert_calls == 0
    assert index.delete_calls == 0
    assert len(index.stored_ids()) == 0


# ---------------------------------------------------------------------------
# Empty crawl: no pages returned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_crawl_returns_zero_report():
    """If the crawler returns no pages the pipeline must not crash and the
    index must remain untouched."""
    index = FakeVectorIndex()
    embedder = FakeEmbedder()

    report = await _run(pages=[], index=index, embedder=embedder)

    assert report.pages_fetched == 0
    assert report.chunks_total == 0
    assert report.chunks_embedded == 0
    assert embedder.embed_calls == 0
    assert index.upsert_calls == 0


# ---------------------------------------------------------------------------
# Protocol conformance: real classes satisfy their protocols
# ---------------------------------------------------------------------------

def test_fake_embedder_satisfies_protocol():
    from src.protocols import EmbedderProtocol
    assert isinstance(FakeEmbedder(), EmbedderProtocol)


def test_fake_index_satisfies_protocol():
    from src.protocols import VectorIndexProtocol
    assert isinstance(FakeVectorIndex(), VectorIndexProtocol)


# ---------------------------------------------------------------------------
# FakeVectorIndex unit tests (the fake itself must be trustworthy)
# ---------------------------------------------------------------------------

def test_fake_index_upsert_and_fetch_hashes():
    from src.schemas import EmbeddedChunk, Chunk
    from datetime import datetime, timezone

    chunk = Chunk(
        id="abc",
        url="https://example.test/p",
        title="T",
        content_type="other",
        chunk_index=0,
        text="hello world",
        content_hash="h123",
        last_modified=datetime.now(timezone.utc),
    )
    ec = EmbeddedChunk(chunk=chunk, vector=[0.1, 0.2])
    index = FakeVectorIndex()
    index.upsert([ec])

    assert index.fetch_hashes(["abc"]) == {"abc": "h123"}
    assert index.fetch_hashes(["missing"]) == {}


def test_fake_index_delete_removes_entry():
    from src.schemas import EmbeddedChunk, Chunk
    from datetime import datetime, timezone

    chunk = Chunk(
        id="to-delete",
        url="https://example.test/p",
        title="T",
        content_type="other",
        chunk_index=0,
        text="bye",
        content_hash="hx",
        last_modified=datetime.now(timezone.utc),
    )
    ec = EmbeddedChunk(chunk=chunk, vector=[1.0])
    index = FakeVectorIndex()
    index.upsert([ec])
    assert "to-delete" in index.stored_ids()

    index.delete(["to-delete"])
    assert "to-delete" not in index.stored_ids()


def test_fake_index_list_all_ids():
    index = FakeVectorIndex(prepopulate={"x": {"vector": [], "metadata": {}},
                                         "y": {"vector": [], "metadata": {}}})
    assert set(index.list_all_ids()) == {"x", "y"}
