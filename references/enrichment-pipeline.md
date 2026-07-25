# Enrichment Pipeline Architecture

## Overview

The Weave enrichment pipeline consists of two entry points sharing a common extraction core:

| Script | Mode | Entry |
|--------|------|-------|
| `quick_enrich.py` | Single-contact interactive | `python3 quick_enrich.py "Name" [--org "Co"]` |
| `overnight_enrichment.py` | Batch overnight | `python3 overnight_enrichment.py` |

Both import shared logic from `weave_enrich.py`:
- `searxng_search()` — SearXNG web search
- `fetch_page()` — HTTP fetch with Jina fallback
- `extract_from_content()` — Regex extraction of occupation, org, location, email
- `validate_field()` — Field-level validation
- `is_auth_walled()` — Auth-wall domain check
- `build_scout_queries()` — Identity-resolved query construction

## Pipeline Phases

1. **SCOUT** — SearXNG identity-resolved research using `build_scout_queries()`
2. **SIFT** — Full page extraction via `fetch_page()` + `extract_from_content()`
3. **SHERLOCK** — Username/handle expansion via `sherlock` CLI (quick_enrich only)
4. **WRITE** — Persist to Weave as Fact nodes with provenance

## Key Config

| Parameter | Overnight | Quick |
|-----------|-----------|-------|
| `SEARCH_DELAY` | 3s | 2s |
| `SYNC_EVERY` | 30 contacts | N/A |
| `DEADLINE_HOUR_ET` | 8am | N/A |
| `MIN_CONFIDENCE` | 0.7 | 0.7 |
| Max pages per contact | 3 | 3 |
| Max queries per contact | 4 | 4 |

## Running

```bash
# Quick single-contact
<<<<<<< Updated upstream
AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root \
  python3 quick_enrich.py "Jane Doe" --org "Acme Corp"

# Overnight batch
AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root \
=======
AGENT_ROOT=~/.hermes/profiles/indigo HOME=/root \
  python3 quick_enrich.py "Jane Doe" --org "Acme Corp"

# Overnight batch
AGENT_ROOT=~/.hermes/profiles/indigo HOME=/root \
>>>>>>> Stashed changes
  python3 overnight_enrichment.py
```

## Agent-Driven Mode (Cron Jobs)

When enrichment runs as an agent cron job (not via `overnight_enrichment.py`), the agent has access to all MCP tools. In this mode:

- **Do NOT attempt to run `enrichment_data.py`** — the file does not exist on disk. Use direct WeaveDB queries and `curl` for SearXNG health.
- **Do NOT attempt to stop/start `ladybug-bridge-weave.service`** — the service no longer exists post-SQLite-migration.
- **If Google sync fails with `invalid_grant`**, log the failure and continue. Use `web_search`, Composio web tools, and `curl` + Jina Reader (`r.jina.ai/URL`) for data gathering instead.
- **Page fetching**: Use `curl -s "https://r.jina.ai/URL"` — `web_extract` fails with SearXNG backend ("search-only backend cannot extract URL content").
- **Pipeline**: Scout (web_search + LinkedIn MCP + SearXNG) → Sift (fetch pages + LLM extraction) → Sherlock (cross-reference) → Write (WeaveDB.execute_write)

- **SearXNG connection resets under load**: During overnight enrichment, SearXNG can return `Connection reset by peer` errors when hit with rapid sequential searches. Add retry logic with exponential backoff (3 attempts, 2s/4s/8s) to `searxng_search()`.
- **Never duplicate extraction logic** — edit `weave_enrich.py`, not individual scripts
- **Always use WeaveDB methods** (`execute`, `execute_write`) for DB writes, never raw `sqlite3` connections
- **Progress tracking** — overnight pipeline writes to `progress.jsonl`; safe to restart (idempotent)
- **Deadline** — overnight pipeline checks `is_past_deadline()` (8am ET) and stops gracefully