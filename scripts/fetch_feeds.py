"""Fetch articles from configured RSS feeds with optional full-page text enrichment."""

from __future__ import annotations

import logging
import socket
import sys
from typing import Any

import feedparser

_FEED_TIMEOUT_SECONDS = 30

# Some CDNs block the default feedparser UA; use a descriptive browser-like string.
feedparser.USER_AGENT = (
    "Mozilla/5.0 (compatible; SRE-Brief/2.1; +https://github.com; "
    "engineering RSS reader; like FeedFetcher-Google)"
)

from scripts.config import load_feeds_config, get_tag_keywords, get_settings
from scripts.content_extract import fetch_article_text
from scripts.utils import (
    article_id,
    is_safe_url,
    parse_date,
    strip_html,
    strip_emoji,
    truncate,
    now_utc,
)

logger = logging.getLogger(__name__)


def _entry_link(entry: Any) -> str:
    """Resolve entry URL from RSS or Atom shapes (string, dict, links list)."""
    raw = entry.get("link")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().split()[0]
    if isinstance(raw, dict):
        href = raw.get("href")
        if href and str(href).strip():
            return str(href).strip()
    id_val = entry.get("id")
    if isinstance(id_val, str) and id_val.strip().startswith(("http://", "https://")):
        return id_val.strip()
    links = getattr(entry, "links", None) or entry.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            href = link.get("href")
            if not href:
                continue
            rel = str(link.get("rel") or "alternate").lower()
            if rel in ("alternate", "self", "related"):
                return str(href).strip()
    return ""


def _apply_tags(text: str, tag_keywords: dict[str, list[str]]) -> list[str]:
    lower = text.lower()
    return sorted({
        tag for tag, keywords in tag_keywords.items()
        if any(kw in lower for kw in keywords)
    })


def _extract_full_content(entry: Any) -> str:
    if hasattr(entry, "content") and entry.content:
        for c in entry.content:
            if c.get("type", "") in ("text/html", "text/plain", "application/xhtml+xml"):
                return strip_emoji(strip_html(c.get("value", "")))
        first_val = entry.content[0].get("value", "") if entry.content else ""
        if first_val:
            return strip_emoji(strip_html(first_val))

    raw = entry.get("summary") or entry.get("description") or ""
    return strip_emoji(strip_html(raw))


def _parse_entry(
    entry: Any, source_name: str, tag_keywords: dict[str, list[str]]
) -> dict[str, Any] | None:
    title = strip_emoji((entry.get("title") or "").strip())
    link = _entry_link(entry)
    if not title or not link:
        logger.debug("Skip entry in %s: missing title or link (title=%r link=%r)", source_name, title[:40], link[:40])
        return None
    if not is_safe_url(link):
        logger.warning("Rejected unsafe URL scheme in '%s': %s", title[:60], link[:80])
        return None

    full_content = _extract_full_content(entry)
    summary = truncate(
        full_content.split("\n")[0] if full_content else title,
        400,
    )

    published_raw = entry.get("published") or entry.get("updated") or entry.get("created") or ""
    published_dt = parse_date(published_raw)

    searchable = f"{title} {full_content}"
    tags = _apply_tags(searchable, tag_keywords)

    return {
        "id": article_id(title, link),
        "title": title,
        "source": source_name,
        "link": link,
        "published": published_dt.isoformat(),
        "fetched_at": now_utc().isoformat(),
        "summary": summary,
        "full_content": full_content,
        "tags": tags,
        "day": published_dt.strftime("%Y-%m-%d"),
    }


