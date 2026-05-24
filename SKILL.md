---
name: ocas-weave
description: 'Weave: private provenance-backed social graph. Maintains queryable records
  of people, relationships, preferences, and shared experiences for recall, gifting,
  hosting, introductions, and serendipity. Trigger phrases: ''who do I know in'',
  ''what does X like'', ''add this person'', ''relationship with'', ''gift ideas for'',
  ''sync contacts'', ''prepare for meeting with'', ''update weave''. Use when storing
  or retrieving facts about a person, recording a relationship, or discovering connections
  between people.

  '
license: MIT
metadata:
  author: Indigo Karasu
  version: 3.5.0
---

# Weave

Weave maintains a private, provenance-backed social graph of people, relationships, preferences, and shared experiences — queryable for meeting prep, gift ideas, hosting, introductions, city connections, and serendipity discovery. Every stored fact carries source type, reference, timestamp, and confidence score; the graph never silently merges two person records and never writes back to external systems without explicit per-sync approval. All queries use Cypher — no SQL. The database initializes automatically on first use.

## Responsibility boundary

Weave owns the social relationship graph: people, relationships, preferences, and shared experiences. It is the only skill that writes to its LadybugDB database.

Weave does not: perform OSINT research (Scout), manage calendars (Sands), organize files (Bower), or build the long-term knowledge graph (Elephas). Entity disambiguation queries to Weave are read-only for all other skills.

## When to use

- Store or update information about a person, relationship, or preference
- Look up who someone is, how they relate to others, or what they like
- Prepare for a meeting, dinner, or introduction
- Find who you know in a given city
- Generate gift ideas grounded in known preferences
- Discover serendipity connections between people
- Sync contacts from Google Contacts or Clay

## When not to use

- Web research without a social graph need — use Sift
- OSINT investigations on people — use Scout
- CRM or sales pipeline automation
- Personality profiling without evidence

## Ontology types

Weave works with these types from `spec-ocas-ontology.md`:

- **Entity/Person** — people in the social graph. Weave extracts and manages Person entities exclusively.

Weave may optionally emit Signals to Elephas for Person nodes with high-confidence identity markers, but this is not required for normal operation.

Each Signal emitted to Elephas must include a `user_relevance` field: `user` if the entity is directly related to the user's world, `agent_only` if encountered incidentally, `unknown` if unclear. Weave entities are almost always `user`-relevant since they represent the user's actual social connections.

## LadybugDB usage guide

Read `references/ladybugdb-guide.md` for query result handling, iteration pitfalls, and import patterns with code examples.

## Storage layout

```
{agent_root}/commons/data/ocas-weave/
  intents.jsonl        — pending intents queued for retry (degraded mode)
  evidence.jsonl       — evidence records for every sync/write run
  sync_log.jsonl       — sync activity log

{agent_root}/commons/db/ocas-weave/
  weave.lbug          — LadybugDB database (auto-created on first use)
  config.json         — connector and sync configuration
  staging/            — temporary import/export files

{agent_root}/commons/journals/ocas-weave/
  YYYY-MM-DD/
    {run_id}.json     — one journal per run
```

Default config.json:
```json
{
  "skill_id": "ocas-weave",
  "skill_version": "2.3.0",
  "config_version": "1",
  "created_at": "",
  "updated_at": "",
  "writeback": {
    "google_contacts": false,
    "clay": false
  },
  "last_sync": {
    "google_contacts": null,
    "clay": null
  },
  "retention": {
    "days": 0
  }
}
```

## Database rules

LadybugDB is an embedded single-file database. One `READ_WRITE` process at a time. If another process holds the lock, operations fail immediately with a lock error — surface the error, do not retry silently.

Multiple `READ_ONLY` connections are safe simultaneously. `COPY FROM` is for bulk import (>100 rows). `MERGE` is for sporadic single-record upserts. Never loop `MERGE` over bulk data.

## Auto-initialization

Every command that opens the database runs `_ensure_init()` first. No manual init command is needed on first use.

Read `references/init_pattern.md` for the `_open_db` implementation pattern. Full DDL is in `references/schemas.md`.

**Config.json missing**: Health checks should verify `config.json` exists in `{agent_root}/commons/db/ocas-weave/`. If missing, run `weave.init` to trigger auto-creation via `_ensure_init()`.

