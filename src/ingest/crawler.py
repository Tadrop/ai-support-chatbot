"""Site crawler.

Strategy:
  1. Try `/sitemap.xml` first (also handles sitemap indexes that point to other sitemaps).
     Sitemaps give us URLs + last-modified dates without fetching every page.
  2. Fall back to a BFS link crawl from the root URL, same-host only, respecting
     CRAWL_MAX_PAGES and CRAWL_CONCURRENCY.
  3. Fetch each page with Playwright (chromium, headless) so JS-rendered content
     is captured.

`crawl()` is async and returns a list of CrawledPage. The orchestrator decides
what to do with them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from playwright.async_api import Browser, async_playwright

from src.config import get_settings
from src.logging_setup import get_logger
from src.schemas import ContentType, CrawledPage

log = get_logger(__name__)

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# Asset extensions we never want to "crawl" as pages.
_SKIP_EXT = (
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".mp4", ".mp3", ".zip", ".gz", ".css", ".js", ".ico",
)


def _classify(url: str) -> ContentType:
    """Cheap URL-based content-type heuristic. The extractor can override later."""
    p = urlparse(url).path.lower()
    if "/faq" in p or "/help" in p:
        return "faq"
    if "/policy" in p or "/policies" in p or "/terms" in p or "/privacy" in p:
        return "policy"
    if "/product" in p or "/shop" in p or "/store" in p:
        return "product"
    if "/blog" in p or "/news" in p or "/articles" in p:
        return "blog"
    return "other"


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc == urlparse(b).netloc


def _normalise(url: str) -> str:
    """Drop fragments; keep query string (some product pages depend on it)."""
    no_frag, _ = urldefrag(url)
    return no_frag


def _is_crawlable(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    if p.path.lower().endswith(_SKIP_EXT):
        return False
    return True


async def _fetch_sitemap_urls(root: str, client: httpx.AsyncClient) -> list[tuple[str, datetime | None]]:
    """Walk sitemap.xml (and any nested sitemap indexes). Returns [(url, lastmod), ...]."""
    sitemap_url = urljoin(root, "/sitemap.xml")
    found: list[tuple[str, datetime | None]] = []
    seen_sitemaps: set[str] = set()
    queue = [sitemap_url]

    while queue:
        sm = queue.pop()
        if sm in seen_sitemaps:
            continue
        seen_sitemaps.add(sm)
        try:
            r = await client.get(sm)
            if r.status_code != 200:
                log.info("sitemap.miss", url=sm, status=r.status_code)
                continue
            root_el = ET.fromstring(r.content)
        except (httpx.HTTPError, ET.ParseError) as e:
            log.warning("sitemap.error", url=sm, error=str(e))
            continue

        # sitemap index → enqueue child sitemaps
        for el in root_el.findall("sm:sitemap", _SITEMAP_NS):
            loc = el.findtext("sm:loc", default="", namespaces=_SITEMAP_NS).strip()
            if loc:
                queue.append(loc)
        # url set
        for el in root_el.findall("sm:url", _SITEMAP_NS):
            loc = el.findtext("sm:loc", default="", namespaces=_SITEMAP_NS).strip()
            lastmod_raw = el.findtext("sm:lastmod", default="", namespaces=_SITEMAP_NS).strip()
            if not loc:
                continue
            lastmod: datetime | None = None
            if lastmod_raw:
                try:
                    lastmod = datetime.fromisoformat(lastmod_raw.replace("Z", "+00:00"))
                except ValueError:
                    lastmod = None
            found.append((_normalise(loc), lastmod))
    return found


async def _fetch_page(browser: Browser, url: str, timeout_s: int) -> tuple[str, str] | None:
    """Render a page and return (html, title). None on failure."""
    ctx = await browser.new_context()
    page = await ctx.new_page()
    try:
        resp = await page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
        if resp is None or not resp.ok:
            log.warning("crawl.http_error", url=url, status=getattr(resp, "status", None))
            return None
        # Best-effort: wait for network to settle but don't block forever.
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        html = await page.content()
        title = (await page.title()) or ""
        return html, title
    except Exception as e:
        log.warning("crawl.page_error", url=url, error=str(e))
        return None
    finally:
        await ctx.close()


async def _bfs_discover(root: str, client: httpx.AsyncClient, max_pages: int) -> list[str]:
    """Plain HTTP BFS just to discover URLs when a sitemap is unavailable.
    We do NOT use these HTML bodies for extraction (Playwright will re-render).
    """
    seen: set[str] = set()
    out: list[str] = []
    queue: list[str] = [root]
    from bs4 import BeautifulSoup  # local import keeps this module light if BFS unused

    while queue and len(out) < max_pages:
        url = queue.pop(0)
        url = _normalise(url)
        if url in seen or not _is_crawlable(url) or not _same_host(url, root):
            continue
        seen.add(url)
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            log.warning("bfs.error", url=url, error=str(e))
            continue
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            continue
        out.append(url)
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            nxt = _normalise(urljoin(url, a["href"]))
            if nxt not in seen and _same_host(nxt, root) and _is_crawlable(nxt):
                queue.append(nxt)
    return out


async def crawl(max_pages: int | None = None) -> list[CrawledPage]:
    """Top-level crawl entrypoint. Returns rendered pages with title + HTML text.

    `max_pages` overrides the value from settings for this call only, without
    mutating the shared Settings singleton.
    """
    s = get_settings()
    root = str(s.crawl_root_url)
    effective_max = max_pages if max_pages is not None else s.crawl_max_pages
    headers = {"User-Agent": s.crawl_user_agent}

    async with httpx.AsyncClient(
        timeout=s.crawl_request_timeout_s, headers=headers, follow_redirects=True
    ) as client:
        sitemap_entries = await _fetch_sitemap_urls(root, client)
        if sitemap_entries:
            log.info("crawl.discover", source="sitemap", count=len(sitemap_entries))
            urls_with_lastmod = [
                (u, lm) for (u, lm) in sitemap_entries
                if _is_crawlable(u) and _same_host(u, root)
            ][:effective_max]
        else:
            log.info("crawl.discover", source="bfs")
            discovered = await _bfs_discover(root, client, effective_max)
            urls_with_lastmod = [(u, None) for u in discovered]

    # Render each URL with Playwright, with a bounded concurrency semaphore.
    sem = asyncio.Semaphore(s.crawl_concurrency)
    pages: list[CrawledPage] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            async def _worker(url: str, lastmod: datetime | None) -> CrawledPage | None:
                async with sem:
                    res = await _fetch_page(browser, url, s.crawl_request_timeout_s)
                if res is None:
                    return None
                html, title = res
                return CrawledPage(
                    url=url,
                    title=title.strip()[:300],
                    content_type=_classify(url),
                    text=html,  # raw HTML; the extractor strips it
                    last_modified=lastmod,
                    fetched_at=datetime.now(timezone.utc),
                )

            results = await asyncio.gather(
                *(_worker(u, lm) for (u, lm) in urls_with_lastmod),
                return_exceptions=False,
            )
            pages = [p for p in results if p is not None]
        finally:
            await browser.close()

    log.info("crawl.done", fetched=len(pages), attempted=len(urls_with_lastmod))
    return pages
