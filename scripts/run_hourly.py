"""Hourly site update: fetch, process, regenerate static site (no email)."""

from __future__ import annotations

import json
import logging
import sys

from scripts.generate_site import generate_site
from scripts.pipeline_stats import load_pipeline_stats
from scripts.process_articles import process
from scripts.scheduler import get_timezone, local_now

logger = logging.getLogger(__name__)


def _print_run_summary() -> None:
    stats = load_pipeline_stats()
    if not stats:
        logger.warning("No pipeline_stats.json — did process() complete?")
        return
    fetch = stats.get("fetch", {})
    proc = stats.get("process", {})
    logger.info(
        "RUN SUMMARY | feeds %s/%s OK | raw_entries=%s | deduped=%s | "
        "after_retention=%s | new_merged=%s | on_disk=%s | day_files=%s",
        fetch.get("feeds_ok"),
        fetch.get("feeds_configured"),
        fetch.get("entries_raw_total"),
        fetch.get("articles_after_dedup"),
        proc.get("incoming_after_retention"),
        proc.get("new_articles_this_run"),
        proc.get("total_articles_on_disk"),
        proc.get("day_json_file_count"),
    )
    try:
        print("\n--- pipeline summary ---")
        print(json.dumps({"fetch": fetch, "process": proc}, indent=2, default=str)[:8000])
    except Exception:
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    tz = get_timezone()
    now = local_now()
    logger.info(
        "Hourly update: %s local (%s)", now.strftime("%Y-%m-%d %H:%M"), tz,
    )

    articles = process()
    logger.info("Processed: %d articles total on disk after merge", len(articles))

    generate_site()

    _print_run_summary()

    logger.info("Hourly site update complete")


if __name__ == "__main__":
    sys.exit(main() or 0)