## Commands

**weave.upsert.person** -- Add or update a person. Auto-inits DB on first call. MERGE on `id`. Read back after write; report failure if no row returned — never claim success unconfirmed.

**weave.upsert.relationship** -- Add or update a `Knows` edge. Confirm both Person nodes exist first. Halt and report which is missing.

**weave.upsert.preference** -- Store a provenance-backed preference. Each preference is a distinct `CREATE` (not merged). Link to Person via `HasPreference` edge.

**weave.import.csv** -- Bulk import contacts via `COPY FROM`. Read `references/import_export.md`. Pre-process CSV to staging dir first. Check `CALL show_warnings() RETURN *` after. Report: N imported, N skipped (with reasons), N failed.

**weave.query** -- Query the graph. Read `references/query_patterns.md`. Modes: `lookup`, `connection`, `serendipity`, `city`, `summarize`, `gift`. Return only stored facts with provenance. Never speculate.

**weave.attach** -- Query an external skill database read-only. Read `references/cross_db.md`.

**weave.export** -- Export data to staging dir via `COPY TO`. Read `references/import_export.md`.

**weave.sync.google-contacts** -- Run bidirectional Google Contacts sync. Read `references/connectors.md` before any sync. Outbound requires `writeback.google_contacts: true` in config.

**weave.sync.clay** -- Bidirectional sync with Clay. Read `references/connectors.md`. Outbound requires `writeback.clay: true` AND explicit approval.

**weave.project.vcard** -- Generate vCard 4.0 draft. Read `references/vcard_projection.md`. Omit fields with confidence below 0.7. Requires explicit approval before writeback.

**weave.writeback.contacts** -- Push records to Google Contacts or Clay. Disabled by default. Requires config enablement AND per-action user approval.

**weave.init** -- Diagnostic and repair. Checks schema, creates missing tables, verifies indexes.

**weave.status** -- Report graph health and config state.

```cypher
CALL show_tables() RETURN *;
MATCH (p:Person) RETURN count(p) AS people;
MATCH ()-[r:Knows]->() RETURN count(r) AS relationships;
MATCH (pref:Preference) RETURN count(pref) AS preferences;
CALL show_warnings() RETURN *;
```

**weave.journal** -- Write journal for the current run. Read `references/journal.md`. Called at end of every run. Journals are immutable after write.

**weave.update** -- Pull latest skill package from GitHub source. Preserves journals and data.

## Run completion

After every Weave command that reads or writes data:

1. Persist any new or updated records to the database
2. Log material decisions to `decisions.jsonl`
3. Write journal via `weave.journal` — Observation Journal for queries/upserts/imports, Action Journal for syncs/writebacks

## Provenance

Every written fact requires: `source_type` (direct / inferred / imported / user-stated), `source_ref`, `record_time` (ISO 8601), `confidence` (0.0–1.0). Use `event_time` when the real-world occurrence has a distinct time. Never write facts without provenance.

## Contact enrichment lifecycle

Read `references/enrichment-pipeline.md` for the full overnight pipeline architecture.

For manual enrichment of high-value contacts, use the full quality pipeline:

1. **Pre-Search Seed Quality Check** — Check `source_type` and `confidence`. If `web_enrichment` with confidence < 0.8: reject org/location/occupation. Use only name + email + phone (trusted signals).
2. **Read** — Query Weave for the existing Person record. Confirm it exists.
3. **Search** — Use SearchX (SearXNG via `execute_code`) for identity-resolved research. Fallback to `web_search`.
4. **Enrich (Scout → Sift → Sherlock)**:
   - **Scout**: Targeted SearXNG queries using name + org. URLs/snippets are discovery only, NOT extraction source.
   - **Sift**: Fetch full page content from top non-auth-walled URLs (direct HTTP for static, Jina Reader for JS-heavy). Extract structured data from full pages. NEVER extract from search snippets (~160 chars → truncated/garbage).
   - **Sherlock**: Run `sherlock --print-found --no-color '<username>' --timeout 90`. Verify 2+ data points per result. For verified profiles that self-link other platforms, treat linked platforms as high-quality.
