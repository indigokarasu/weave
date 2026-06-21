---
name: ocas-weave
description: 'Private provenance-backed social graph. Maintains queryable records of people, relationships, preferences, and shared experiences for recall, gifting, hosting, introductions, and serendipity. Use for storing or retrieving facts about a person, recording a relationship, or discovering connections between people. Do NOT use for sending messages (use Dispatch), calendar management (use Sands), OSINT research (use Scout), or web research without a social graph need (use Sift).'
license: MIT
source: https://github.com/indigokarasu/weave
includes:
- references/**
- scripts/**
metadata:
  author: Indigo Karasu (indigokarasu)
  version: "4.3.0"
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
- Knowledge graph entity resolution (use Elephas)
- Web research without a social graph need — use Sift
- CRM or sales pipeline automation
- Personality profiling without evidence

## Auth Rule — owner Only

**Weave exclusively uses owner's Google auth (`google-workspace-user`).** Never use Indigo's account for any Weave operation. The `TOKEN_PATH` in `google_sync.py` is hardcoded to `google-workspace-user.json`. Violation silently fetches wrong contact data.

**ALWAYS sync Contacts after changes** via `scripts/google_sync.py`. This is the canonical sync path — never skip it after a contact mutation.

## Responsibility Boundary

Weave owns the social relationship graph: people, relationships, preferences, and shared experiences. It is the only skill that writes to its SQLite database (`weave.sqlite`).

**Backend**: SQLite with WAL mode via `weave_sqlite.WeaveDB`. Replaced LadybugDB in June 2026. See `references/sqlite-backend-research.md` for the evaluation and migration details.

Weave does not: perform OSINT research (Scout), manage calendars (Sands), organize files (Bower), or build the long-term knowledge graph (Elephas).

## Ontology types

- **Entity/Person** — people in the social graph. Weave extracts and manages Person entities exclusively.

Weave may optionally emit Signals to Elephas for Person nodes with high-confidence identity markers.

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
- **weave.query** — Query the graph. Modes: `lookup`, `connection`, `serendipity`, `city`, `summarize`, `gift`. Return only stored facts with provenance.
- **weave.attach** — Query an external skill database read-only.
- **weave.export** — Export data via `COPY TO`.
- **weave.sync.google-contacts** — Bidirectional Google Contacts sync. Read `references/connectors.md`.
- **weave.sync.clay** — Bidirectional sync with Clay.
- **weave.project.vcard** — Generate vCard 4.0 draft.
- **weave.writeback.contacts** — Push records to Google Contacts or Clay. Disabled by default.
- **weave.init** — Diagnostic and repair.
- **weave.status** — Report graph health and config state.
- **weave.journal** — Write journal for the current run.
- **weave.update** — Pull latest skill package from GitHub.

## Run completion

After every Weave command:
1. Persist any new or updated records to the database.
2. Log material decisions to `decisions.jsonl`.
3. Write journal via `weave.journal`.
4. **Read-back verification**: After every write, immediately query the DB by primary key. Confirm written data matches intent. Never claim success unconfirmed.

## Provenance

Every written fact requires: `source_type` (direct / inferred / imported / user-stated), `source_ref`, `record_time` (ISO 8601), `confidence` (0.0–1.0).

## Contact enrichment lifecycle

Read `references/enrichment-pipeline.md` for the full overnight pipeline architecture.

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
- Outbound requires `writeback.google_contacts: true` AND a previous sync checkpoint.

## Recovery behavior

See `references/recovery-weave.md` for the full recovery contract.

## Constraints

See `references/constraints.md` for the full constraint set.

## Gotchas

See `references/gotchas-weave.md` for the full gotcha catalog including:
- SQLite WAL mode and concurrent access
- Google OAuth token handling and cross-account contamination
- LinkedIn profile fetching
- Python environment (no liblbug.so needed)
- Cron mode constraints and workarounds
- Contact merge diagnosis and repair

## OKRs

Read `references/okrs.md` for Weave-specific OKR definitions and targets.

## Optional skill cooperation

- Elephas — read Chronicle for entity enrichment; journal entity observations
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
| `weave:sync-google` | `0 4 * * *` | `AGENT_ROOT=<hermes-home> HOME=/root python3 -u {skill_root}/scripts/google_sync.py` |
| `weave:enrichability-recalc` | `0 1 * * *` | `python3 {skill_root}/scripts/recalculate_enrichability.py` |

## Agent-Driven Overnight Enrichment

When the enrichment pipeline is run as a cron job (agent-driven, not script-driven), the agent has access to all MCP tools including web_search, web_extract, Composio LinkedIn, and SearXNG. In this mode:

### Script Dependencies
- **`enrichment_data.py` does NOT exist on disk.** Do not attempt to run it. Instead:
  - For SearXNG health: `curl -s "http://localhost:8888/search?q=test&format=json&limit=3"`
  - For contacts with gaps: query WeaveDB directly (`SELECT p.id, p.name, ... FROM persons p LEFT JOIN edges e ... HAVING occupation IS NULL OR org IS NULL`)
  - For writing enrichment: use `WeaveDB.execute_write()` directly
  - For stats: query WeaveDB directly

### LadybugDB Bridge
- The `ladybug-bridge-weave.service` **no longer exists** after the SQLite migration (June 2026). Do not attempt to stop/start it. The SQLite backend does not require it.

### Google OAuth Failure Handling
- If `google_sync.py` fails with HTTP 401 / `invalid_grant`, the refresh token has been revoked. **The script now exits with code 2 and a clean ABORT message** (no traceback). Log the failure and continue with enrichment using MCP tools. Do not halt the entire pipeline. owner must re-authorize OAuth manually — there is no programmatic workaround.
- When Google sync is unavailable, enrichment data can still be gathered via web_search, web_extract, Composio web tools, and direct page fetching (curl + Jina Reader).

### Page Fetching
- `web_extract` fails with SearXNG backend ("search-only backend cannot extract URL content"). Use `curl -s "https://r.jina.ai/URL"` for page content fetching instead.
- LinkedIn profiles: direct HTTP with browser User-Agent works; Jina Reader is blocked for LinkedIn. Composio `LINKEDIN_GET_PERSON` requires a `person_id` (not username) — there is no name-search tool.

## Self-update

`weave.update` pulls the latest package from GitHub. See `references/self-update.md`.

## Database maintenance

See `references/database_maintenance.md`.
See `references/graph-storage-backend-research.md` for the full evaluation of alternatives to LadybugDB (SQLite adjacency lists recommended).

## Pitfalls

- **Never leave broken scripts after a migration**: When you migrate a shared backend (like LadybugDB → SQLite), you MUST update ALL scripts that depend on it in the same session. Do not wait to be told. Check `grep -rl "old_import" scripts/` and fix every hit.
- **Fix known issues immediately without asking**: When you identify a problem and know how to fix it, apply the fix immediately. Do not ask "should I fix this?" or wait for the user to tell you. If something is broken and the fix is clear, just fix it.
- **Do not ask confirmation on approved plans**: When the user says "yes" to a plan, execute immediately. Do not re-ask "should I proceed?" or present alternatives after approval.
- **Module-level imports**: Python names imported inside `if __name__ == "__main__"` are NOT visible to module-level functions. Import `timedelta`, `sqlite3`, and all other dependencies at the top of the file.
- **Shared auth module**: All Google OAuth + API call logic lives in `scripts/google_api.py`. Import from there — never duplicate `get_access_token`, `api_get`, `api_post`, or `api_patch` in individual scripts. The shared module handles token refresh, rate-limit backoff, and error handling consistently.
- **SQLite FK constraints on polymorphic references**: The `edges.target_id` is a polymorphic reference (can point to `persons.id`, `facts.id`, or `preferences.id` depending on `rel_type`). Do NOT add `FOREIGN KEY (target_id) REFERENCES persons(id)` — it breaks `HasFact` and `HasPreference` edges. If the FK already exists in a live DB, use a migration script to recreate the table without it (see `scripts/migrate_edges_fk.py` for the pattern).
- **Schema code vs live DB divergence**: `CREATE TABLE IF NOT EXISTS` in `weave_sqlite.py` only runs on first DB creation. Schema fixes in code do NOT apply to existing DBs. Always write an explicit migration script when changing DDL on a live database, and verify row counts before/after.
- **Script name references**: When referencing other scripts in subprocess calls or Popen, verify the filename exactly matches what's on disk. `enrichment_control.py` referenced `overnight_weave_enrichment.py` but the actual file is `overnight_enrichment.py` — always `ls scripts/` to confirm.
- **Enrichment write pattern consistency**: When writing enrichment data to Weave, always use `weave.execute_write()` / `weave.execute()` (the WeaveDB abstraction layer) rather than raw `sqlite3` connections. Raw connections bypass FK enforcement, skip WAL mode, and can leave the DB in an inconsistent state. The only exception is bulk import via `weave.bulk_import()` which manages its own connection lifecycle.
- **Shared enrichment extraction**: All web scraping, content extraction, and field validation logic lives in `scripts/weave_enrich.py`. Both `quick_enrich.py` and `overnight_enrichment.py` import from it. When modifying extraction patterns (regex, validation rules, search queries), update `weave_enrich.py` — never edit duplicated copies in individual scripts.
- **Post-migration reference drift**: After a backend migration (e.g., LadybugDB → SQLite), ALL reference files must be audited — not just code. `schemas.md`, `gotchas-weave.md`, `connectors.md`, and any file with code examples or schema docs will silently drift. Check every `.md` in `references/` for stale imports, old DB paths, deprecated query languages, and outdated CLI commands. Orphaned reference files (not linked from SKILL.md) should be archived or deleted.
- **WeaveDB default path calculation**: In `scripts/weave_sqlite.py`, `AGENT_ROOT = Path(__file__).resolve().parents[2]` goes up 2 levels from the script, but the skill lives at `profiles/indigo/skills/ocas-weave/scripts/`. The correct path is `parents[3]` to reach `profiles/indigo/`. Using `parents[2]` points to `skills/` which has a stale/empty `commons/db/ocas-weave/weave.sqlite`. This causes silent write failures — the DB opens but has no data. Verify `DEFAULT_DB_PATH` resolves to the expected location on first import.
- **Function signature drift in pipeline scripts**: `overnight_enrichment.py` called `sift_extract_from_pages(name, org, all_results, max_pages=3)` but the function signature is `sift_extract_from_pages(name, search_results, max_pages=3)`. The extra `org` argument shifted `all_results` into `max_pages` and `max_pages=3` was ignored. Always verify function signatures match when calling shared functions from `weave_enrich.py`.
- **Enrichment field validation too permissive (PATCHED 2026-06-18)**: `validate_field()` in `weave_enrich.py` previously allowed garbage through: sentence fragments as occupations ("As the Editorial Director"), city names as org ("Chicago", "Los Angeles"), single-word generic orgs ("Professional", "Accidents", "per", "newsletter"), partial org names ("was", "Updates", "Product", "San"), invalid locations ("Teague, CP"), junk emails ("leaflet@1.9.4"), and duplicate fact inserts. **Fix applied**: org validation now rejects known STATIC_CITIES, generic non-company words, sentence fragments (was/were/been/have/has), values without uppercase letters, and single-character values. Occupation validation requires title-case tokens. Always deduplicate facts before insert.
- **SearXNG connection resets under load**: During overnight enrichment, SearXNG can return `Connection reset by peer` or `Remote end closed connection without response` errors when hit with rapid sequential searches. Add retry logic with exponential backoff (3 attempts, 2s/4s/8s delays) to `searxng_search()` in `weave_enrich.py`.
- **overnight_enrichment.py duplicate processing**: The script's progress tracking does not prevent re-processing contacts that were already enriched in a previous run. If interrupted and restarted, contacts appear in the progress file but may already have facts written. The script also writes duplicate facts (same predicate/value for the same person) when `enrich_weave_contact()` is called multiple times for the same contact. Always deduplicate after enrichment runs: `SELECT source_id, predicate, value, COUNT(*) FROM facts f JOIN edges e ON f.id = e.target_id WHERE e.rel_type = 'HasFact' GROUP BY source_id, predicate, value HAVING COUNT(*) > 1`.
- **Data quality red flags for org values**: Reject org values that are: (1) known city names, (2) single generic words (Professional, Employees, Newsletter), (3) sentence fragments containing verbs like "was"/"were"/"been", (4) values without any uppercase letters, (5) email addresses or URLs, (6) values matching the person's own name.
- **google_api.py silent refresh failure (PATCHED 2026-06-20)**: `get_access_token()` in `google_api.py` had two compounding bugs: (1) the credential file stores `expiry` as a Unix timestamp float (e.g., `1781939144.66`) but the code called `datetime.fromisoformat(expiry)` which throws `ValueError` on a float; (2) the `except Exception: pass` silently swallowed the error and returned the expired token without refreshing. The Google People API then returns HTTP 401. **Fix**: check `isinstance(expiry, (int, float))` and use `datetime.fromtimestamp()` for numeric values, otherwise fall back to `fromisoformat()`. After fixing, also verify the refresh token itself hasn't been revoked — `invalid_grant` from the token endpoint means the OAuth consent flow must be re-completed by owner.
- **google_sync.py unhandled auth failure (PATCHED 2026-06-20)**: Even after `get_access_token()` was fixed to raise `RuntimeError` on `invalid_grant`, the `google_sync.py` `__main__` entry point had no try/except — it let the exception propagate as a raw traceback and exit code 1. Cron jobs should never crash with tracebacks. **Fix**: wrap `main()` in a try/except that catches `RuntimeError` containing "refresh token revoked" and exits with code 2 and a clean `ABORT` message to stderr. This distinguishes auth failures (exit 2) from other crashes (exit 1) and avoids noisy cron alerts for a known unrecoverable state.
- **Credential file managed by MCP server**: The file at `/root/.google_workspace_mcp/credentials/google-workspace-user.json` is written by the google_workspace MCP server, which may overwrite `expiry` back to a float after a refresh. Always handle BOTH float and ISO format in `get_access_token()`. If the MCP server overwrote the file between your `json.dump` and the next read, the fix is still safe because it handles both formats.
- **`enrichment_data.py` does not exist**: There is no `enrichment_data.py` on disk. Use direct WeaveDB queries and `curl` for SearXNG health. See the "Agent-Driven Overnight Enrichment" section above.
- **`web_extract` cannot fetch URLs with SearXNG backend**: Use `curl -s "https://r.jina.ai/URL"` instead. This is the reliable page-fetching method in cron/agent context.
- **LadybugDB bridge removed**: `ladybug-bridge-weave.service` no longer exists. Skip stop/start bridge steps in the enrichment pipeline.

## Support File Map

| File | When to read |
|------|-------------|
| `references/schemas.md` | Before any DDL, upsert, or import — Python usage pattern and schema |
| `references/gotchas-weave.md` | Before any Weave operation — full gotcha catalog |
| `references/query_patterns.md` | Before any weave.query call — SQL templates for all modes |
| `references/connectors.md` | Before any Google/Clay sync |
| `references/sqlite-backend-research.md` | Storage backend details, migration notes, SQLite schema |
| `references/enrichment-pipeline.md` | Overnight enrichment architecture, SearXNG retry pattern |
| `references/constraints.md` | Full constraint set |
| `references/config-defaults.md` | Default config structure |
| `references/self-update.md` | Self-update procedure |
| `references/enrichment-data-quality.md` | Data quality patterns, garbage categories, validation rules, SearXNG reliability |
| `references/recovery-weave.md` | Recovery contract details |
| `scripts/weave_sqlite.py` | SQLite backend module — import `WeaveDB` from here |
| `scripts/google_api.py` | Shared Google OAuth + API helpers — import `get_access_token`, `api_get`, `api_post`, `api_patch`, `PEOPLE_API_BASE` from here. All scripts that talk to Google APIs should use this module, not duplicate auth logic. |
| `scripts/migrate_ladybugdb_to_sqlite.py` | One-time migration script (already run June 2026) |
| `scripts/migrate_edges_fk.py` | FK migration: removes incorrect `FOREIGN KEY (target_id)` from edges table. Run once; safe to re-run (idempotent). |
| `scripts/weave_enrich.py` | Shared enrichment extraction, search, and validation. Contains `searxng_search`, `fetch_page`, `extract_from_content`, `validate_field`, `is_auth_walled`, `build_scout_queries`. Used by both `quick_enrich.py` and `overnight_enrichment.py` — do not duplicate this logic in individual scripts. |

## Visibility

public
