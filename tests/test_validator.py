"""Cited-URL self-check tests."""

from __future__ import annotations

from src.schemas import ChatResponse, RetrievedChunk
from src.validator.url_check import filter_cited_urls, validate_citations


def _chunk(url: str, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        id="x", score=score, url=url, title="t",
        content_type="other", text="...", chunk_index=0,
    )


def _resp(cited: list[str], in_scope: bool = True) -> ChatResponse:
    return ChatResponse(answer="A.", cited_urls=cited, confidence=0.9, in_scope=in_scope)


# ---------------------------------------------------------------------------
# validate_citations (strict)
# ---------------------------------------------------------------------------

def test_all_cited_urls_in_context_passes():
    retrieved = [_chunk("https://example.test/a"), _chunk("https://example.test/b")]
    resp = _resp(["https://example.test/a"])
    assert validate_citations(resp, retrieved) is resp


def test_one_hallucinated_url_fails():
    retrieved = [_chunk("https://example.test/a")]
    resp = _resp(["https://example.test/a", "https://made-up.test/fake"])
    assert validate_citations(resp, retrieved) is None


def test_empty_cited_urls_passes():
    """Empty citations is permitted — the upstream chat flow has its own
    'in_scope=True must cite something' rule (validator stays focused)."""
    retrieved = [_chunk("https://example.test/a")]
    resp = _resp([])
    assert validate_citations(resp, retrieved) is resp


def test_url_normalisation_ignores_trailing_slash():
    retrieved = [_chunk("https://example.test/policy/returns/")]
    resp = _resp(["https://example.test/policy/returns"])
    assert validate_citations(resp, retrieved) is resp


def test_url_normalisation_ignores_scheme_case():
    retrieved = [_chunk("https://Example.Test/Page")]
    resp = _resp(["https://example.test/page"])
    assert validate_citations(resp, retrieved) is resp


def test_all_urls_must_be_grounded_not_just_some():
    """Even one hallucinated URL alongside genuine ones must fail —
    we cannot ship partial fabrication."""
    retrieved = [_chunk("https://example.test/a"), _chunk("https://example.test/b")]
    resp = _resp([
        "https://example.test/a",
        "https://example.test/b",
        "https://example.test/INVENTED",
    ])
    assert validate_citations(resp, retrieved) is None


# ---------------------------------------------------------------------------
# filter_cited_urls (permissive variant)
# ---------------------------------------------------------------------------

def test_filter_drops_unknown_keeps_grounded():
    retrieved = [_chunk("https://example.test/a")]
    resp = _resp(["https://example.test/a", "https://made-up.test/fake"])
    out = filter_cited_urls(resp, retrieved)
    assert out.cited_urls == ["https://example.test/a"]
