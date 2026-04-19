"""Run fetch → process → site generation locally with verbose logging.

Usage (from repository root):
  python -m scripts.verify_pipeline
"""

from __future__ import annotations

import json
import logging
import sys

from scripts.generate_site import generate_site
from scripts.pipeline_stats import load_pipeline_stats
from scripts.process_articles import process


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    print("=== SRE Brief — verify_pipeline ===\n")

    articles = process()
    print(f"\n--- After process(): {len(articles)} articles in memory (merged from disk) ---\n")

    generate_site()
    print("\n--- After generate_site(): static HTML under docs/ ---\n")

    stats = load_pipeline_stats()
    if stats:
        print("--- data/pipeline_stats.json (summary) ---")
        print(json.dumps(stats, indent=2, default=str)[:6000])
        if len(json.dumps(stats)) > 6000:
            print("\n… (truncated; see file for full JSON)\n")
    else:
        print("WARNING: pipeline_stats.json missing (process() should have written it)\n")

    fetch = stats.get("fetch", {}) if stats else {}
    proc = stats.get("process", {}) if stats else {}
    print("=== Counts ===")
    print(f"  feeds configured:     {fetch.get('feeds_configured', '—')}")
    print(f"  feeds OK:             {fetch.get('feeds_ok', '—')}")
    print(f"  raw RSS entries:      {fetch.get('entries_raw_total', '—')}")
    print(f"  parse/link rejects:   {fetch.get('entries_parse_failed', '—')}")
    print(f"  unique after dedup:   {fetch.get('articles_after_dedup', '—')}")
    print(f"  after retention:      {proc.get('incoming_after_retention', '—')}")
    print(f"  new merged this run:  {proc.get('new_articles_this_run', '—')}")
    print(f"  total on disk:        {proc.get('total_articles_on_disk', '—')}")
    print(f"  day JSON files:       {proc.get('day_json_file_count', '—')}")

    if proc.get("warnings"):
        print("\n=== Warnings ===")
        for w in proc["warnings"]:
            print(f"  - {w}")

    if not articles:
        print("\nRESULT: FAILURE — zero articles on disk after pipeline.")
        return 1
    if fetch.get("articles_after_dedup", 0) == 0:
        print("\nNOTE: This fetch returned zero new articles (feeds may be unchanged); disk still has data.")
    print("\nRESULT: OK — open docs/index.html in a browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
