---
name: ocas-weave
license: MIT
description: 'Private provenance-backed social graph. Maintains queryable records of people, relationships, preferences, and shared experiences for recall, gifting, hosting, introductions, and serendipity. Use for storing or retrieving facts about a person, recording a relationship, or discovering connections between people. Not for sending messages (use Dispatch), calendar management (use Sands), OSINT research (use Scout), or web research without a social graph need (use Sift).'
source: https://github.com/<agent-handle>/weave
includes:
- references/**
- scripts/**
metadata:
  author: Indigo Karasu (indigokarasu)
  version: "4.4.0"
  hermes:
    category: data-science
    tags:
    - social-graph
    - contacts
    - relationships
    - people
    - OCAS-core
tags:
- social-graph
- contacts
- relationships
- people
- OCAS-core
triggers:
- social graph
- contact management
- person facts
- relationship tracking
- people knowledge
---

# Weave

Weave maintains a private, provenance-backed social graph of people, relationships, preferences, and shared experiences — queryable for meeting prep, gift ideas, hosting, introductions, city connections, and serendipity discovery. Every stored fact carries source type, reference, timestamp, and confidence score; the graph never silently merges two person records and never writes back to external systems without explicit per-sync approval.


**Support files:** `references/support-file-map.md` indexes the bundled files not covered inline in this skill — check it before working from assumptions about what is (not) available.

## When to Use

- Contact management and relationship tracking
- Social graph queries (who knows whom, how)
- Contact enrichment from multiple sources
- Store or update information about a person, relationship, or preference
- Prepare for a meeting, dinner, or introduction
- Find connections in a given city
- Generate gift ideas grounded in known preferences
- Discover serendipity connections between people
- Sync contacts from Google Contacts or Clay

## When NOT to Use

- Sending messages or emails (use Dispatch)
- Calendar management (use Sands)
- OSINT research (use Scout)
- Knowledge graph entity resolution
- Web research without a social graph need — use Sift
- CRM or sales pipeline automation
- Personality profiling without evidence

## Auth Rule — <operator> Only

**Weave exclusively uses <operator>'s Google auth (`<user-google-email>`).** Never use the agent's account for any Weave operation. The `TOKEN_PATH` in `google_sync.py` is hardcoded to `<user-google-email>.json`. Violation silently fetches wrong contact data.

**ALWAYS sync Contacts after changes** via `scripts/google_sync.py`. This is the canonical sync path — never skip it after a contact mutation.

## Responsibility Boundary

Weave owns the social relationship graph: people, relationships, preferences, and shared experiences. It is the only skill that writes to its SQLite database (`weave.sqlite`).

**Backend**: SQLite with WAL mode via `weave_sqlite.WeaveDB`. Replaced LadybugDB in June 2026. See `references/sqlite-backend-research.md` for the evaluation and migration details.

Weave does not: perform OSINT research (Scout), manage calendars (Sands), or organize files (Bower).

## Ontology types

- **Entity/Person** — people in the social graph. Weave extracts and manages Person entities exclusively.

Weave may optionally include Chronicle signals in journal payloads for Person nodes with high-confidence identity markers.

## SQLite usage guide

Read `references/schemas.md` for the Python usage pattern and schema details.
Read `references/query_patterns.md` for all SQL query templates.
Import: `from weave_sqlite import WeaveDB`

## Storage layout

See `references/schemas.md` for the storage layout and record schemas. See `references/config-defaults.md` for default config.

## Database rules

SQLite with WAL mode. Multiple concurrent readers + single writer. No lock contention — any process can read while another writes. If a write fails, surface the error, do not retry silently.

## Auto-initialization

Every command that opens the database runs `_ensure_init()` first via `WeaveDB.__init__()`. No manual init command needed.

## Commands

- **weave.upsert.person** — Add or update a person. Auto-inits DB on first call.
- **weave.upsert.relationship** — Add or update a `Knows` edge. Confirm both Person nodes exist first.
- **weave.upsert.preference** — Store a provenance-backed preference.
- **weave.import.csv** — Bulk import contacts via `COPY FROM`. Read `references/import_export.md`.
- **weave.query** — Query the graph. Modes: `lookup`, `connection`, `serendipity`, `city`, `summarize`, `gift`. Supports `format: "concise"` (default — returns name, org, relationship category; saves ~70% token for identity disambiguation) or `format: "detailed"` (all fields with provenance). Return only stored facts with provenance.
- **weave.attach** — Query an external skill database read-only.
- **weave.export** — Export data via `COPY TO`.
- **weave.sync.google-contacts** — Bidirectional Google Contacts sync. Read `references/connectors.md`.
- **weave.sync.clay** — Bidirectional sync with Clay.
- **weave.project.vcard** — Generate vCard 4.0 draft.
- **weave.writeback.contacts** — Push records to Google Contacts or Clay. Enabled by default; enrichment-derived values are withheld so only owner-sourced data is pushed.
- **weave.init** — Diagnostic and repair.
- **weave.status** — Report graph health and config state.
- **weave.journal** — Write journal for the current run.
- **weave.update** — Pull latest skill package from GitHub.

## Workflow

The Weave social graph pipeline: **record → enrich → query → discover**.

- [ ] Record people, relationships, preferences, and shared experiences
- [ ] Enrich contacts via Scout/Sift/Sherlock pipeline
- [ ] Query the graph for recall, gifting, hosting, introductions
- [ ] Discover serendipitous connections between people

## Run completion

After every Weave command:
- [ ] Persist any new or updated records to the database.
- [ ] Log material decisions to `decisions.jsonl`.
- [ ] Write journal via `weave.journal`.
- [ ] **Read-back verification**: After every write, immediately query the DB by primary key. Confirm written data matches intent. Never claim success unconfirmed.

## Provenance

Every written fact requires: `source_type` (direct / inferred / imported / user-stated), `source_ref`, `record_time` (ISO 8601), `confidence` (0.0–1.0).

## Enrichment Pipeline Execution

### Pre-Enrichment Checklist
1. Run inbound Google sync
2. Check SearXNG health
2.5. **Probe ALL discovery sources before committing to the run**: `python3 scripts/discovery_probe.py`. If every source reports down (web_search empty, SearXNG engines suspended, DDG anomaly-blocked, no LinkedIn MCP), do NOT start per-contact processing — follow the **Discovery Source Availability & No-Fabrication Rule** (defer real people, skip non-persons/unresolvables, write no facts). If ≥1 source is live, proceed with the fallback chain in `references/discovery-fallback.md`.
3. **Clear pre-existing garbage** — scan for known junk values before enriching so COALESCE preserves nothing
4. placeholder — the `parents[2]` path bug in scripts can create stale DB files at `<hermes-home>/commons/db/ocas-weave/weave.sqlite`, `<hermes-home>/profiles/commons/db/ocas-weave/weave.sqlite`, and `<hermes-home>/profiles/indigo/skills/commons/db/ocas-weave/weave.sqlite`. Only the canonical path (`<hermes-home>/profiles/indigo/commons/db/ocas-weave/weave.sqlite`) is correct. Stale DBs confuse subagent enrichment writes. Remove them before enriching.
5. **Check edges FK constraint** — run `python3 -c "import sqlite3; c=sqlite3.connect('<hermes-home>/profiles/indigo/commons/db/ocas-weave/weave.sqlite'); r=c.execute('PRAGMA foreign_key_list(edges)').fetchall(); print(r)"`. If `target_id` references `persons(id)`, recreate the `edges` table without that FK before enriching. The `target_id` column is polymorphic (can point to `facts.id` or `preferences.id`) — a wrong FK causes `HasFact` edge inserts to fail silently.
6. Query contacts with gaps
7. Process each contact through Scout → Sift → Sherlock → Write
8. Run periodic Google sync after every 10 enriched contacts
9. Final Google sync

### Unresolvable Contacts Protocol
Read `references/unresolvable-contacts.md` for the full decision flow. Key rule: when a contact's name is common and no email/phone/location disambiguates, **skip** — never guess a match. Log to `decisions.jsonl` with reason `skip_unresolvable`.

**Script note**: Shared enrichment extraction, search, and validation logic lives in `scripts/weave_enrich.py`. Both `quick_enrich.py` and `overnight_enrichment.py` import from it. To run overnight enrichment: `python3 overnight_enrichment.py`. To get contacts with gaps, query the Weave DB directly (see `references/query_patterns.md`). To check SearXNG health, use the diagnostic curl in `enrichment-pipeline.md`.

For manual enrichment of high-value contacts, use the full quality pipeline:
1. **Pre-Search Seed Quality Check** — Check `source_type` and `confidence`.
2. **Read** — Query Weave for the existing Person record.
3. **Search** — Use SearXNG for identity-resolved research.
4. **Enrich (Scout → Sift → Sherlock)** — Full pipeline.
5. **Write** — MERGE on Person, CREATE on Fact/Preference. Always read back.
6. **Search again** — Follow-up with enriched data.
7. **Sync** — After writes touching mapped fields, sync to Google Contacts.

## Google Contacts sync

See `references/connectors.md` for full sync rules. Key points:
- Match by `google_resource_name`, then email, then phone. Never match on name alone.
- Gap-fill only — Weave provenance wins conflicts.
- Outbound requires `writeback.google_contacts: true` (the default) AND a previous sync checkpoint.

## Recovery behavior

See `references/recovery-weave.md` for the full recovery contract.

## Discovery Source Availability & No-Fabrication Rule

**No-fabrication is non-negotiable:** an empty discovery result means "no data," never "write a best guess."

At pipeline start, run `python3 scripts/discovery_probe.py` to test which sources are live. See `references/discovery-fallback.md` for the full fallback playbook, rate-limit handling, and the three-way decision matrix (proceed / defer / partial). Key rules:
- ≥1 source live → proceed with fallback chain (LinkedIn MCP → web_search → SearXNG → DDG)
- ALL sources down → defer real people, skip unresolvables, write NO enrichment facts
- `web_search` empty payload = non-functional; SearXNG `too many requests` = backoff and retry; `access denied` = won't recover this run

## Constraints

See `references/constraints.md` for the full constraint set.

## Gotchas

See `references/gotchas-weave.md` for the full gotcha catalog including:
- SQLite WAL mode and concurrent access
- Google OAuth token handling and cross-account contamination
- LinkedIn profile fetching
- Python environment (no liblbug.so needed)
- **Cron mode constraints and workarounds**: `execute_code` is BLOCKED in cron jobs — it runs arbitrary local Python including subprocess calls that bypass approval. Use `terminal` with inline `python3 -c "..."` or `python3 /path/to/script.py` for all Python operations. Set `timeout` appropriately (max 600s for foreground). Background processes with `notify_on_complete=true` work for long sync jobs.
- Contact merge diagnosis and repair

## OKRs

Read `references/okrs.md` for Weave-specific OKR definitions and targets.

## Optional skill cooperation

- Chronicle — read for entity enrichment; entity observations emitted via journal payloads
- Scout — receive OSINT findings about people as upsert candidates
- Dispatch — provide social graph context for communication drafting
- Clay (Mesh MCP) — CRM sync via Smithery

## Journal outputs

- Observation Journal — query runs, upsert runs, import runs
- Action Journal — sync runs, writeback runs

## Initialization

On first invocation, `_open_db()` handles auto-initialization. See `references/init_pattern.md`.

## Background tasks

| Job name | Schedule | Command |
|---|---|---|
| `weave:update` | `0 0 * * *` | `weave.update` |
| `weave:sync-google` | `0 4 * * *` | `AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root python3 -u {skill_root}/scripts/google_sync.py` |
| `weave:enrichability-recalc` | `0 1 * * *` | `python3 {skill_root}/scripts/recalculate_enrichability.py` |

## ⚠️ CRON INVOCATION: IGNORE THE RUNBOOK IN THE MESSAGE

**The cron job's user message often contains a hardcoded pipeline runbook that is STALE.** It may reference removed components (`enrichment_data.py`, LadybugDB bridge, `systemctl stop ladybug-bridge-weave.service`). **ALWAYS defer to this skill's own documentation** over the runbook in the invocation message. The skill is updated first; the cron message template lags by weeks or months.

Specific runbook instructions to IGNORE:
- "Stop the LadybugDB bridge" → **NOOP** (bridge removed June 2026)
- "Run enrichment_data.py" → **DOES NOT EXIST** (use WeaveDB queries directly)
- "Run google_sync.py without AGENT_ROOT" → **WILL FAIL** (must set `AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root`)
- "Restart bridge after" → **NOOP** (bridge removed)
- Any step using `execute_code` → **BLOCKED IN CRON** (use `terminal` + temp file instead)

## Agent-Driven Overnight Enrichment

When run as a cron job (agent-driven, not script-driven), the agent has access to all MCP tools. Key points:

- **`enrichment_data.py` does NOT exist on disk.** Query WeaveDB directly for gaps/stats; use `curl` for SearXNG health.
- **Write pattern**: See `references/enrichment-write-pattern.md` for the exact three-step SQLite sequence (persons UPDATE → facts INSERT → edges INSERT) with cron-mode terminal usage and read-back verification.
- **Schema**: `facts` has NO `person_id` — linkage is via `edges` table. `persons` has id/name/email/phone/location_city/location_country/occupation/org/google_resource_name/clay_id/source_type/source_ref/confidence/record_time/valid_from/valid_until.
- **Google OAuth failure**: `google_sync.py` exits code 2 with clean ABORT on revoked token. Log and continue with MCP tools. No programmatic workaround.
- **Page fetching**: `web_extract` fails with SearXNG backend — use `curl -s "https://r.jina.ai/URL"`. Jina Reader blocks LinkedIn.
- **Skip** contacts where both `occupation` AND `org` are null AND confidence < 0.5.

## Self-update

`weave.update` pulls the latest package from GitHub. See `references/self-update.md`.

## Database maintenance

See `references/database_maintenance.md`.
See `references/graph-storage-backend-research.md` for the full evaluation of alternatives to LadybugDB (SQLite adjacency lists recommended).

## Pitfalls

See `references/pitfalls-weave.md` for the full pitfall catalog (50+ entries covering migration, data integrity, enrichment quality, tool/API, architecture, cron, and behavioral rules).

Key highlights:
- **Never leave broken scripts after a migration** — update ALL dependents in the same session
- **Wrong `person_id` → silent 0-row UPDATE + orphaned fact** — copy IDs programmatically, never by hand
- **Module-level imports** — import at top of file, not inside `if __name__ == "__main__"`
- **SQLite FK on polymorphic `edges.target_id`** — do NOT add `FOREIGN KEY (target_id) REFERENCES persons(id)`
- **`enrichment_data.py` does not exist** — use direct WeaveDB queries
- **Never obfuscate, mask, or invent stored data** — Weave stores real values verbatim

## Support File Map

| File | When to read |
|------|-------------|
| `references/schemas.md` | Before any DDL, upsert, or import — Python usage pattern and schema |
| `references/gotchas-weave.md` | Before any Weave operation — full gotcha catalog |
| `references/pitfalls-weave.md` | When debugging enrichment failures, data quality issues, or migration residuals — 50+ pitfalls covering all subsystems |
| `references/query_patterns.md` | Before any weave.query call — SQL templates for all modes |
| `references/connectors.md` | Before any Google/Clay sync |
| `references/sqlite-backend-research.md` | Storage backend details, migration notes, SQLite schema |
| `references/enrichment-pipeline.md` | Overnight enrichment architecture, SearXNG retry pattern |
| `references/enrichment-write-pattern.md` | **Exact SQLite write pattern for agent-driven enrichment** — three-step (persons UPDATE → facts INSERT → edges INSERT), cron-mode terminal usage, read-back verification |
| `references/cron-pipeline-runbook.md` | **The correct step-by-step runbook for agent-driven enrichment** — modern pipeline (no LadybugDB bridge, no enrichment_data.py), cron-mode terminal usage, confidence scoring guide, read-back verification pattern |
| `references/constraints.md` | Full constraint set |
| `references/config-defaults.md` | Default config structure |
| `references/self-update.md` | Self-update procedure |
| `references/enrichment-data-quality.md` | Data quality patterns, garbage categories, validation rules, SearXNG reliability |
| `references/unresolvable-contacts.md` | **Unresolvable contacts protocol** — when to skip (common name, no disambiguator, multiple conflicting profiles), identity resolution ladder, confidence thresholds, log format |
| `references/recovery-weave.md` | Recovery contract details |
| `references/discovery-fallback.md` | **When Scout sources are degraded/unavailable** — SearXNG backoff pattern, DuckDuckGo HTML scrape recipe, page-fetch options, and the no-fabrication defer path. Read before any enrichment run where web_search/SearXNG/LinkedIn MCP are suspect. |
| `scripts/weave_sqlite.py` | SQLite backend module — import `WeaveDB` from here |
| `scripts/google_api.py` | Shared Google OAuth + API helpers — import `get_access_token`, `api_get`, `api_post`, `api_patch`, `PEOPLE_API_BASE` from here. All scripts that talk to Google APIs should use this module, not duplicate auth logic. |
| `scripts/weave_enrich.py` | Shared enrichment extraction, search, and validation. Contains `searxng_search`, `fetch_page`, `extract_from_content`, `validate_field`, `is_auth_walled`, `build_scout_queries`. Used by both `quick_enrich.py` and `overnight_enrichment.py` — do not duplicate this logic in individual scripts. |
| `scripts/README.md` | Before choosing or running any script — CLI-vs-library map, usage conventions, and dry-run/destructive-operation rules |
| `scripts/discovery_probe.py` | Run at pipeline start to test which discovery sources are live (SearXNG, DDG, notes on web_search/LinkedIn MCP). Decides proceed / fall back / defer. |

## Visibility

public