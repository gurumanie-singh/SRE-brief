"""Optional HTTP fetch + lightweight HTML-to-text extraction (stdlib only)."""

from __future__ import annotations

import logging
import re
import ssl
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.utils import strip_html, strip_emoji, truncate

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "SRE-Brief/1.0 (+https://github.com; reliability RSS reader; contact: none)"
)

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg", "template"):
            self._skip_depth += 1
        if self._skip_depth:
            return
        if t in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "article", "section"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg", "template") and self._skip_depth:
            self._skip_depth -= 1
        if not self._skip_depth and t in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "article", "section"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def fetch_article_text(url: str, timeout: float, max_chars: int) -> str:
    """Fetch URL and return best-effort plain text. Returns '' on failure."""
    if not url.startswith(("http://", "https://")):
        return ""
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"})
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310 — user feed URLs only
            raw_bytes = resp.read(max_chars + 5000)
            charset = resp.headers.get_content_charset() or "utf-8"
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.debug("Fetch failed for %s: %s", url[:80], exc)
        return ""

    try:
        html = raw_bytes.decode(charset, errors="replace")
    except LookupError:
        html = raw_bytes.decode("utf-8", errors="replace")

    html = _SCRIPT_STYLE_RE.sub(" ", html)
    collector = _TextCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception as exc:
        logger.debug("HTML parse failed: %s", exc)
        return ""

    plain = strip_emoji(strip_html(collector.text()))
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    if len(plain) > max_chars:
        plain = truncate(plain, max_chars)
    return plain
