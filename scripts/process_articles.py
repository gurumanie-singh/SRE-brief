"""Incremental article processing with per-day JSON storage.

Lifecycle:
  0-7 days   -> active (homepage)
  7-30 days  -> archive
  >30 days   -> pruned from repository data files
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import timedelta
from typing import Any

from scripts.config import (
    DAYS_DIR,
    _LEGACY_ARTICLES_FILE,
    load_feeds_config,
    get_settings,
    get_vendor_keywords,
    get_personalization,
)
from scripts.enrich import enrich_article, group_articles, classify_impact, validate_impact_distribution
from scripts.fetch_feeds import fetch_all_feeds
from scripts.utils import (
    load_day, save_day, list_day_files, load_json,
    now_utc,
)

logger = logging.getLogger(__name__)

def _migrate_legacy(max_retention_days: int) -> None:
    if not _LEGACY_ARTICLES_FILE.exists():
        return

    logger.info("Migrating legacy articles.json to per-day files...")
    articles = load_json(_LEGACY_ARTICLES_FILE)
    if not articles:
        _LEGACY_ARTICLES_FILE.unlink(missing_ok=True)
        return

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in articles:
        a.pop("full_content", None)
        by_day[a["day"]].append(a)

    cutoff = (now_utc() - timedelta(days=max_retention_days)).strftime("%Y-%m-%d")
    written = 0
    for day_str, day_articles in by_day.items():
        if day_str < cutoff:
            continue
        save_day(DAYS_DIR, day_str, day_articles)
        written += 1

    _LEGACY_ARTICLES_FILE.unlink(missing_ok=True)
    logger.info("Migration complete: %d day files written, legacy file removed", written)


def _strip_for_storage(article: dict[str, Any]) -> dict[str, Any]:
    article.pop("full_content", None)
    return article


def cleanup_old_days(max_days: int) -> int:
    cutoff = (now_utc() - timedelta(days=max_days)).strftime("%Y-%m-%d")
    deleted = 0
    for day_str, path in list_day_files(DAYS_DIR):
        if day_str < cutoff:
            path.unlink()
            deleted += 1
            logger.info("Deleted expired day file %s", path.name)
    return deleted


def process() -> list[dict[str, Any]]:
    config = load_feeds_config()
    settings = get_settings(config)
    vendor_kw = get_vendor_keywords(config)
    personalization = get_personalization(config)
    max_retention = settings.get("max_retention_days", 30)
    max_per_day = settings.get("max_articles_per_day", 20)

    _migrate_legacy(max_retention)

    incoming = fetch_all_feeds()

    retention_cutoff = (now_utc() - timedelta(days=max_retention)).strftime("%Y-%m-%d")
    incoming_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in incoming:
        if a["day"] >= retention_cutoff:
            incoming_by_day[a["day"]].append(a)

    total_new = 0
    total_stored = 0
    days_written = 0

    for day_str, day_incoming in sorted(incoming_by_day.items()):
        existing = load_day(DAYS_DIR, day_str)
        existing_ids = {a["id"] for a in existing}

        new_articles: list[dict[str, Any]] = []
        for article in day_incoming:
            if article["id"] in existing_ids:
                continue
            try:
                enriched = enrich_article(article, vendor_kw, personalization)
                new_articles.append(_strip_for_storage(enriched))
            except Exception as exc:
                logger.warning("Enrichment failed for '%s': %s", article.get("title", "?"), exc)
                if not article.get("impact"):
                    text = f"{article.get('title', '')} {article.get('summary', '')}"
                    article["impact"] = classify_impact(text)
                new_articles.append(_strip_for_storage(article))

        if not new_articles:
            patched = 0
            for a in existing:
                if not a.get("impact"):
                    text = f"{a.get('title', '')} {a.get('summary', '')}"
                    a["impact"] = classify_impact(text)
                    patched += 1
            if patched:
                save_day(DAYS_DIR, day_str, existing)
                logger.info("Day %s: backfilled impact on %d existing articles", day_str, patched)
            total_stored += len(existing)
            continue

        total_new += len(new_articles)
        merged = existing + new_articles

        for a in merged:
            if not a.get("impact"):
                text = f"{a.get('title', '')} {a.get('summary', '')}"
                a["impact"] = classify_impact(text)

        merged = group_articles(merged)
        merged.sort(key=lambda a: a.get("published", ""), reverse=True)
        if len(merged) > max_per_day:
            logger.info("Day %s: capped from %d to %d articles", day_str, len(merged), max_per_day)
            merged = merged[:max_per_day]

        save_day(DAYS_DIR, day_str, merged)
        days_written += 1
        total_stored += len(merged)

    logger.info(
        "Processing complete: %d new articles across %d day files (%d total stored)",
        total_new, days_written, total_stored,
    )

    total_patched = 0
    for day_str, path in list_day_files(DAYS_DIR):
        day_articles = load_day(DAYS_DIR, day_str)
        patched = 0
        for a in day_articles:
            if not a.get("impact"):
                text = f"{a.get('title', '')} {a.get('summary', '')}"
                a["impact"] = classify_impact(text)
                patched += 1
        if patched:
            save_day(DAYS_DIR, day_str, day_articles)
            total_patched += patched
    if total_patched:
        logger.info("Impact backfill: classified %d articles with blank impact", total_patched)

    deleted = cleanup_old_days(max_retention)
    if deleted:
        logger.info("Lifecycle: removed %d day files older than %d days", deleted, max_retention)

    all_current = _load_all_current()
    validate_impact_distribution(all_current)

    from scripts.config import DATA_DIR
    archive_dir = DATA_DIR / "archive"
    if archive_dir.exists():
        for old_file in archive_dir.glob("*.json"):
            old_file.unlink()

    return all_current


def _load_all_current() -> list[dict[str, Any]]:
    from scripts.utils import load_all_days
    return load_all_days(DAYS_DIR)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    articles = process()
    print(f"Processed {len(articles)} articles total")
    enriched = sum(1 for a in articles if a.get("sections"))
    grouped = sum(1 for a in articles if a.get("related_sources"))
    priority = sum(1 for a in articles if a.get("operational_priority"))
    days = len(set(a["day"] for a in articles))
    print(f"  {days} day files")
    print(f"  {enriched} with enriched sections")
    print(f"  {grouped} with related sources (grouped)")
    print(f"  {priority} flagged for operational priority")


if __name__ == "__main__":
    sys.exit(main() or 0)
