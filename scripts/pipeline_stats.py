"""Write and read pipeline diagnostics for the static site and operators."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.config import DATA_DIR

logger = logging.getLogger(__name__)

STATS_FILE = DATA_DIR / "pipeline_stats.json"


def write_pipeline_stats(payload: dict[str, Any]) -> None:
    """Atomically persist pipeline stats for the site empty-state and CI logs."""
    out = dict(payload)
    out["written_at"] = datetime.now(timezone.utc).isoformat()
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")
    tmp.replace(STATS_FILE)
    logger.info("Pipeline stats written to %s", STATS_FILE)


def load_pipeline_stats() -> dict[str, Any]:
    if not STATS_FILE.exists():
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read pipeline stats: %s", exc)
        return {}
