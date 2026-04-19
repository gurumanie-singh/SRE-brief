"""SRE-focused enrichment: impact classification, root-cause hints, structured sections.

Deterministic heuristics only — no external AI APIs.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

_IMPACT_SIGNALS: list[tuple[str, int]] = [
    ("multi-region failure", 22),
    ("multi region", 18),
    ("widespread failure", 20),
    ("production outage", 20),
    ("major outage", 20),
    ("service unavailable", 18),
    ("complete outage", 18),
    ("sev-1", 18),
    ("sev 1", 18),
    ("severity 1", 16),
    ("severity-1", 16),
    ("p0 incident", 18),
    ("p0 ", 14),
    ("global outage", 20),
    ("data plane outage", 16),
    ("control plane outage", 16),
    ("downtime", 12),
    ("outage", 10),
    ("major incident", 16),
    ("severe production", 16),
    ("cascading failure", 14),
    ("cascading failures", 14),

    ("partial outage", 12),
    ("significant degradation", 12),
    ("elevated errors", 10),
    ("error budget", 8),
    ("dependency failure", 12),
    ("upstream failure", 10),
    ("failover", 8),
    ("failed over", 8),
    ("control plane issue", 12),
    ("scaling issue", 10),
    ("capacity issue", 10),
    ("overload", 10),
    ("retry storm", 10),
    ("incident", 6),
    ("postmortem", 4),
    ("post-mortem", 4),
    ("root cause", 4),

    ("latency", 5),
    ("slowdown", 5),
    ("performance degradation", 7),
    ("performance issue", 6),
    ("degraded performance", 7),
    ("timeouts", 5),
    ("timeout spike", 7),
    ("queue backlog", 7),
    ("minor disruption", 5),
    ("error spike", 6),
    ("5xx", 6),
    ("503", 6),
    ("504", 6),

    ("best practices", 2),
    ("lessons learned", 2),
    ("architecture", 2),
    ("reliability pattern", 3),
    ("observability", 2),
    ("monitoring", 1),
    ("tracing", 1),
    ("sre ", 1),
    ("site reliability", 1),
    ("how we", 1),
    ("deep dive", 1),
    ("case study", 1),
]

_EXPLICIT_IMPACT_MARKERS: list[tuple[str, str]] = [
    ("critical incident", "critical"),
    ("high impact", "high"),
    ("medium impact", "medium"),
    ("low impact", "low"),
]

_SCORE_CRITICAL = 16
_SCORE_HIGH = 8
_SCORE_MEDIUM = 2
_DEFAULT_IMPACT = "medium"


def _score_impact_text(text: str) -> tuple[int, list[tuple[str, int]]]:
    lower = text.lower()
    matched: list[tuple[str, int]] = []
    for phrase, weight in _IMPACT_SIGNALS:
        if phrase in lower:
            matched.append((phrase, weight))
    total = sum(w for _, w in matched)
    for phrase, band in _EXPLICIT_IMPACT_MARKERS:
        if phrase in lower:
            matched.append((phrase, 25))
            if band == "critical":
                total = max(total, _SCORE_CRITICAL + 2)
            elif band == "high":
                total = max(total, _SCORE_HIGH + 2)
            elif band == "medium":
                total = max(total, _SCORE_MEDIUM + 1)
            else:
                total = max(total, 1)
    return total, matched


def classify_impact(text: str) -> str:
    """Return one of critical, high, medium, low. Always returns a value."""
    score, matched = _score_impact_text(text)

    low_signal_hits = sum(
        1 for p, _ in matched
        if any(
            x in p
            for x in (
                "best practices", "lessons learned", "architecture",
                "reliability pattern", "observability", "deep dive", "case study",
            )
        )
    )
    incident_signal_hits = sum(
        1 for p, _ in matched
        if any(
            x in p
            for x in (
                "outage", "downtime", "incident", "degradation", "failure",
                "503", "504", "5xx", "latency", "overload",
            )
        )
    )

    if score >= _SCORE_CRITICAL:
        impact = "critical"
    elif score >= _SCORE_HIGH:
        impact = "high"
    elif score >= _SCORE_MEDIUM:
        impact = "medium"
    elif score > 0:
        if low_signal_hits >= 2 and incident_signal_hits == 0:
            impact = "low"
        else:
            impact = "medium"
    else:
        impact = _DEFAULT_IMPACT

    if matched:
        top = sorted(matched, key=lambda x: x[1], reverse=True)[:4]
        top_str = ", ".join(f"{p}({w})" for p, w in top)
        logger.debug("Impact score=%d → %s [%s]", score, impact, top_str)
    else:
        logger.debug("Impact score=0 → %s (fallback)", impact)

    return impact


def validate_impact_distribution(articles: list[dict[str, Any]]) -> None:
    from collections import Counter as C
    dist: C[str] = C()
    blank = 0
    for a in articles:
        imp = a.get("impact")
        if not imp:
            blank += 1
        else:
            dist[imp] += 1
    total = len(articles)
    if not total:
        return
    parts = [f"{s}={c}" for s, c in sorted(dist.items(), key=lambda x: x[1], reverse=True)]
    logger.info("Impact distribution (%d articles): %s", total, ", ".join(parts))
    if blank:
        logger.error("IMPACT BUG: %d/%d articles have blank impact", blank, total)
    if total >= 10:
        for imp, count in dist.items():
            pct = count / total * 100
            if pct > 85:
                logger.warning(
                    "Impact skew: %s is %.0f%% (%d/%d) — review classification rules",
                    imp, pct, count, total,
                )


_ROOT_CAUSE_SIGNALS: list[tuple[str, str]] = [
    ("misconfiguration", "misconfiguration"),
    ("wrong configuration", "misconfiguration"),
    ("capacity exhaustion", "capacity exhaustion"),
    ("ran out of capacity", "capacity exhaustion"),
    ("thundering herd", "capacity exhaustion"),
    ("dependency failure", "dependency failure"),
    ("upstream timeout", "dependency failure"),
    ("network partition", "network issue"),
    ("packet loss", "network issue"),
    ("dns", "dns issue"),
    ("nameserver", "dns issue"),
    ("control plane", "control plane issue"),
    ("api server", "control plane issue"),
    ("deployment", "deployment failure"),
    ("bad deploy", "deployment failure"),
    ("rollback", "deployment failure"),
    ("database", "database issue"),
    ("query planner", "database issue"),
    ("replication lag", "database issue"),
    ("cascading failure", "cascading failure"),
    ("retry storm", "cascading failure"),
    ("monitoring gap", "monitoring blind spot"),
    ("alert did not fire", "monitoring blind spot"),
    ("lack of visibility", "monitoring blind spot"),
]


def infer_root_cause(text: str) -> str:
    lower = text.lower()
    best_label = ""
    best_score = 0
    for phrase, label in _ROOT_CAUSE_SIGNALS:
        if phrase in lower:
            score = len(phrase)
            if score > best_score:
                best_score = score
                best_label = label
    return best_label or "unspecified"


_LAYER_SIGNALS: list[tuple[str, str]] = [
    ("kubernetes", "platform — kubernetes"),
    ("k8s", "platform — kubernetes"),
    ("aws", "cloud — aws"),
    ("amazon web services", "cloud — aws"),
    ("gcp", "cloud — gcp"),
    ("google cloud", "cloud — gcp"),
    ("azure", "cloud — azure"),
    ("cloudflare", "edge — cdn"),
    ("cdn", "edge — cdn"),
    ("load balancer", "network edge"),
    ("database", "data store"),
    ("postgres", "data store"),
    ("mysql", "data store"),
    ("redis", "data store"),
    ("kafka", "messaging"),
    ("network", "networking"),
    ("bgp", "networking"),
]


def infer_affected_layer(text: str) -> str:
    lower = text.lower()
    for phrase, layer in _LAYER_SIGNALS:
        if phrase in lower:
            return layer
    return "general production"


def infer_reliability_theme(text: str, tags: list[str]) -> str:
    if "observability" in tags or "tracing" in tags:
        return "observability"
    if "capacity" in tags or "scaling" in tags:
        return "capacity and scale"
    if "incident" in tags or "outage" in tags:
        return "incident response"
    lower = text.lower()
    if "postmortem" in lower or "post-mortem" in lower:
        return "incident learning"
    if "slo" in lower or "error budget" in lower:
        return "reliability governance"
    return "general reliability"


_TIMELINE_LINE_RE = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])\s*(.{8,180})$",
    re.MULTILINE,
)


def extract_timeline_entries(text: str, max_items: int = 8) -> list[str]:
    """Heuristic: bullet lines that look like timeline steps."""
    entries: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) < 10:
            continue
        if re.search(r"\d{1,2}:\d{2}|utc|gmt|pdt|pst|edt|est|minutes? after|hours? after", line, re.I):
            if line not in entries:
                entries.append(line[:220])
        if len(entries) >= max_items:
            break
    if len(entries) >= 2:
        return entries[:max_items]
    for m in _TIMELINE_LINE_RE.finditer(text):
        chunk = m.group(1).strip()
        if re.search(r"\d|utc|incident|outage|rollback|mitigat", chunk, re.I):
            entries.append(chunk[:220])
        if len(entries) >= max_items:
            break
    return entries[:max_items]


def detect_vendors(text: str, vendor_keywords: dict[str, list[str]]) -> list[str]:
    lower = text.lower()
    return sorted({
        vendor for vendor, keywords in vendor_keywords.items()
        if any(kw in lower for kw in keywords)
    })


_OPS_PRIORITY_KW = [
    "rollback required", "rollback", "customer impact", "user impact",
    "data loss", "workaround required", "mitigation in progress",
    "incident commander", "war room", "emergency change",
    "production blocked", "halt deployments", "freeze",
    "immediate action", "action required", "urgent",
]


def detect_operational_priority(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _OPS_PRIORITY_KW)


_STOPWORDS = frozenset(
    "a an the and or but in on at to for of is are was were be been being "
    "has have had do does did will would shall should may might can could "
    "this that these those with from by as not no its it they them their "
    "new how what who where when why which all also into over more than "
    "about after before between through during".split()
)


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def compute_title_similarity(title_a: str, title_b: str) -> float:
    words_a = _significant_words(title_a)
    words_b = _significant_words(title_b)
    if not words_a or not words_b:
        return 0.0
    union = words_a | words_b
    return len(words_a & words_b) / len(union) if union else 0.0


def group_articles(articles: list[dict[str, Any]], threshold: float = 0.42) -> list[dict[str, Any]]:
    """Cross-feed grouping using title similarity and overlapping vendors."""
    if not articles:
        return articles

    used: set[str] = set()
    result: list[dict[str, Any]] = []

    for i, primary in enumerate(articles):
        if primary["id"] in used:
            continue
        used.add(primary["id"])
        related: list[dict[str, str]] = []
        pv = set(primary.get("vendors", []))

        for j in range(i + 1, len(articles)):
            other = articles[j]
            if other["id"] in used:
                continue
            if other.get("source") == primary.get("source"):
                continue

            ov = set(other.get("vendors", []))
            shared_vendor = bool(pv and ov and pv & ov)
            similar_title = compute_title_similarity(primary["title"], other["title"]) >= threshold

            if shared_vendor or similar_title:
                related.append({"source": other["source"], "link": other["link"]})
                used.add(other["id"])

        primary["related_sources"] = related
        result.append(primary)

    for a in articles:
        if a["id"] not in used:
            a["related_sources"] = []
            result.append(a)

    result.sort(key=lambda a: a.get("published", ""), reverse=True)
    return result


def generate_landscape_bullets(articles: list[dict[str, Any]]) -> list[str]:
    if not articles:
        return []

    bullets: list[str] = []
    tags = Counter()
    vendors = Counter()
    impacts = Counter()
    priority = 0

    for a in articles:
        tags.update(a.get("tags", []))
        vendors.update(a.get("vendors", []))
        imp = a.get("impact")
        if imp:
            impacts[imp] += 1
        if a.get("operational_priority"):
            priority += 1

    crit = impacts.get("critical", 0)
    high = impacts.get("high", 0)
    if crit:
        bullets.append(
            f"{crit} critical incident signal{'s' if crit != 1 else ''} in the feed window"
        )
    elif high:
        bullets.append(f"{high} high-impact reliability event{'s' if high != 1 else ''} surfaced")

    if priority:
        bullets.append(
            f"{priority} stor{'y' if priority == 1 else 'ies'} flagged for operational follow-up"
        )

    top_vendors = [v for v, _ in vendors.most_common(2) if vendors[v] >= 2]
    if top_vendors:
        bullets.append(
            f"Repeated coverage across {', '.join(top_vendors)}"
        )

    if tags.get("outage", 0) >= 2:
        bullets.append("Multiple outage or availability narratives detected")
    elif tags.get("postmortem", 0) >= 2:
        bullets.append("Several postmortem or lessons-learned write-ups published")

    return bullets[:4]


def extract_top_topics(articles: list[dict[str, Any]], max_items: int = 6) -> list[dict[str, Any]]:
    phrase_counter: Counter = Counter()
    phrase_articles: dict[str, list[str]] = {}

    for a in articles:
        title_lower = a.get("title", "").lower()
        words = re.findall(r"[a-z][a-z0-9\-]+", title_lower)
        significant = [w for w in words if len(w) > 3 and w not in _STOPWORDS]

        for i in range(len(significant)):
            for length in (1, 2):
                if i + length <= len(significant):
                    phrase = " ".join(significant[i : i + length])
                    if len(phrase) > 4:
                        phrase_counter[phrase] += 1
                        phrase_articles.setdefault(phrase, []).append(a["id"])

    seen_ids: set[frozenset[str]] = set()
    results: list[dict[str, Any]] = []
    for phrase, count in phrase_counter.most_common(30):
        if count < 2:
            break
        article_ids = phrase_articles[phrase]
        key = frozenset(article_ids[:3])
        if key in seen_ids:
            continue
        seen_ids.add(key)
        results.append({"topic": phrase, "count": count})
        if len(results) >= max_items:
            break

    return results


def apply_personalization(
    article: dict[str, Any], personalization: dict[str, Any]
) -> dict[str, Any]:
    preferred_vendors = set(v.lower() for v in personalization.get("preferred_vendors", []))
    highlight_kw = [kw.lower() for kw in personalization.get("highlight_keywords", [])]

    highlighted = False
    if preferred_vendors:
        article_vendors = {v.lower() for v in article.get("vendors", [])}
        if article_vendors & preferred_vendors:
            highlighted = True
    if highlight_kw:
        searchable = f"{article.get('title', '')} {article.get('summary', '')}".lower()
        if any(kw in searchable for kw in highlight_kw):
            highlighted = True

    article["highlighted"] = highlighted
    return article


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def split_sentences(text: str) -> list[str]:
    raw = _SENT_SPLIT.split(text.strip())
    return [s.strip() for s in raw if len(s.strip()) > 15]


def _score_sentence(sentence: str, keywords: list[str]) -> int:
    lower = sentence.lower()
    return sum(1 for kw in keywords if kw in lower)


def _pick_sentences(sentences: list[str], keywords: list[str], max_count: int = 4) -> list[str]:
    scored = [(s, _score_sentence(s, keywords)) for s in sentences]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, sc in scored[:max_count] if sc > 0]


_TECH_KW = [
    "latency", "throughput", "cpu", "memory", "disk", "queue", "thread",
    "connection pool", "timeout", "retry", "circuit breaker", "load balancer",
    "kubernetes", "pod", "node", "container", "kernel", "network", "tcp",
    "dns", "tls", "http", "grpc", "database", "replication", "shard",
    "cache", "cdn", "edge", "region", "availability zone", "control plane",
    "data plane", "deployment", "rollback", "canary", "feature flag",
    "observability", "metric", "trace", "log", "dashboard", "alert",
    "slo", "sli", "error budget", "capacity", "autoscaling", "throttle",
]

_IMPACT_KW = [
    "customer", "user", "client", "tenant", "region", "availability",
    "downtime", "degradation", "outage", "impact", "affected", "duration",
    "error rate", "5xx", "success rate", "minutes", "hours", "scope",
]

_RESOLUTION_KW = [
    "resolved", "mitigation", "fix", "patched", "rolled back", "rollback",
    "scaled", "throttled", "rerouted", "failover", "restored", "recovered",
    "workaround", "root cause", "permanent fix", "follow-up",
]

_LESSONS_KW = [
    "lesson", "learned", "going forward", "we will", "action items",
    "improve", "prevent", "better", "next time", "follow-up", "recommendation",
]


def _build_overview(sentences: list[str], title: str, summary: str) -> str:
    if len(sentences) >= 2:
        text = " ".join(sentences[:3])
        if len(text) > 80:
            return text
    if summary and len(summary) > 80:
        return summary
    return f"{title}. {summary}" if summary else title


def _build_technical(sentences: list[str], tags: list[str]) -> str:
    picked = _pick_sentences(sentences, _TECH_KW, 5)
    if picked:
        return "\n\n".join(picked)
    tag_set = set(tags)
    parts: list[str] = []
    if "kubernetes" in tag_set:
        parts.append("Kubernetes or container orchestration appears central to this story.")
    if "observability" in tag_set:
        parts.append("Telemetry, monitoring, or tracing considerations are highlighted.")
    if "networking" in tag_set:
        parts.append("Network path, DNS, or edge delivery factors are discussed.")
    if not parts:
        parts.append("Technical detail is limited in the syndicated excerpt; open the source for depth.")
    return "\n\n".join(parts)


def _build_impact(sentences: list[str], impact: str | None, affected_layer: str) -> str:
    picked = _pick_sentences(sentences, _IMPACT_KW, 4)
    parts: list[str] = []
    if picked:
        parts.append(" ".join(picked))
    label = impact or "medium"
    parts.append(f"Classified impact band: {label.replace('_', ' ')}. Affected layer signal: {affected_layer}.")
    return "\n\n".join(parts)


def _build_resolution(sentences: list[str]) -> str:
    picked = _pick_sentences(sentences, _RESOLUTION_KW, 4)
    if picked:
        return "\n\n".join(picked)
    return (
        "Resolution detail may be partial in the RSS excerpt. "
        "Refer to the original write-up for timelines, mitigations, and verification steps."
    )


def _build_lessons(sentences: list[str], tags: list[str]) -> str:
    picked = _pick_sentences(sentences, _LESSONS_KW, 4)
    if picked:
        return "\n\n".join(picked)
    if "postmortem" in tags:
        return "This reads as a retrospective or postmortem-style narrative; extract concrete action items from the source."
    return "Lessons are not explicit in the excerpt; use the source discussion for durable engineering takeaways."


def _build_root_cause_highlight(root_cause: str, sentences: list[str]) -> str:
    if not root_cause or root_cause == "unspecified":
        return ""
    picked = _pick_sentences(sentences, [root_cause.split()[0], "because", "due to", "caused by"], 3)
    head = f"Likely root-cause theme: {root_cause}."
    if picked:
        return head + " " + " ".join(picked)
    return head + " Corroborate against the vendor or team narrative in the original article."


def _build_trends_context(tags: list[str], title: str) -> str:
    contexts: list[str] = []
    tag_set = set(tags)
    if "outage" in tag_set:
        contexts.append("Large-scale outages remain the forcing function for resilient architecture, communication, and automation investments.")
    if "postmortem" in tag_set:
        contexts.append("Public postmortems compress organizational learning into patterns the broader industry can reuse.")
    if "observability" in tag_set:
        contexts.append("Observability shifts remain tightly coupled with incident response quality and capacity planning accuracy.")
    if "capacity" in tag_set or "scaling" in tag_set:
        contexts.append("Demand spikes and capacity cliffs continue to drive autoscaling, queueing, and graceful degradation designs.")
    if not contexts:
        contexts.append("This item contributes to the broader reliability narrative for production engineering teams.")
    return "\n\n".join(contexts)


def build_email_summary(article: dict[str, Any]) -> str:
    title = article.get("title", "")
    summary = article.get("summary", "")
    full = article.get("full_content", "") or summary
    impact = article.get("impact")

    sentences = split_sentences(full)
    lead = sentences[0] if sentences else summary.split(". ")[0] if summary else title
    if not lead.endswith("."):
        lead = lead.rstrip(".!?") + "."
    parts = [lead]
    if impact in ("critical", "high"):
        parts.append(f"Impact band: {impact}.")
    if len(parts) == 1 and len(sentences) >= 2:
        second = sentences[1]
        if not second.endswith("."):
            second = second.rstrip(".!?") + "."
        parts.append(second)
    result = " ".join(parts)
    return result[:500] if len(result) <= 500 else result[:497].rsplit(" ", 1)[0] + "…"


def enrich_article(
    article: dict[str, Any],
    vendor_keywords: dict[str, list[str]] | None = None,
    personalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full_text = article.get("full_content", "") or ""
    title = article.get("title", "")
    summary = article.get("summary", "")
    tags = article.get("tags", [])
    searchable = f"{title} {summary} {full_text}"

    impact = classify_impact(searchable)
    vendors = detect_vendors(searchable, vendor_keywords or {})
    operational_priority = detect_operational_priority(searchable)
    root_cause = infer_root_cause(searchable)
    affected_layer = infer_affected_layer(searchable)
    reliability_theme = infer_reliability_theme(searchable, tags)
    timeline_entries = extract_timeline_entries(full_text or summary)

    sentences = split_sentences(full_text)
    sections = {
        "overview": _build_overview(sentences, title, summary),
        "technical_breakdown": _build_technical(sentences, tags),
        "impact": _build_impact(sentences, impact, affected_layer),
        "resolution": _build_resolution(sentences),
        "lessons_learned": _build_lessons(sentences, tags),
        "root_cause_highlight": _build_root_cause_highlight(root_cause, sentences),
        "incident_timeline": "\n".join(timeline_entries) if timeline_entries else "",
        "trends": _build_trends_context(tags, title),
    }

    article["impact"] = impact
    article["vendors"] = vendors
    article["operational_priority"] = operational_priority
    article["root_cause"] = root_cause
    article["affected_layer"] = affected_layer
    article["reliability_theme"] = reliability_theme
    article["timeline_entries"] = timeline_entries
    article["sections"] = sections
    article["email_summary"] = build_email_summary(article)
    article["related_sources"] = article.get("related_sources", [])
    article["highlighted"] = False

    if personalization:
        apply_personalization(article, personalization)

    return article