5. **Write** — MERGE on Person, CREATE on Fact/Preference. Always read back.
6. **Search again** — Follow-up with enriched data (company, location, title).
7. **Sync** — After writes touching mapped fields, sync to Google Contacts.

### Implicit data from user language

When the user uses pronouns or relationship terms (wife, husband, partner, brother, etc.), extract and store as high-confidence Facts (`source_type: 'user-stated'`, `confidence: 0.99`).

## Google Contacts sync

Weave maintains bidirectional sync via `scripts/google_sync.py` (inbound then outbound in one invocation, sharing 90 req/min quota).

- Match by `google_resource_name`, then email, then phone. Never match on name alone.
- Gap-fill only — Weave provenance wins conflicts.
- Outbound requires `writeback.google_contacts: true` in config AND a previous sync checkpoint.
- **MUST snapshot contacts before outbound push** (see `references/connectors.md`).
- Full field sync is mandatory — all fields from `references/google-field-map.md`, including birthdays and relations from Fact/Knows nodes.
- OAuth: read scopes `contacts` + `contacts.readonly`, write-back needs `contacts`. Pre-flight token check: `references/google-token-quick-check.md`.
- Token corruption scripts: see `references/google-token-diagnostics.md` for TOKEN_PATH fixes and byte-level verification.

## Recovery behavior

This skill implements the recovery contract from `spec-ocas-recovery.md`.

- **Evidence**: Every sync/write run writes an evidence record to `{agent_root}/commons/data/ocas-weave/evidence.jsonl`, including no-op runs. `not_activity_reason` is mandatory for no-op runs.
- **Gap detection**: On every wake, checks the evidence log. If gap exceeds cadence (24h for sync-google, 1h for enrichability-recalc), logs `gap_detected`.
- **Degraded mode**: When dependencies are unavailable, logs `degraded: <dependency>` and queues changes. Existing data remains queryable.
- **Log compaction**: Evidence/decision logs older than 30 days (no-op) or 90 days (error/gap) compacted. Sync checkpoints never auto-deleted. Last 7 days retained.

## Constraints

- Never use SQL.
- Never report a write as successful before read-back confirms it.
- Never parse or modify `.lbug`, `.wal`, `.shadow`, or `.tmp` files directly.
- Never write to Chronicle or any other skill's database.
- Never silently collapse two Person nodes into one.
- Use ontology standard relationship types in `Knows.rel_type`.
- Store useful, durable, socially actionable facts only.
- **No outbound sync without explicit per-sync user approval.**
- No notes field for structured data — Person.notes column was dropped. Store metadata as Fact nodes with typed predicates.
- Surface lock errors immediately.
- Write a journal at the end of every run. Runs missing journals are invalid.
- Before outbound Google sync, verify Person-level fields are populated. Fact node data is NOT auto-synced to Google — aggregation step required.

## Pitfalls

- **Separate enrichment skills**: All enrichment workflow lives in this skill. Merge any accidental duplicates.
- **`HasFact` has no properties**: `CREATE (p)-[:HasFact]->(f)` only — no property bags allowed.
- **Wrong enrichment pipeline**: Manual enrichment = full Scout→Sift→Sherlock pipeline. Do NOT shortcut with raw SearXNG regex alone.
- **Inline credential code triggers security auditors**: All credential diagnostics live in `references/google-token-diagnostics.md`.
- **Tool output truncation**: `read_file`/`terminal` may truncate paths (e.g., `/root/...json`). This is a display artifact — verify with raw reads before fixing. In Apr–May 2026, 25+ minutes were lost to this false positive.
- **TOKEN_PATH corruption**: Can be caused by `read_file` truncation being written back, or sed/asterisk replacement. See `references/google-token-diagnostics.md`.
- **`write_file` line number prefix injection**: Using `write_file` after `read_file` can inject line numbers into scripts. Use direct Python file I/O or `sed` instead.
- **Auth migration misses**: When updating Google auth, check ALL Python scripts — skill scripts, data directory scripts, webui scripts.
- **google_sync.py `Person.notes` column**: May reference dropped `Person.notes`. Remove all references to `notes` in MERGE, CREATE, RETURN, `build_contact_body`, and parameter dicts (5 locations). Verify with: `python3 -c "import ast; ast.parse(open('scripts/google_sync.py').read()); print('OK')"`.
- **Enrichment scraper substring bug**: Occasional truncated text extraction. Clear corrupted fields and fix scraper before re-running.

