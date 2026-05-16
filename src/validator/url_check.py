"""Cited-URL self-check.

Every URL that Claude claims to have cited must actually appear in the set of
retrieved chunk URLs.  If any cited URL was NOT in the retrieved context, Claude
invented it — the entire response is dropped and a handoff is triggered.

This is the single most important anti-hallucination control in the system.
The rule must never be relaxed.
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.logging_setup import get_logger
from src.schemas import ChatResponse, RetrievedChunk

log = get_logger(__name__)


def _normalise(url: str) -> str:
    """Strip scheme, trailing slash, and fragment for comparison."""
    p = urlparse(url.strip())
    return f"{p.netloc}{p.path}".rstrip("/").lower()


def validate_citations(
    response: ChatResponse,
    retrieved: list[RetrievedChunk],
) -> ChatResponse | None:
    """Return the response unchanged if all cited URLs are grounded, else None.

    None signals to the caller that the answer must be discarded and the
    conversation handed off to a human agent.
    """
    if not response.cited_urls:
        # No citations claimed → grounding check passes (the answer may still
        # trigger handoff via low confidence or in_scope=False, but that's the
        # caller's responsibility).
        return response

    retrieved_urls = {_normalise(c.url) for c in retrieved}
    hallucinated: list[str] = []

    for cited in response.cited_urls:
        if _normalise(cited) not in retrieved_urls:
            hallucinated.append(cited)

    if hallucinated:
        log.warning(
            "validator.hallucinated_urls",
            hallucinated=hallucinated,
            retrieved=[c.url for c in retrieved],
        )
        return None  # caller must handoff

    return response


def filter_cited_urls(
    response: ChatResponse,
    retrieved: list[RetrievedChunk],
) -> ChatResponse:
    """Silently drop any cited URL not in retrieved context (permissive variant).

    Use this only when you want to surface a partial answer rather than
    discard it entirely.  The strict `validate_citations` is used by default
    in the chat flow; this variant is not used in production but is provided
    for evaluation tooling.
    """
    retrieved_urls = {_normalise(c.url) for c in retrieved}
    clean = [u for u in response.cited_urls if _normalise(u) in retrieved_urls]
    return response.model_copy(update={"cited_urls": clean})