def fetch_all_feeds() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (articles, stats) for logging and pipeline_stats.json."""
    config = load_feeds_config()
    tag_keywords = get_tag_keywords(config)
    settings = get_settings(config)
    feeds = config["feeds"]

    enrich_fetch = bool(settings.get("enrich_fetch_full_article"))
    scrape_timeout = float(settings.get("scrape_timeout_seconds", 12))
    scrape_max_chars = int(settings.get("scrape_max_chars", 12000))
    scrape_cap = int(settings.get("scrape_max_articles_per_run", 8))

    articles: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    scraped = 0
    feed_rows: list[dict[str, Any]] = []
    entries_seen_total = 0
    entries_parse_failed = 0

    logger.info("Feed ingestion: %d feed URLs configured", len(feeds))

    for feed_cfg in feeds:
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        row: dict[str, Any] = {
            "name": name,
            "url": url,
            "ok": False,
            "entries_raw": 0,
            "entries_accepted": 0,
            "error": None,
        }
        logger.info("Fetching feed: %s (%s)", name, url)

        prev_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(_FEED_TIMEOUT_SECONDS)
            parsed = feedparser.parse(url)
        except Exception as exc:
            row["error"] = str(exc)
            logger.error("Failed to fetch %s: %s", name, exc)
            feed_rows.append(row)
            continue
        finally:
            socket.setdefaulttimeout(prev_timeout)

        raw_entries = list(parsed.entries or [])
        row["entries_raw"] = len(raw_entries)

        if not raw_entries:
            row["error"] = (
                f"zero entries (bozo={bool(parsed.bozo)} "
                f"{getattr(parsed, 'bozo_exception', '')!s})"
            )
            logger.warning("Feed %s returned no entries — %s", name, row["error"])
            feed_rows.append(row)
            continue

        if parsed.bozo:
            logger.warning(
                "Feed %s is bozo/malformed but has %d entries — continuing (%s)",
                name, len(raw_entries), getattr(parsed, "bozo_exception", ""),
            )

        row["ok"] = True
        count = 0
        for entry in raw_entries:
            entries_seen_total += 1
            try:
                article = _parse_entry(entry, name, tag_keywords)
            except Exception as exc:
                entries_parse_failed += 1
                logger.warning("Skipping bad entry in %s: %s", name, exc)
                continue

            if article is None:
                entries_parse_failed += 1
                continue
            if article["id"] in seen_ids:
                continue

            if enrich_fetch and scraped < scrape_cap:
                extra = fetch_article_text(article["link"], scrape_timeout, scrape_max_chars)
                if extra and len(extra) > len(article.get("full_content", "")):
                    article["full_content"] = extra[:scrape_max_chars]
                    article["content_enriched"] = True
                    scraped += 1
                elif enrich_fetch:
                    article.setdefault("content_enriched", False)

            seen_ids.add(article["id"])
            articles.append(article)
            count += 1

        row["entries_accepted"] = count
        logger.info("  → %d accepted articles from %s (raw entries=%d)", count, name, len(raw_entries))
        feed_rows.append(row)

    if enrich_fetch:
        logger.info("Article page enrichment: fetched %d pages this run", scraped)

    articles.sort(key=lambda a: a["published"], reverse=True)

    feeds_ok = sum(1 for r in feed_rows if r.get("ok"))
    stats: dict[str, Any] = {
        "feeds_configured": len(feeds),
        "feeds_attempted": len(feed_rows),
        "feeds_ok": feeds_ok,
        "entries_raw_total": entries_seen_total,
        "entries_parse_failed": entries_parse_failed,
        "articles_after_dedup": len(articles),
        "feeds_detail": feed_rows,
    }
    logger.info(
        "Fetch summary: %d/%d feeds OK | %d raw entries | %d parse rejects | %d unique articles",
        feeds_ok, len(feeds), entries_seen_total, entries_parse_failed, len(articles),
    )
    return articles, stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    articles, stats = fetch_all_feeds()
    print(f"Fetched {len(articles)} unique articles")
    print(
        f"  feeds OK: {stats['feeds_ok']}/{stats['feeds_configured']} | "
        f"raw entries: {stats['entries_raw_total']} | "
        f"parse failed: {stats['entries_parse_failed']}"
    )
    for a in articles[:5]:
        print(f"  [{a['day']}] {a['title'][:80]}")


if __name__ == "__main__":
    sys.exit(main() or 0)
