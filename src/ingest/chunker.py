"""Semantic-aware token chunker.

Algorithm:
  1. Split clean text into "blocks" on blank lines (the extractor produced these
     boundaries from <p>, <li>, <h*> etc.). Headings (lines starting with `# `)
     start a new block AND get attached to the next block as a soft prefix.
  2. Greedily pack blocks into windows of ~CHUNK_TARGET_TOKENS, never splitting a
     block (so a paragraph or list item never gets cut mid-thought).
  3. If a single block exceeds the target, fall back to splitting it on sentence
     boundaries; if even one sentence exceeds the target, hard-split on tokens.
  4. Apply CHUNK_OVERLAP_TOKENS overlap by prefixing each chunk (after the first)
     with the trailing tokens of the previous chunk. Overlap helps retrieval
     recall when an answer straddles a chunk boundary.

Output is a list of `Chunk` with deterministic IDs (sha1 of url + chunk_index)
and content_hash (sha256 of text). Determinism is what makes the diff detector
work: re-running the pipeline on unchanged content produces identical IDs and
hashes, so nothing gets re-embedded.
"""

from __future__ import annotations

import hashlib
import re

import tiktoken

from src.config import get_settings
from src.schemas import Chunk, CrawledPage

# `cl100k_base` is the BPE tokenizer used by OpenAI/tiktoken; it's a reasonable
# proxy for token counts across modern LLMs. The exact count doesn't have to
# match Voyage's tokenizer — we only need a stable budget for chunk sizing.
_ENC = tiktoken.get_encoding("cl100k_base")

_HEADING = re.compile(r"^#\s+(.*)$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def _count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def _split_blocks(text: str) -> list[str]:
    """Split on blank lines, keeping non-empty blocks. Strips heading marker `# `."""
    blocks: list[str] = []
    for raw in re.split(r"\n\s*\n", text):
        b = raw.strip()
        if not b:
            continue
        m = _HEADING.match(b)
        if m:
            b = m.group(1).strip()
        if b:
            blocks.append(b)
    return blocks


def _split_long_block(block: str, target: int) -> list[str]:
    """Block exceeds the token budget — fall back to sentence packing, then hard cut."""
    sentences = _SENTENCE_SPLIT.split(block)
    out: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for sent in sentences:
        st = _count_tokens(sent)
        if st > target:
            # Sentence itself is huge — hard-split on tokens as last resort.
            if cur:
                out.append(" ".join(cur))
                cur, cur_tok = [], 0
            tok_ids = _ENC.encode(sent)
            for i in range(0, len(tok_ids), target):
                out.append(_ENC.decode(tok_ids[i : i + target]))
            continue
        if cur_tok + st > target and cur:
            out.append(" ".join(cur))
            cur, cur_tok = [], 0
        cur.append(sent)
        cur_tok += st
    if cur:
        out.append(" ".join(cur))
    return out


def _pack(blocks: list[str], target: int) -> list[str]:
    """Greedy block packing. Blocks larger than target get pre-split."""
    expanded: list[str] = []
    for b in blocks:
        if _count_tokens(b) > target:
            expanded.extend(_split_long_block(b, target))
        else:
            expanded.append(b)

    chunks: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for b in expanded:
        bt = _count_tokens(b)
        if cur_tok + bt > target and cur:
            chunks.append("\n\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(b)
        cur_tok += bt
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """Prepend the trailing `overlap` tokens of chunk N-1 to chunk N."""
    if overlap <= 0 or len(chunks) < 2:
        return chunks
    out = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_ids = _ENC.encode(chunks[i - 1])
        tail = _ENC.decode(prev_ids[-overlap:]) if len(prev_ids) > overlap else _ENC.decode(prev_ids)
        out.append(f"{tail}\n\n{chunks[i]}")
    return out


def _chunk_id(url: str, idx: int) -> str:
    return hashlib.sha1(f"{url}#{idx}".encode("utf-8")).hexdigest()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_page(
    page: CrawledPage,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[Chunk]:
    """Chunk one extracted page into Chunk records ready for embedding."""
    s = get_settings()
    target = target_tokens if target_tokens is not None else s.chunk_target_tokens
    overlap = overlap_tokens if overlap_tokens is not None else s.chunk_overlap_tokens

    if not page.text.strip():
        return []

    blocks = _split_blocks(page.text)
    raw_chunks = _pack(blocks, target)
    final_chunks = _apply_overlap(raw_chunks, overlap)

    url_str = str(page.url)
    return [
        Chunk(
            id=_chunk_id(url_str, i),
            url=page.url,
            title=page.title,
            content_type=page.content_type,
            chunk_index=i,
            text=text,
            content_hash=_content_hash(text),
            last_modified=page.last_modified,
        )
        for i, text in enumerate(final_chunks)
    ]
