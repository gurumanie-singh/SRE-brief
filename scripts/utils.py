"""Shared utility functions: hashing, date helpers, JSON I/O, text cleaning."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0000FE0F"
    "\U0000200D"
    "\U00002139"
    "\U000025C7"
    "\U00002B50"
    "]+",
    re.UNICODE,
)

_SAFE_URL_SCHEMES = frozenset({"http", "https"})


def strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def is_safe_url(url: str) -> bool:
    try:
        return urlparse(url).scheme.lower() in _SAFE_URL_SCHEMES
    except Exception:
        return False


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    return clean.lower()


def article_id(title: str, link: str) -> str:
    key = normalize_text(title) + "|" + normalize_url(link)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_MAP = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&apos;": "'", "&nbsp;": " ",
    "&#39;": "'", "&#x27;": "'", "&#34;": '"',
}
_ENTITY_RE = re.compile(r"&[#\w]+;")


def _replace_entity(match: re.Match) -> str:
    return _ENTITY_MAP.get(match.group(0), " ")


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = _ENTITY_RE.sub(_replace_entity, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, max_chars: int = 280) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[: max_chars].rsplit(" ", 1)[0]
    return truncated.rstrip(".,;:!?") + "..."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return now_utc().strftime("%Y-%m-%d")


def format_date_human(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.day} {dt.strftime('%B')} {dt.year}"
    except (ValueError, AttributeError):
        return date_str


def format_datetime_local(iso_str: str, tz: Any) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_local = dt.astimezone(tz)
        return f"{dt_local.day} {dt_local.strftime('%B')} {dt_local.year}, {dt_local.strftime('%H:%M')}"
    except (ValueError, AttributeError, TypeError):
        return iso_str


def parse_date(date_str: str | None) -> datetime:
    if not date_str:
        return now_utc()
    from dateutil import parser as dateutil_parser
    try:
        dt = dateutil_parser.parse(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, OverflowError):
        logger.warning("Unparseable date '%s', using now", date_str)
        return now_utc()


def _day_path(days_dir: Path, day_str: str) -> Path:
    return days_dir / f"{day_str}.json"


def load_day(days_dir: Path, day_str: str) -> list[dict[str, Any]]:
    path = _day_path(days_dir, day_str)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return []


def save_day(days_dir: Path, day_str: str, articles: list[dict[str, Any]]) -> None:
    days_dir.mkdir(parents=True, exist_ok=True)
    path = _day_path(days_dir, day_str)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(articles, fh, indent=2, default=str, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)


def list_day_files(days_dir: Path) -> list[tuple[str, Path]]:
    if not days_dir.exists():
        return []
    pairs = []
    for p in days_dir.glob("*.json"):
        day_str = p.stem
        if len(day_str) == 10 and day_str[4] == "-" and day_str[7] == "-":
            pairs.append((day_str, p))
    pairs.sort(key=lambda x: x[0], reverse=True)
    return pairs


def load_all_days(days_dir: Path) -> list[dict[str, Any]]:
    all_articles: list[dict[str, Any]] = []
    for day_str, path in list_day_files(days_dir):
        all_articles.extend(load_day(days_dir, day_str))
    all_articles.sort(key=lambda a: a.get("published", ""), reverse=True)
    return all_articles


def load_days_range(
    days_dir: Path, start_date: str, end_date: str | None = None
) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    for day_str, path in list_day_files(days_dir):
        if day_str < start_date:
            continue
        if end_date and day_str > end_date:
            continue
        articles.extend(load_day(days_dir, day_str))
    articles.sort(key=lambda a: a.get("published", ""), reverse=True)
    return articles


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return []


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)
