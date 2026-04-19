"""Central configuration loaded from feeds.yaml and environment variables."""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
FEEDS_FILE = ROOT_DIR / "feeds.yaml"
DATA_DIR = ROOT_DIR / "data"
DAYS_DIR = DATA_DIR / "days"
STATE_FILE = DATA_DIR / "state.json"
TEMPLATES_DIR = ROOT_DIR / "templates"
DOCS_DIR = ROOT_DIR / "docs"

_LEGACY_ARTICLES_FILE = DATA_DIR / "articles.json"

SMTP_HOST = os.getenv("SMTP_HOST", "").strip() or "smtp.gmail.com"
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "").strip() or "587")
except ValueError:
    SMTP_PORT = 587
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "").strip()
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "").strip()


def load_feeds_config() -> dict[str, Any]:
    if not FEEDS_FILE.exists():
        raise FileNotFoundError(f"Feed config not found: {FEEDS_FILE}")
    with open(FEEDS_FILE, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if not config or "feeds" not in config:
        raise ValueError("feeds.yaml must contain a 'feeds' key")
    feeds = config["feeds"]
    if not isinstance(feeds, list) or len(feeds) == 0:
        raise ValueError("feeds.yaml 'feeds' must be a non-empty list")
    for feed in feeds:
        if "url" not in feed or "name" not in feed:
            raise ValueError(f"Each feed entry needs 'name' and 'url': {feed}")
    return config


def get_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = {
        "timezone": "UTC",
        "active_days": 7,
        "max_retention_days": 30,
        "max_articles_per_day": 20,
        "max_articles_per_page": 30,
        "email_max_articles": 15,
        "site_title": "SRE Brief",
        "site_description": "Daily operational reliability briefing from engineering sources",
        "site_base_url": "",
        "enrich_fetch_full_article": False,
        "scrape_timeout_seconds": 12,
        "scrape_max_chars": 12000,
        "scrape_max_articles_per_run": 8,
        # Homepage lists articles whose calendar `day` falls in this window (UTC day strings).
        # If that yields zero rows but data exists, generate_site falls back to newest by `published`.
        "homepage_calendar_days": 7,
        "homepage_recency_fallback": True,
    }
    if config is None:
        config = load_feeds_config()
    settings = config.get("settings", {})
    return {**defaults, **(settings or {})}


def get_tag_keywords(config: dict[str, Any] | None = None) -> dict[str, list[str]]:
    if config is None:
        config = load_feeds_config()
    return config.get("tag_keywords", {})


def get_vendor_keywords(config: dict[str, Any] | None = None) -> dict[str, list[str]]:
    if config is None:
        config = load_feeds_config()
    return config.get("vendor_keywords", {})


def get_personalization(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        config = load_feeds_config()
    return config.get("personalization", {})
