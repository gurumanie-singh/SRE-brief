# SRE Brief

Daily **Site Reliability Engineering (SRE)** and **production reliability** briefing. The repository ingests public RSS feeds, classifies each item into an operational impact band, enriches it with deterministic heuristics, publishes a **static GitHub Pages** site updated **hourly**, and sends **one** concise email per local day at **07:00** in your configured timezone.

This project is adapted from the **Threat Brief** cybersecurity news pipeline: the same file-based architecture, GitHub Actions automation model, per-day JSON storage, Jinja2 static generation, and Gmail-compatible SMTP delivery — with domain logic, taxonomy, templates, and copy rewritten for reliability engineering.

## What you get

- **Hourly workflow**: fetch feeds → deduplicate → classify impact → enrich → write `data/days/YYYY-MM-DD.json` → regenerate `docs/`.
- **Daily email workflow**: timezone-aware gate → send a single HTML + plain-text briefing → persist dedup markers in `data/state.json`.
- **No backend, no database**: YAML configuration + JSON files + static HTML.

## Quick start (local)

```bash
cd SRE-brief
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.run_hourly
```

Open `docs/index.html` in a browser (or serve `docs/` with any static file server).

### Debugging the content pipeline (empty homepage)

1. **One-shot verification** (fetch → process → site + summary):

   ```bash
   python -m scripts.verify_pipeline
   ```

2. **Step-by-step** (same stages the hourly job runs):

   ```bash
   python -m scripts.fetch_feeds
   python -m scripts.process_articles
   python -m scripts.generate_site
   ```

3. **Artifacts to inspect**
   - `data/days/YYYY-MM-DD.json` — must exist after a successful ingest. If only `data/last_updated.json` is present, the site was regenerated **without** new JSON (often: workflow never ran ingest, or only `generate_site` was executed locally).
   - `data/pipeline_stats.json` — written every `process()` run: per-feed results, dedupe counts, retention drops, and warnings.

4. **Homepage date window** — the index lists items whose RSS calendar `day` is within `settings.homepage_calendar_days` (default 7). Engineering feeds often carry posts **older than seven calendar days**; in that case the generator uses **`homepage_recency_fallback`** (default `true`) and fills the homepage from the newest `published` timestamps instead of showing an empty feed.

5. **Manually trigger the hourly workflow on GitHub** — Repository → **Actions** → **Hourly Site Update** → **Run workflow** → choose the default branch → **Run workflow**. Open the job log: a **Report pipeline stats** step prints `data/pipeline_stats.json` and lists `data/days/*.json`.

### Optional article HTML enrichment

In `feeds.yaml`, set `settings.enrich_fetch_full_article: true` to fetch each article URL (capped per run) for richer classification. This increases runtime and outbound traffic — keep disabled on slow networks.

### Email dry run

1. Copy `.env.example` to `.env` and fill in credentials (use a provider **app password**, never your primary account password).
2. Load env vars then run the daily sender standalone:

```bash
set -a && source .env && set +a
python -m scripts.send_email
```

The scheduler deduplicates via `data/state.json`. For a forced resend in automation, use the workflow dispatch input **Force send** (sets `FORCE_RUN=true` for that run only).

### Schedule diagnostics

```bash
python -m scripts.scheduler
```

## GitHub setup

### Secrets (repository)

| Secret | Required | Purpose |
|--------|----------|---------|
| `EMAIL_SENDER` | Yes (for email) | SMTP login / From address |
| `EMAIL_PASSWORD` | Yes (for email) | App password or SMTP token |
| `EMAIL_RECEIVER` | Yes (for email) | Inbox that receives the digest |
| `SMTP_HOST` | No | Default `smtp.gmail.com` |
| `SMTP_PORT` | No | Default `587` |

### GitHub Pages

1. Repository **Settings → Pages**.
2. **Build and deployment**: deploy from branch **main** (or your default) and folder **`/docs`**.
3. Set `settings.site_base_url` in `feeds.yaml` to your Pages URL, e.g. `https://<user>.github.io/sre-brief/`, then let the hourly workflow regenerate the site so email “Read more” links resolve.

### Workflows

| Workflow | File | When |
|----------|------|------|
| Hourly site update | `.github/workflows/update-site.yml` | Every hour + manual |
| Daily email | `.github/workflows/daily-email.yml` | Every 30 minutes (Python gate) + manual |

**Manual runs**: Actions → select workflow → **Run workflow**.

**Duplicate email prevention**: `scripts/scheduler.py` records `last_email_date` in `data/state.json` after a successful send. The workflow runs every 30 minutes but sends **at most once** per local calendar day after 07:00.

**Concurrency**: both workflows share `concurrency.group: sre-brief-pipeline` so hourly pushes and daily state commits serialize safely.

## Configuration

### Timezone and send window

`feeds.yaml`:

```yaml
settings:
  timezone: "America/Chicago"
```

IANA names only (`Europe/London`, `Asia/Tokyo`, …). The daily job triggers every 30 minutes; Python allows the first run **on or after 07:00 local** that has not yet completed for that date.

### Feeds

Edit the `feeds:` list — each entry needs `name` and `url`. Tags and vendors are inferred using `tag_keywords` and `vendor_keywords` maps.

### Personalization

```yaml
personalization:
  preferred_vendors: ["AWS", "Kubernetes"]
  highlight_keywords: ["multi-region", "postmortem"]
  email_min_impact: "medium"   # critical | high | medium | low
```

`email_min_impact` filters **out** lower-impact items from the email (site remains unchanged).

## Impact classification

Every article receives `impact` ∈ `critical`, `high`, `medium`, `low` using weighted keyword scoring over title + summary + optional fetched body. Default when signals are absent: **`medium`**. Distribution is logged after each processing run.

Human-readable labels on the site:

- **Critical incident** — widespread outages, major production failures  
- **High impact** — strong degradation, partial outages, serious scaling or dependency failures  
- **Medium** — latency, localized disruption, operational noise  
- **Low / insight** — guidance, architecture, observability practices without acute incident signals  

## Retention and repository size

- **0–7 days** (`settings.active_days`): homepage window.  
- **Up to `max_retention_days`**: day JSON files and generated HTML kept; older day files **deleted** each run.  
- **Archive pages** only cover days still present on disk.  

There is **no** separate cold-storage archive folder inside this repo by design — the hourly job removes expired `data/days/*.json` and stale HTML.

## Troubleshooting

| Symptom | Check |
|---------|------|
| Empty site | Run `python -m scripts.run_hourly` locally with network; verify feeds respond. |
| SMTP auth errors | App password, 2FA, and “less secure app” policies per provider. |
| Email not sending on schedule | `timezone` correct? Inspect Actions log for “Too early” vs “Already ran today”. |
| Wrong links in email | `site_base_url` must match your GitHub Pages URL including trailing slash policy (generator uses your string verbatim). |

## Pushing this repository to GitHub (private)

From your machine:

```bash
cd /Users/guru/Documents/SRE-brief
git init
git add .
git commit -m "Initial commit: SRE Brief reliability news platform"
```

Create an **empty private** repository on GitHub (no README/license templates to avoid merge conflicts), then:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

If you use SSH:

```bash
git remote add origin git@github.com:<your-username>/<repo-name>.git
```

Enable Actions, Secrets, and Pages as described above.

## License

MIT — see `LICENSE`.
