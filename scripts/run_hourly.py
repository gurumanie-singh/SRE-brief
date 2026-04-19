"""Hourly site update: fetch, process, regenerate static site (no email)."""

from __future__ import annotations

import logging
import sys

from scripts.process_articles import process
from scripts.generate_site import generate_site
from scripts.scheduler import get_timezone, local_now

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    tz = get_timezone()
    now = local_now()
    logger.info(
        "Hourly update: %s local (%s)", now.strftime("%Y-%m-%d %H:%M"), tz,
    )

    articles = process()
    logger.info("Processed: %d articles total", len(articles))

    generate_site()

    logger.info("Hourly site update complete")


if __name__ == "__main__":
    sys.exit(main() or 0)
