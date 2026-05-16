"""HTML-extractor tests — verify chrome stripping + heading preservation."""

from __future__ import annotations

from src.ingest.extractor import extract_text


def test_drops_script_and_style():
    html = """
    <html><body>
      <script>var x = 1;</script>
      <style>body { color: red; }</style>
      <p>Real content here.</p>
    </body></html>
    """
    out = extract_text(html)
    assert "Real content here." in out
    assert "var x" not in out
    assert "color: red" not in out


def test_drops_nav_footer_aside():
    html = """
    <html><body>
      <nav>Home | Shop | About</nav>
      <main><p>Body of the page.</p></main>
      <aside>Newsletter signup</aside>
      <footer>(c) 2026</footer>
    </body></html>
    """
    out = extract_text(html)
    assert "Body of the page." in out
    assert "Home | Shop" not in out
    assert "Newsletter signup" not in out
    assert "(c) 2026" not in out


def test_preserves_paragraph_boundaries():
    html = "<html><body><p>First.</p><p>Second.</p><p>Third.</p></body></html>"
    out = extract_text(html)
    # Paragraph breaks become double newlines.
    assert "First." in out and "Second." in out and "Third." in out
    assert "\n\n" in out


def test_marks_headings_for_chunker():
    html = "<html><body><h2>Returns Policy</h2><p>30 days.</p></body></html>"
    out = extract_text(html)
    # The walker emits `# ` as a soft marker so the chunker can spot heading starts.
    assert "# Returns Policy" in out
    assert "30 days." in out


def test_prefers_main_landmark_when_present():
    """If <main> exists, prefer it — most sites stuff promo bars and cookie
    banners outside of <main>."""
    html = """
    <html><body>
      <div class="cookie-banner">We use cookies</div>
      <main><p>Actual article content.</p></main>
      <div class="promo">Buy now!</div>
    </body></html>
    """
    out = extract_text(html)
    assert "Actual article content." in out
    assert "We use cookies" not in out
    assert "Buy now!" not in out


def test_collapses_excess_whitespace():
    html = "<p>One   two\t\tthree   \n\n\n\n   four.</p>"
    out = extract_text(html)
    # No runs of 3+ newlines, no tab characters.
    assert "\n\n\n" not in out
    assert "\t" not in out
    assert "One two three" in out  # internal whitespace collapsed to single spaces