## OKRs

Universal OKRs from spec-ocas-journal.md apply. Weave-specific:

```yaml
skill_okrs:
  - name: person_record_completeness
    metric: fraction of Person nodes with name + (email or phone) + record_time
    direction: maximize
    target: 0.80
    evaluation_window: 30_runs
  - name: sync_success_rate
    metric: fraction of sync runs with zero failed records
    direction: maximize
    target: 0.90
    evaluation_window: 30_runs
  - name: import_skip_rate
    metric: fraction of imported rows skipped due to missing required fields
    direction: minimize
    target: 0.05
    evaluation_window: 30_runs
  - name: query_provenance_coverage
    metric: fraction of returned facts carrying source_ref and record_time
    direction: maximize
    target: 1.0
    evaluation_window: 30_runs
  - name: schedule_adherence
    metric: fraction of scheduled runs completed on time
    direction: maximize
    target: 0.95
    evaluation_window: 30_runs
  - name: data_integrity
    metric: fraction of sync runs with no corrupted data
    direction: maximize
    target: 0.98
    evaluation_window: 30_runs
```

## Optional skill cooperation

- Elephas — read Chronicle read-only for entity enrichment (degrades gracefully)
- Elephas — journal entity observations consumed during Chronicle ingestion
- Scout — receive OSINT findings about people as upsert candidates
- Dispatch — provide social graph context for communication drafting
- **Clay (Mesh MCP)** — CRM sync via Smithery (`clay-inc/clay-mcp`). Old Clay REST API is deprecated. Auth is OAuth (not API key). Install: `npx -y @smithery/cli@latest mcp add clay-inc/clay-mcp`.

## Journal outputs

- Observation Journal — query runs, upsert runs, import runs
- Action Journal — sync runs, writeback runs

Journals include `entities_observed`, `relationships_observed`, `preferences_observed` in `decision.payload`, each with `user_relevance` field.

## Initialization

On first invocation of any Weave command, `_open_db()` handles auto-initialization:

1. Create directories and default `config.json`
2. Open database (auto-creates `weave.lbug` and runs DDL if needed)
3. Register cron job `weave:update` if not present
4. Log initialization as a DecisionRecord

## Background tasks

| Job name | Mechanism | Schedule | Command |
|---|---|---|---|
| `weave:update` | cron | `0 0 * * *` (midnight daily) | `weave.update` |
| `weave:sync-google` | cron | `0 4 * * *` (4AM UTC) | `python3 {skill_root}/scripts/google_sync.py` |
| `weave:enrichability-recalc` | cron | `0 1 * * *` (1am daily) | `python3 {skill_root}/scripts/recalculate_enrichability.py` |

## Self-update

`weave.update` pulls the latest package from GitHub. See `references/self-update.md`.

## Database Maintenance

See `references/database_maintenance.md`. Key uses: before Google sync, after enrichment runs, periodic health checks.

## Support file map

| File | When to read |
|---|---|
| `references/schemas.md` | Before any DDL, upsert, or import |
| `references/init_pattern.md` | Implementing _open_db or troubleshooting init |
| `references/ladybugdb-guide.md` | Query result handling, iteration, import patterns |
| `references/query_patterns.md` | Before any weave.query call |
| `references/import_export.md` | Before any COPY FROM/TO operation |
| `references/cross_db.md` | Before weave.attach or Chronicle queries |
| `references/connectors.md` | Before any Google/Clay sync |
| `references/vcard_projection.md` | Before weave.project.vcard |
| `references/journal.md` | Before weave.journal; at end of every run |
| `references/google-token-quick-check.md` | Pre-flight token validation before sync |
| `references/google-token-diagnostics.md` | Token diagnostic, TOKEN_PATH fixes |
| `references/enrichment-pipeline.md` | Overnight enrichment architecture & health checks |
| `references/sync-pitfalls.md` | Google sync API quota and known pitfalls |
| `references/self-update.md` | Self-update procedure |

## Update command

```bash
weave.update
```

## Visibility

public
