"""End-to-end ingestion orchestrator + CLI entrypoint.

Pipeline:
  crawl -> extract -> chunk -> diff vs index -> embed (changed only) -> upsert
                                             -> delete orphans

Run from the project root:

    # Full ingest (uses CRAWL_ROOT_URL from .env):
    python -m src.ingest.pipeline

    # Dry run (no Pinecone writes, prints what WOULD happen):
    python -m src.ingest.pipeline --dry-run

    # Limit to N pages for dev:
    python -m src.ingest.pipeline --max-pages 20

`run_ingest` accepts optional injected deps (crawler_fn, embedder, index) so
the entire pipeline can be exercised in tests without any network calls.
The CLI path passes None for all three, which causes the real implementations
to be constructed lazily.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass

from src.config import get_settings
from src.ingest.chunker import chunk_page
from src.ingest.diff import DiffPlan, plan_diff
from src.ingest.extractor import extract_page
from src.logging_setup import configure_logging, get_logger
from src.protocols import CrawlerFn, EmbedderProtocol, VectorIndexProtocol
from src.schemas import Chunk, CrawledPage

log = get_logger(__name__)


@dataclass
class IngestReport:
    pages_fetched: int
    chunks_total: int
    chunks_embedded: int
    chunks_unchanged: int
    chunks_deleted: int
    elapsed_s: float

    def as_dict(self) -> dict:
        return self.__dict__


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _all_chunks_from_pages(pages: list[CrawledPage]) -> list[Chunk]:
    """Extract clean text + chunk every page."""
    out: list[Chunk] = []
    for p in pages:
        cleaned = extract_page(p)
        if not cleaned.text.strip():
            log.info("ingest.skip_empty", url=str(p.url))
            continue
        out.extend(chunk_page(cleaned))
    return out


def _execute_plan(
    plan: DiffPlan,
    embedder: EmbedderProtocol | None,
    index: VectorIndexProtocol,
    dry_run: bool,
) -> tuple[int, int]:
    """Embed changed chunks + upsert; delete orphans. Returns (embedded, deleted).

    `embedder` may be None only when plan.to_embed is empty (orphan-only diff).
    """
    if dry_run:
        log.info(
            "ingest.dry_run",
            would_embed=len(plan.to_embed),
            would_delete=len(plan.to_delete),
            unchanged=len(plan.unchanged),
        )
        return 0, 0

    written = 0
    if plan.to_embed:
        if embedder is None:
            raise RuntimeError("embedder is required when plan.to_embed is non-empty")
        embedded = embedder.embed_chunks(plan.to_embed)
        written = index.upsert(embedded)

    deleted = index.delete(plan.to_delete)
    return written, deleted


def _build_real_crawler(max_pages: int | None) -> CrawlerFn:
    """Return a zero-arg async callable that invokes the real Playwright crawler."""
    from src.ingest.crawler import crawl  # deferred — Playwright not needed in tests

    if max_pages is None:
        return crawl
    # Wrap with a partial that overrides the per-run page cap without mutating
    # the shared Settings singleton.
    async def _capped() -> list[CrawledPage]:
        return await crawl(max_pages=max_pages)
    return _capped


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def run_ingest(
    *,
    dry_run: bool = False,
    max_pages_override: int | None = None,
    crawler_fn: CrawlerFn | None = None,
    embedder: EmbedderProtocol | None = None,
    index: VectorIndexProtocol | None = None,
) -> IngestReport:
    """Run the full ingestion pipeline once.

    Pass `crawler_fn`, `embedder`, and `index` to inject fakes for testing.
    When any of them is None the real implementation is constructed lazily —
    that's what the CLI path does.
    """
    s = get_settings()
    started = time.perf_counter()
    log.info("ingest.start", root=str(s.crawl_root_url), dry_run=dry_run)

    # --- crawl ---
    if crawler_fn is None:
        crawler_fn = _build_real_crawler(max_pages_override)
    pages = await crawler_fn()

    # --- extract + chunk ---
    chunks = _all_chunks_from_pages(pages)
    if not chunks:
        log.warning("ingest.no_chunks")
        return IngestReport(
            pages_fetched=len(pages),
            chunks_total=0,
            chunks_embedded=0,
            chunks_unchanged=0,
            chunks_deleted=0,
            elapsed_s=time.perf_counter() - started,
        )

    # --- build index client if not injected ---
    if index is None:
        from src.index.pinecone_client import PineconeIndex
        index = PineconeIndex()

    # --- diff ---
    new_ids = [c.id for c in chunks]
    existing_hashes = index.fetch_hashes(new_ids) if not dry_run else {}
    all_existing_ids = index.list_all_ids() if not dry_run else []
    plan = plan_diff(chunks, existing_hashes, all_existing_ids)
    log.info(
        "ingest.diff_plan",
        to_embed=len(plan.to_embed),
        unchanged=len(plan.unchanged),
        to_delete=len(plan.to_delete),
    )

    # --- embed (only construct Voyage client if there's work to do) ---
    if embedder is None and plan.to_embed and not dry_run:
        from src.ingest.embedder import VoyageEmbedder
        embedder = VoyageEmbedder()

    embedded_count, deleted_count = _execute_plan(plan, embedder, index, dry_run=dry_run)

    elapsed = time.perf_counter() - started
    report = IngestReport(
        pages_fetched=len(pages),
        chunks_total=len(chunks),
        chunks_embedded=embedded_count,
        chunks_unchanged=len(plan.unchanged),
        chunks_deleted=deleted_count,
        elapsed_s=elapsed,
    )
    log.info("ingest.done", **report.as_dict())
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GreenLeaf chatbot ingestion pipeline")
    p.add_argument("--dry-run", action="store_true", help="Crawl + chunk only; no Pinecone writes")
    p.add_argument("--max-pages", type=int, default=None, help="Cap pages fetched (overrides .env)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    s = get_settings()
    configure_logging(s.log_level)
    try:
        report = asyncio.run(
            run_ingest(dry_run=args.dry_run, max_pages_override=args.max_pages)
        )
    except KeyboardInterrupt:
        log.warning("ingest.interrupted")
        return 130
    except Exception as e:
        log.error("ingest.failed", error=str(e), exc_info=True)
        return 1
    return 0 if report.pages_fetched > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
