"""Chunker behavior tests — pure logic, no API calls."""

from __future__ import annotations

from datetime import datetime, timezone

from src.ingest.chunker import chunk_page, _content_hash, _chunk_id
from src.schemas import CrawledPage


def _page(text: str, url: str = "https://example.test/page") -> CrawledPage:
    return CrawledPage(
        url=url,
        title="Test page",
        content_type="other",
        text=text,
        last_modified=None,
        fetched_at=datetime.now(timezone.utc),
    )


def test_empty_text_yields_no_chunks():
    assert chunk_page(_page("   \n  \n  "), target_tokens=100, overlap_tokens=10) == []


def test_short_page_produces_single_chunk():
    chunks = chunk_page(_page("Just a short paragraph."), target_tokens=100, overlap_tokens=10)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].text == "Just a short paragraph."


def test_chunk_ids_are_deterministic():
    """Re-chunking identical content must yield identical IDs and hashes —
    that's what makes the diff detector skip unchanged content."""
    text = "Para one.\n\nPara two.\n\nPara three with more words to bulk it up."
    a = chunk_page(_page(text), target_tokens=20, overlap_tokens=5)
    b = chunk_page(_page(text), target_tokens=20, overlap_tokens=5)
    assert [c.id for c in a] == [c.id for c in b]
    assert [c.content_hash for c in a] == [c.content_hash for c in b]


def test_long_text_splits_into_multiple_chunks():
    # 30 paragraphs of ~10 tokens each, target 50 tokens → expect several chunks.
    paragraphs = [f"Paragraph number {i} contains some sample words here." for i in range(30)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_page(_page(text), target_tokens=50, overlap_tokens=10)
    assert len(chunks) > 1
    # Chunk indices are sequential from 0.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_overlap_prepends_tail_of_previous_chunk():
    paragraphs = [f"sentence {i}" for i in range(40)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_page(_page(text), target_tokens=30, overlap_tokens=8)
    assert len(chunks) >= 2
    # The second chunk should start with material drawn from the first.
    # Hard to assert exactly (BPE re-decoding fuzzes whitespace), but it must
    # be longer than the comparable chunk produced with overlap=0.
    no_overlap = chunk_page(_page(text), target_tokens=30, overlap_tokens=0)
    assert len(chunks[1].text) > len(no_overlap[1].text)


def test_oversized_single_block_is_split():
    """A single block bigger than the target must still be split, not dropped."""
    big_block = "word " * 500  # ~500 tokens with no paragraph breaks
    chunks = chunk_page(_page(big_block), target_tokens=80, overlap_tokens=0)
    assert len(chunks) > 1


def test_heading_marker_is_stripped():
    """The extractor emits `# Heading` markers; the chunker must strip them
    before they end up in retrieved text."""
    text = "# Returns Policy\n\nWe accept returns within 30 days."
    chunks = chunk_page(_page(text), target_tokens=100, overlap_tokens=0)
    assert "# " not in chunks[0].text
    assert "Returns Policy" in chunks[0].text
    assert "30 days" in chunks[0].text


def test_id_uses_url_and_index():
    text = "A\n\nB\n\nC"
    chunks = chunk_page(_page(text, url="https://example.test/foo"), target_tokens=2, overlap_tokens=0)
    expected_first = _chunk_id("https://example.test/foo", 0)
    assert chunks[0].id == expected_first
    # Different URL → different ID for the same chunk_index.
    other = chunk_page(_page(text, url="https://example.test/bar"), target_tokens=2, overlap_tokens=0)
    assert other[0].id != chunks[0].id


def test_content_hash_changes_when_text_changes():
    a = _content_hash("the quick brown fox")
    b = _content_hash("the quick brown fox.")  # one extra char
    assert a != b
