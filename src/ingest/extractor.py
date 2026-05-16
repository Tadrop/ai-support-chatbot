"""Strip raw HTML down to clean, chunkable text.

Drops nav/footer/scripts/style/aside/forms — anything that's chrome rather than
content. Preserves paragraph and heading boundaries (as blank lines and `# `-style
prefixes) so the chunker can later split on them semantically.

We never set `content_type` here — the crawler classified the URL, and the rest of
the pipeline trusts that.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

from src.schemas import CrawledPage

# Tags whose content is ALWAYS chrome, not page content.
_DROP_TAGS = {
    "script", "style", "noscript", "template", "svg",
    "nav", "footer", "header", "aside", "form",
    "iframe", "button",
}

# Tags whose content is real content but should produce a paragraph break.
_BLOCK_TAGS = {
    "p", "div", "section", "article",
    "li", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre",
}


def _normalise_whitespace(s: str) -> str:
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _walk(node, out: list[str]) -> None:
    """Depth-first walk emitting text fragments with paragraph breaks at block tags."""
    if isinstance(node, NavigableString):
        text = str(node)
        if text.strip():
            out.append(text)
        return

    name = getattr(node, "name", None)
    if name in _DROP_TAGS:
        return

    is_block = name in _BLOCK_TAGS
    is_heading = name in {"h1", "h2", "h3", "h4", "h5", "h6"}

    if is_block:
        out.append("\n\n")
    if is_heading:
        out.append("# ")  # marker so the chunker can prefer splitting at headings

    for child in getattr(node, "children", []):
        _walk(child, out)

    if is_block:
        out.append("\n\n")


def extract_text(html: str) -> str:
    """Convert raw HTML to clean text with paragraph boundaries preserved."""
    soup = BeautifulSoup(html, "lxml")
    # Drop chrome up-front so we don't waste cycles walking into them.
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    # Prefer the most content-rich landmark if present.
    root = soup.find("main") or soup.find("article") or soup.body or soup
    chunks: list[str] = []
    _walk(root, chunks)
    return _normalise_whitespace("".join(chunks))


def extract_page(page: CrawledPage) -> CrawledPage:
    """Return a new CrawledPage with .text replaced by clean extracted text."""
    cleaned = extract_text(page.text)
    return page.model_copy(update={"text": cleaned})
