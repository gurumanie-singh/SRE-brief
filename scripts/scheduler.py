"""Timezone-aware scheduling gate and persistent state management."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scripts.config import STATE_FILE, load_feeds_config

logger = logging.getLogger(__name__)
EXECUTION_HOUR = 7


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt state.json (%s) — starting fresh", exc)
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, default=str, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(STATE_FILE)


def get_timezone() -> ZoneInfo:
    try:
        config = load_feeds_config()
        tz_str = config.get("settings", {}).get("timezone", "UTC")
        if not tz_str:
            tz_str = "UTC"
        return ZoneInfo(tz_str)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid timezone '%s' in feeds.yaml — using UTC", tz_str)
        return ZoneInfo("UTC")
    except Exception:
        return ZoneInfo("UTC")


def local_now() -> datetime:
    return datetime.now(get_timezone())


def get_local_today() -> str:
    return local_now().strftime("%Y-%m-%d")


def should_run(force: bool = False) -> tuple[bool, str]:
    tz = get_timezone()
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    state = load_state()
    last_run = state.get("last_run_date", "")

    if force:
        return True, f"Manual trigger for {today} at {current_time} {tz}"

    if last_run == today:
        return False, f"Already ran today ({today} {tz})"

    if now.hour < EXECUTION_HOUR:
        return False, (
            f"Too early (now {current_time} {tz}, "
            f"waiting for {EXECUTION_HOUR:02d}:00)"
        )

    return True, f"Executing for {today} at {current_time} {tz}"


def should_send_email() -> tuple[bool, str]:
    today = get_local_today()
    state = load_state()
    last_email = state.get("last_email_date", "")
    if last_email == today:
        return False, f"Email already sent for {today}"
    return True, f"Email not yet sent for {today}"


def mark_run_complete() -> None:
    now = local_now()
    state = load_state()
    state["last_run_date"] = now.strftime("%Y-%m-%d")
    state["last_run_iso"] = now.isoformat()
    state["timezone"] = str(get_timezone())
    save_state(state)
    logger.info("State: marked run complete for %s", now.strftime("%Y-%m-%d"))


def mark_email_sent() -> None:
    now = local_now()
    state = load_state()
    state["last_email_date"] = now.strftime("%Y-%m-%d")
    state["last_email_iso"] = now.isoformat()
    save_state(state)
    logger.info("State: marked email sent for %s", now.strftime("%Y-%m-%d"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    tz = get_timezone()
    now = datetime.now(tz)
    state = load_state()

    print(f"Timezone:       {tz}")
    print(f"Local time:     {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Exec window:    {EXECUTION_HOUR:02d}:00 - {EXECUTION_HOUR:02d}:59")
    print(f"Last run date:  {state.get('last_run_date', '(never)')}")
    print(f"Last email date:{state.get('last_email_date', '(never)')}")

    run_ok, run_reason = should_run()
    email_ok, email_reason = should_send_email()
    print(f"Should run:     {'YES' if run_ok else 'NO'} — {run_reason}")
    print(f"Should email:   {'YES' if email_ok else 'NO'} — {email_reason}")


if __name__ == "__main__":
    sys.exit(main() or 0)
