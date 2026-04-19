"""Generate the static GitHub Pages site into docs/."""

from __future__ import annotations

import json
import logging
import shutil
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from scripts.config import (
    DATA_DIR,
    DAYS_DIR,
    DOCS_DIR,
    TEMPLATES_DIR,
    load_feeds_config,
    get_settings,
)
from scripts.enrich import generate_landscape_bullets, extract_top_topics
from scripts.pipeline_stats import load_pipeline_stats
from scripts.utils import (
    load_all_days, list_day_files,
    format_date_human, format_datetime_local, now_utc,
)
from scripts.scheduler import get_local_today, get_timezone, local_now

logger = logging.getLogger(__name__)


def _paragraphs_filter(text: str) -> Markup:
    if not text:
        return Markup("")
    paras = text.strip().split("\n\n")
    html_parts = []
    for p in paras:
        cleaned = p.strip().replace("\n", " ")
        if cleaned:
            html_parts.append(f"<p>{Markup.escape(cleaned)}</p>")
    return Markup("\n".join(html_parts))


def _group_by_day(articles: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        grouped[article["day"]].append(article)
    return sorted(grouped.items(), key=lambda x: x[0], reverse=True)


def _collect_all(articles: list[dict[str, Any]], key: str) -> list[str]:
    counter: Counter = Counter()
    for a in articles:
        counter.update(a.get(key, []))
    return [item for item, _ in counter.most_common()]


def _collect_scalar_field(articles: list[dict[str, Any]], key: str) -> list[str]:
    counter: Counter = Counter()
    for a in articles:
        val = a.get(key)
        if isinstance(val, str) and val.strip():
            counter[val.strip()] += 1
    return [item for item, _ in counter.most_common()]


def _impact_label(slug: str) -> str:
    return {
        "critical": "Critical Incident",
        "high": "High Impact",
        "medium": "Medium",
        "low": "Low / Insight",
    }.get(slug or "", slug or "")


def _published_sort_key(a: dict[str, Any]) -> str:
    return a.get("published", "") or ""


def _setup_jinja(settings: dict[str, Any]) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["site_title"] = settings["site_title"]
    env.globals["site_description"] = settings["site_description"]
    env.globals["site_base_url"] = settings.get("site_base_url", "")
    env.globals["impact_label"] = _impact_label
    env.filters["paragraphs"] = _paragraphs_filter
    env.filters["human_date"] = format_date_human
    tz = get_timezone()
    env.filters["article_time"] = lambda iso_str: format_datetime_local(iso_str, tz)
    return env


def _copy_static_assets() -> None:
    dst = DOCS_DIR / "assets"
    dst.mkdir(parents=True, exist_ok=True)
    for filename in ("style.css", "app.js"):
        src = TEMPLATES_DIR / filename
        if src.exists():
            shutil.copy2(src, dst / filename)


def _write_last_updated(iso_str: str, human: str, timezone_str: str) -> None:
    path = DATA_DIR / "last_updated.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_at_iso": iso_str,
        "updated_at_human": human,
        "timezone": timezone_str,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _cleanup_stale_pages(
    valid_article_ids: set[str], valid_days: set[str]
) -> None:
    articles_dir = DOCS_DIR / "articles"
    if articles_dir.exists():
        deleted = 0
        for f in articles_dir.glob("*.html"):
            if f.stem not in valid_article_ids:
                f.unlink()
                deleted += 1
        if deleted:
            logger.info("Cleaned %d stale article pages", deleted)

    daily_dir = DOCS_DIR / "daily"
    if daily_dir.exists():
        deleted = 0
        for f in daily_dir.glob("*.html"):
            if f.stem not in valid_days:
                f.unlink()
                deleted += 1
        if deleted:
            logger.info("Cleaned %d stale daily pages", deleted)


def generate_site() -> None:
    config = load_feeds_config()
    settings = get_settings(config)
    env = _setup_jinja(settings)

    articles = load_all_days(DAYS_DIR)
    pipeline_diag = load_pipeline_stats()
    homepage_fallback = False

    if not articles:
        logger.warning(
            "No articles found under %s — generating empty site "
            "(see data/pipeline_stats.json after a full hourly run)",
            DAYS_DIR,
        )

    calendar_days = int(settings.get("homepage_calendar_days", settings.get("active_days", 7)))
    allow_fallback = bool(settings.get("homepage_recency_fallback", True))
    max_homepage = settings["max_articles_per_page"]

    active_cutoff = (now_utc() - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    active_articles = [a for a in articles if a.get("day", "") >= active_cutoff]

    if not active_articles and articles and allow_fallback:
        homepage_fallback = True
        logger.warning(
            "Homepage calendar window (%d days, cutoff=%s) excluded all %d stored articles; "
            "using recency fallback (newest by published).",
            calendar_days, active_cutoff, len(articles),
        )
        active_articles = sorted(articles, key=_published_sort_key, reverse=True)[:max_homepage]

    all_days_grouped = _group_by_day(articles)
    today = get_local_today()
    today_human = format_date_human(today)

    now_local = local_now()
    last_updated_human = (
        f"{now_local.day} {now_local.strftime('%B')} {now_local.year}, "
        f"{now_local.strftime('%H:%M')}"
    )
    last_updated_iso = now_local.isoformat()
    _write_last_updated(last_updated_iso, last_updated_human, str(get_timezone()))

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "daily").mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "articles").mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "archive").mkdir(parents=True, exist_ok=True)

    _copy_static_assets()

    all_tags = _collect_all(active_articles, "tags")
    all_vendors = _collect_all(active_articles, "vendors")
    all_themes = _collect_scalar_field(active_articles, "reliability_theme")
    all_impacts: list[str] = []
    for imp in ("critical", "high", "medium", "low"):
        if any(a.get("impact") == imp for a in active_articles):
            all_impacts.append(imp)

    todays_articles = [a for a in articles if a.get("day") == today]
    if not todays_articles and all_days_grouped:
        todays_articles = all_days_grouped[0][1]

    impact_counts: dict[str, int] = {}
    priority_count = 0
    for a in todays_articles:
        imp = a.get("impact")
        if imp:
            impact_counts[imp] = impact_counts.get(imp, 0) + 1
        if a.get("operational_priority"):
            priority_count += 1

    tz_abbr = now_local.strftime("%Z") or str(get_timezone())

    landscape_bullets = generate_landscape_bullets(todays_articles)

    cutoff_7d = (now_utc() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_articles = [a for a in articles if a.get("day", "") >= cutoff_7d]
    top_topics = extract_top_topics(week_articles if week_articles else articles)

    homepage_articles = active_articles[:max_homepage]
    homepage_days = _group_by_day(homepage_articles)

    logger.info(
        "SITE: total_stored=%d | homepage_calendar_cutoff=%s | homepage_rows=%d | fallback=%s",
        len(articles), active_cutoff, len(homepage_articles), homepage_fallback,
    )

    index_tpl = env.get_template("index.html")
    index_html = index_tpl.render(
        prefix="",
        articles=homepage_articles,
        days_grouped=homepage_days,
        generated_at=today,
        generated_at_human=today_human,
        last_updated_human=last_updated_human,
        last_updated_iso=last_updated_iso,
        timezone_abbr=tz_abbr,
        impact_counts=impact_counts,
        priority_count=priority_count,
        total_today=len(todays_articles),
        landscape_bullets=landscape_bullets,
        top_topics=top_topics,
        all_tags=all_tags,
        all_vendors=all_vendors,
        all_themes=all_themes,
        all_impacts=all_impacts,
        pipeline_diag=pipeline_diag,
        homepage_calendar_days=calendar_days,
        homepage_calendar_cutoff=active_cutoff,
        homepage_fallback=homepage_fallback,
        total_stored=len(articles),
    )
    (DOCS_DIR / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("Generated docs/index.html with %d homepage rows", len(homepage_articles))

    day_tpl = env.get_template("day.html")
    for day_str, day_articles in all_days_grouped:
        day_tags = _collect_all(day_articles, "tags")
        day_vendors = _collect_all(day_articles, "vendors")
        day_themes = _collect_scalar_field(day_articles, "reliability_theme")
        day_impacts = []
        for imp in ("critical", "high", "medium", "low"):
            if any(a.get("impact") == imp for a in day_articles):
                day_impacts.append(imp)
        day_bullets = generate_landscape_bullets(day_articles)

        day_html = day_tpl.render(
            prefix="../",
            day=day_str,
            day_human=format_date_human(day_str),
            articles=day_articles,
            landscape_bullets=day_bullets,
            all_tags=day_tags,
            all_vendors=day_vendors,
            all_themes=day_themes,
            all_impacts=day_impacts,
        )
        (DOCS_DIR / "daily" / f"{day_str}.html").write_text(day_html, encoding="utf-8")
    logger.info("Generated %d daily pages", len(all_days_grouped))

    article_tpl = env.get_template("article.html")
    for article in articles:
        sections = article.get("sections", {})
        art_html = article_tpl.render(
            prefix="../",
            article=article,
            sections=sections,
        )
        (DOCS_DIR / "articles" / f"{article['id']}.html").write_text(art_html, encoding="utf-8")
    logger.info("Generated %d individual article pages", len(articles))

    archive_tpl = env.get_template("archive_index.html")
    day_summaries = []
    for day_str, day_articles in all_days_grouped:
        tag_counter: Counter = Counter()
        imp_counter: Counter = Counter()
        for a in day_articles:
            tag_counter.update(a.get("tags", []))
            im = a.get("impact")
            if im:
                imp_counter[im] += 1
        top_tags = [t for t, _ in tag_counter.most_common(4)]
        top_impact = imp_counter.most_common(1)[0][0] if imp_counter else None
        day_summaries.append({
            "day": day_str,
            "day_human": format_date_human(day_str),
            "count": len(day_articles),
            "top_tags": top_tags,
            "top_impact": top_impact,
        })

    archive_html = archive_tpl.render(prefix="../", days=day_summaries)
    (DOCS_DIR / "archive" / "index.html").write_text(archive_html, encoding="utf-8")
    logger.info("Generated docs/archive/index.html with %d days", len(day_summaries))

    valid_ids = {a["id"] for a in articles}
    valid_days = {day_str for day_str, _ in all_days_grouped}
    _cleanup_stale_pages(valid_ids, valid_days)

    nojekyll = DOCS_DIR / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.write_text("", encoding="utf-8")

    logger.info("Site generation complete")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    generate_site()


if __name__ == "__main__":
    sys.exit(main() or 0)
