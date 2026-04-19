"""Daily email pipeline: timezone gate, single send per local day, state commit."""

from __future__ import annotations

import logging
import os
import sys

from scripts.scheduler import (
    should_run,
    should_send_email,
    mark_run_complete,
    mark_email_sent,
)
from scripts.send_email import send_email_now

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    force = os.getenv("FORCE_RUN", "").lower() in ("1", "true", "yes")

    run_ok, run_reason = should_run(force=force)
    logger.info("Schedule: %s -- %s", "RUN" if run_ok else "SKIP", run_reason)

    if not run_ok:
        return

    email_ok, email_reason = should_send_email()
    if email_ok:
        logger.info("Email: sending daily briefing...")
        success = send_email_now()
        if success:
            mark_email_sent()
            mark_run_complete()
        else:
            logger.warning("Email: send failed — will retry next eligible trigger")
    else:
        logger.info("Email: %s", email_reason)
        mark_run_complete()

    logger.info("Daily email pipeline finished")


if __name__ == "__main__":
    sys.exit(main() or 0)
