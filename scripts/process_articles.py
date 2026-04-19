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
from datetime import timedelta, datetime, timezone
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
from scripts.pipeline_stats import write_pipeline_stats
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


def _published_dt(article: dict[str, Any]) -> datetime:
    try:
        dt = datetime.fromisoformat(article.get("published", "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return now_utc()


def process() -> list[dict[str, Any]]:
    config = load_feeds_config()
    settings = get_settings(config)
    vendor_kw = get_vendor_keywords(config)
    personalization = get_personalization(config)
    max_retention = settings.get("max_retention_days", 30)
    max_per_day = settings.get("max_articles_per_day", 20)

    _migrate_legacy(max_retention)

    incoming, fetch_stats = fetch_all_feeds()

    retention_cutoff = (now_utc() - timedelta(days=max_retention)).strftime("%Y-%m-%d")
    retention_start_dt = now_utc() - timedelta(days=max_retention)

    incoming_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dropped_retention = 0
    for a in incoming:
        pub = _published_dt(a)
        if pub >= retention_start_dt:
            incoming_by_day[a["day"]].append(a)
        else:
            dropped_retention += 1

    after_retention = sum(len(v) for v in incoming_by_day.values())

    if incoming and after_retention == 0:
        logger.error(
            "PIPELINE: all %d fetched articles were outside the %d-day retention window "
            "(calendar day cutoff=%s). Nothing will be stored.",
            len(incoming), max_retention, retention_cutoff,
        )

    total_new = 0
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

    logger.info(
        "Processing complete: %d new articles merged across %d day files touched this run",
        total_new, days_written,
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

    day_files = list_day_files(DAYS_DIR)
    warnings: list[str] = []
    if fetch_stats.get("articles_after_dedup", 0) == 0:
        warnings.append("Fetch returned zero usable articles — check feeds, network, or RSS parse errors.")
    if incoming and after_retention == 0:
        warnings.append(
            f"All {len(incoming)} items were older than the {max_retention}-day retention window."
        )
    if incoming and total_new == 0 and after_retention > 0:
        warnings.append(
            "Incoming articles matched existing IDs only (no new rows merged this run)."
        )

    process_stats = {
        "retention_max_days": max_retention,
        "retention_cutoff_day": retention_cutoff,
        "incoming_total": len(incoming),
        "incoming_after_retention": after_retention,
        "dropped_by_retention": dropped_retention,
        "new_articles_this_run": total_new,
        "day_files_touched": days_written,
        "total_articles_on_disk": len(all_current),
        "day_json_file_count": len(day_files),
        "warnings": warnings,
    }

    logger.info(
        "STORE: retention_cutoff=%s | after_retention=%d | new_merged=%d | "
        "total_on_disk=%d | day_json_files=%d",
        retention_cutoff, after_retention, total_new, len(all_current), len(day_files),
    )

    write_pipeline_stats({"fetch": fetch_stats, "process": process_stats})

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
    print(f"  {days} calendar days with data")
    print(f"  {enriched} with enriched sections")
    print(f"  {grouped} with related sources (grouped)")
    print(f"  {priority} flagged for operational priority")


if __name__ == "__main__":
    sys.exit(main() or 0)
