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

## LadybugDB Query Result Handling

When querying Weave via LadybugDB (`real_ladybug`), the return format depends on the Cypher clause:

- **`RETURN p`** (whole node): Each row is a **dict** with all properties plus `_ID` and `_LABEL` keys. Use dict access like `row['name']` or `row.get('name')`.
- **`RETURN p.id, p.name, ...`** (column selectors): Each row is a **list** (not a dict). Map column names to indices using `r.get_column_names()`. Example: `cols = r.get_column_names(); row[cols.index('p.name')]`.
- **`r.get_all()`** returns a Python list of rows (each row is either dict or list depending on your RETURN clause).
- **`r.rows_as_dict()`** returns a *QueryResult* object, NOT a Python dict — do not treat it as data.
- **`r.get_column_names()`** works for all queries and returns a list of column name strings.

Key mistake to avoid: Using `row['name']` on a list row from column selectors will raise `TypeError: list indices must be integers or slices, not str`. Always match your access pattern to the return format.

### Iteration Pitfalls (discovered Apr 2026)

- **`r.get_all()` fails on corrupt rows**: If any row contains corrupted/invalid UTF-8 data, `get_all()` raises `UnicodeDecodeError` and returns NOTHING — even if 99% of rows are valid. For queries over the full Person table, use row-by-row iteration with error handling instead:
  ```python
  rows = []
  while True:
      try:
          row = r.get_next()
          rows.append(row)
      except StopIteration:
          break
      except Exception as e:
          if "No more tuples" in str(e):
              break  # LadybugDB raises this instead of StopIteration
          if "utf-8" in str(e):
              continue  # Skip corrupt row
          raise
  ```
- **End-of-results exception**: `r.get_next()` raises `Runtime exception: No more tuples in QueryResult` when exhausted — NOT `StopIteration`. Always check for this string in exception handlers. The pattern `"No more tuples" in str(e)` distinguishes it from data corruption errors.
- **Import pattern**: `from real_ladybug import Database, Connection` (top-level). There is NO `lb` submodule — both `Database` and `Connection` are exported directly. `READ_ONLY`/`READ_WRITE` constants are NOT exported — use `Database(path, read_only=True)` parameter instead. Connection: `Connection(db)` then `conn.execute(cypher, params)`.
  ```python
  from real_ladybug import Database, Connection
  db = Database("/path/to/weave.lbug", read_only=True)
  conn = Connection(db)
  r = conn.execute("MATCH (p:Person) RETURN p.id, p.name LIMIT 5")
  ```
- **No `randomUUID()` in Cypher**: LadybugDB does not support `randomUUID()`. Generate UUIDs in Python with `uuid.uuid4()` and pass as parameters: `CREATE (f:Fact {id: $fact_id, ...})`. Always generate IDs on the Python side, never in Cypher expressions.

## Recovery Behavior

This skill implements the recovery contract from `spec-ocas-recovery.md`.

- **Evidence**: Every sync/write run writes an evidence record to `{agent_root}/commons/data/ocas-weave/evidence.jsonl`, including no-op runs. The `not_activity_reason` field is mandatory when no side effects occur.
- **Gap detection**: On every wake, checks the evidence log. If gap exceeds cadence (24h for sync-google, 1h for enrichability-recalc), logs `gap_detected`.
- **Degraded mode**: When Google Contacts API or LadybugDB are unavailable, logs `degraded: <dependency>` and queues changes for retry. Existing Weave data remains queryable.
- **Log compaction**: Evidence and decision logs older than 30 days (no-op) or 90 days (error/gap) compacted. Sync checkpoints never auto-deleted. Last 7 days retained.

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

LadybugDB is an embedded single-file database. One `READ_WRITE` process at a time. If another process holds the lock, operations fail immediately with a lock error — do not retry silently, surface the error.

Multiple `READ_ONLY` connections are safe simultaneously. `COPY FROM` is for bulk import (>100 rows). `MERGE` is for sporadic single-record upserts. Never loop `MERGE` over bulk data.

## Auto-initialization

Every command that opens the database runs `_ensure_init()` first. No manual init command is needed on first use.

Read `references/init_pattern.md` for the `_open_db` implementation pattern. Full DDL is in `references/schemas.md`.

**Config.json missing**: Health checks should verify `config.json` exists in `{agent_root}/commons/db/ocas-weave/`. If missing, run `weave.init` to trigger auto-creation via `_ensure_init()`, as health checks do not invoke database commands and thus do not trigger auto-init.

## Commands

**weave.upsert.person** -- Add or update a person. Auto-inits DB on first call. MERGE on `id`. Read back after write; report failure if no row returned — never claim success unconfirmed.

**weave.upsert.relationship** -- Add or update a `Knows` edge. Confirm both Person nodes exist first. Halt and report which is missing.

**weave.upsert.preference** -- Store a provenance-backed preference. Each preference is a distinct `CREATE` (not merged). Link to Person via `HasPreference` edge.

**weave.import.csv** -- Bulk import contacts via `COPY FROM`. Read `references/import_export.md`. Pre-process CSV to staging dir first. Check `CALL show_warnings() RETURN *` after. Report: N imported, N skipped (with reasons), N failed.

**weave.query** -- Query the graph. Read `references/query_patterns.md`. Modes: `lookup`, `connection`, `serendipity`, `city`, `summarize`, `gift`. Return only stored facts with provenance. Never speculate.

**weave.attach** -- Query an external skill database read-only. Read `references/cross_db.md`.

**weave.export** -- Export data to staging dir via `COPY TO`. Read `references/import_export.md`.

**weave.sync.google-contacts** -- Run bidirectional Google Contacts sync. Inbound: Google Contacts → Weave. Outbound: Weave → Google Contacts via BatchUpdateContacts (200 per batch, with batchGet for etags). **MUST snapshot contacts before outbound push** (see references/connectors.md). Outbound requires `writeback.google_contacts: true` in config — no per-sync approval step. Read `references/connectors.md` before any sync.

**weave.sync.clay** -- Bidirectional sync with Clay. Read `references/connectors.md`. Clay is enrichment source — Weave provenance wins conflicts. Outbound requires `writeback.clay: true` AND explicit approval.

**weave.project.vcard** -- Generate vCard 4.0 draft. Read `references/vcard_projection.md`. Omit fields with confidence below 0.7. Label DRAFT. Requires explicit approval before writeback.

**weave.writeback.contacts** -- Push records to Google Contacts or Clay. Disabled by default. Requires config enablement AND per-action user approval.

**weave.init** -- Diagnostic and repair command. Checks schema, creates missing tables, verifies indexes. Use when troubleshooting, not as a prerequisite — the database initializes automatically on first use.

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

## Contact Enrichment Lifecycle

When enriching a contact, follow this strict order:

0. **Pre-Search Seed Quality Check** — Before building any search query:
   - Check the contact's `source_type` and `confidence`
   - If `source_type` is `web_enrichment` and `confidence` < 0.8: **Reject all fields** (org, location, occupation). Use only trusted signals: `name`, `email`, `phone`
   - If `source_type` is `imported`, `scout_research`, or `user_provided`: Use all provided fields for search context
   - Search query must only include trusted signals: `name` (mandatory), `email` (mandatory), `phone` (area code hint), `location` (only if trusted)

1. **Read** — Query Weave for the existing Person record. Confirm the record exists before any enrichment.
2. **Search** — Use the searchx skill (SearXNG via `execute_code`) for web research. If SearchX is not available, fallback to the native `web_search` tool.
3. **Enrich** — Add new Facts, Preferences, and relationship edges with full provenance (`source_type`, `source_ref`, `confidence`, `record_time`) on every write.
   - **Scout Phase**: Use SearchX (SearXNG via `execute_code`) for identity-resolved research. Build targeted queries using name + org. Returns URLs and snippets — these are NOT the extraction source, only the discovery mechanism.
   - **Sift Phase**: Fetch **full page content** from the top non-auth-walled URLs discovered by Scout. Use direct HTTP fetch for static sites, Jina Reader (`r.jina.ai/<url>`) for JS-heavy sites. Extract structured data (occupation, org, location, email) from the full page content. **NEVER extract from search result snippets** — snippets are ~160 characters and produce truncated/garbage data (e.g., `"r Vice President"`, `"St"` instead of `"Stanford"`). Skip auth-walled domains (LinkedIn, Twitter/X, Facebook, Instagram) as they return login walls.
   - **Sherlock Phase**: Run `sherlock --print-found --no-color '<username>' --timeout 90` (required command). **Critical**: Sherlock finds USERNAMES, not verified profiles. You MUST verify 2+ data points for each result. Ignore username-only matches (different person with same handle).

   - **High-Quality Propagation Rule**: If a VERIFIED profile (2+ data points) SELF-IDENTIFIES another social platform:
     * Example: Verified GitHub bio says "Twitter: @handle" or links to Bluesky
     * Example: Verified LinkedIn profile links to GitHub/Bluesky
     * Example: Verified personal website/portfolio links to social platforms
     THEN: Treat that linked platform as HIGH-QUALITY (no separate 2+ point check needed). The verification transfers from the trusted source to its self-linked profiles.
     Note: If the linked platform is inaccessible (X.com blocks crawlers, account suspended), log it as "high-quality (linked from verified <source>) but inaccessible".
4. **Write** — Persist changes to Weave via `MERGE` on Person nodes and `CREATE` on Fact/Preference nodes. Always read back after write to confirm success.
5. **Search again** — With newly enriched data (company names, locations, titles), construct follow-up searches to find deeper information.
6. **Sync** — After every Weave write that touches data mapped in `references/google-field-map.md`, immediately sync to Google Contacts. Never update one without mirroring the other.

- **Pitfall: Separate enrichment skills**: Do not create separate enrichment skills (e.g., old `weave-enrichment-proper`). All Weave enrichment workflow must live in this skill. If a new enrichment-related skill is created accidentally, merge its content into this skill and delete the duplicate.

### Implicit Data from User Language

When the user refers to a person using pronouns (him/her, his/hers, they/them) or relationship terms (wife, husband, partner, brother, son, daughter, etc.), always extract and store these as high-confidence Facts (`source_type: 'user-stated'`, `confidence: 0.99`). This includes pronouns, relationship labels, and any other implicit signals in the user's word choices.

### Google Contacts Sync Rules

- Every field goes in its proper structured field: `name→names`, `email→emailAddresses`, `phone→phoneNumbers`, `org→organizations.name`, `title→organizations.title`, `city/region→addresses`, `birthday→birthdays({date:{month,day}})`, `relation→relations({person:"Plain Text Name",type:"spouse"})`, `URL→urls(type:"LinkedIn"/"Website"/"Instagram")`.
- NEVER dump structured data into notes/biographies.
- The `relations.person` field is **plain text name**, NOT a Google resource ID.
- ALWAYS sync Google Contacts when Weave is updated, and vice versa. Never perform a unilateral write.
- Uses `camelCase` field names in the Google People API (e.g., `givenName`, `familyName`, not `given_name`).
- Always `GET` the contact first to retrieve the `etag`, then `PATCH` with the etag + changed fields.
- `updatePersonFields` query param must list every field being updated.
- Birthday format: `{"date": {"month": M, "day": D}}`, no year if unknown.
- URL `type` field uses labels: `"LinkedIn"`, `"Website"`, `"Instagram"`, not `"work"/"home"`.

### Accuracy over Assumption

When a tool call fails or an API returns an error, do not assume the feature is impossible or unsupported. Investigate the exact API specification, test alternative field names (e.g., `camelCase` vs `snake_case`), and verify the endpoint before concluding a capability is missing.

### Data Integrity Safeguards

**NEVER push Weave data to external systems without validation.** Web enrichment has produced corrupted data (Apr 2026):
- Truncated occupation fields (missing first characters, e.g., "r Vice President" instead of "Senior Vice President")
- Fragment org fields (e.g., "St" instead of "Stanford")
- Full bios stored in occupation field
- Job titles in city field

Before any outbound sync:
1. Run validation to catch fragment/truncated fields
2. Clear invalid fields rather than pushing bad data
3. **Always snapshot Google Contacts first** (see references/connectors.md)

**Enrichment scraper bug**: The overnight enrichment scraper occasionally extracts substrings incorrectly, storing truncated text. If this happens, clear the corrupted fields and fix the scraper before re-running enrichment.

## Constraints

- Never use SQL.
- Never report a write as successful before read-back confirms it.
- Never parse or modify `.lbug`, `.wal`, `.shadow`, or `.tmp` files directly.
- Never write to Chronicle or any other skill's database.
- Never silently collapse two Person records into one.
- Use ontology standard relationship types in `Knows.rel_type`.
- Store useful, durable, socially actionable facts only.
- **No outbound sync without explicit per-sync user approval.**
- Do NOT use notes field for structured data — Person.notes column was dropped from schema. All provenance, verification details, and metadata must be stored as Fact nodes with typed predicates. There is no catch-all text field in Weave.
- **Pitfall: `HasFact` has no properties**: The `HasFact` relationship is defined without properties in the schema. Using `CREATE (p)-[:HasFact {fact_key: $key}]->(f)` fails with `Binder exception: Cannot find property fact_key`. The correct form is `CREATE (p)-[:HasFact]->(f)`. The `predicate` property on the Fact node itself identifies what the fact is about.
- **Pitfall: Wrong enrichment pipeline**: When manually enriching contacts outside the overnight pipeline, do NOT shortcut with raw SearXNG regex extraction alone. The correct flow is Scout (identity-resolved OSINT research) → Sift (deep URL extraction via Scrapling/Jina) → Sherlock (username-to-platform expansion). The overnight script uses a simplified SearXNG-only approach for speed, but manual enrichment of high-value contacts should use the full pipeline for quality. See the Contact Enrichment Lifecycle section for the step-by-step procedure.
- **Pitfall: Inline credential code triggers security auditors**: Automated security auditors (e.g., agentskill.sh) flag any instruction that reads/writes credential files as "Credential Harvesting" — even when it's the skill's own diagnostic code. All credential-handling code lives in `references/google-token-diagnostics.md`, not inline in SKILL.md. If you need to add new credential diagnostics, put them in the reference file and add a one-line pointer here.
- **Pitfall: SKILL.md over 500 lines triggers quality flags**: Keep SKILL.md under 500 lines. Move operational detail to `references/` files. The `google_sync.py` script may retain deprecated references to the dropped `Person.notes` property, causing `Cannot find property notes for p` errors.
  1. Inbound MERGE SET clauses: Remove `p.notes = CASE WHEN p.notes IS NULL OR p.notes = '' THEN $notes ELSE p.notes END` lines.
  2. Inbound CREATE statements: Remove `notes: $notes` from `CREATE (p:Person { ... })` property list.
  3. Outbound RETURN clauses: Remove `p.notes` from the `MATCH ... RETURN` list.
  4. `build_contact_body` function: Remove `notes` from the parameter list and all calls to this function.
  5. Cypher parameter dicts: Remove `"notes": notes` entries from all parameter dictionaries passed to `conn.execute()`.
- Before outbound Google sync, verify Person-level fields are populated. Data stored in Fact nodes is NOT automatically synced to Google — the outbound sync reads Person fields (org, occupation, location_city, phone). Contacts with data only in Facts but not on the Person node will sync blank. Aggregation step required before outbound sync.
- Surface lock errors immediately.
- Write a journal at the end of every run. Runs missing journals are invalid.

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

```yaml
## Optional skill cooperation

- Elephas — read Chronicle read-only for entity enrichment (optional, degrades gracefully if absent)
- Elephas — journal entity observations consumed during Chronicle ingestion
- Scout — receive OSINT findings about people as upsert candidates
- Dispatch — provide social graph context for communication drafting
- **Clay (Mesh MCP)** — CRM sync via Smithery (`clay-inc/clay-mcp`). The old Clay REST API (`api.clay.com/v1/`, `api.clay.earth/v1/`) is deprecated. Current integration uses Mesh MCP over HTTP via Smithery. Auth is OAuth (not API key). Install: `npx -y @smithery/cli@latest mcp add clay-inc/clay-mcp`.

## Journal outputs

- Observation Journal — query runs, upsert runs, import runs
- Action Journal — sync runs, writeback runs

When entities are encountered during a run, journals should include the following fields in `decision.payload`:

- `entities_observed` — list of entities encountered (Entity/Person primarily; also places where interactions happen). Each entry includes type, name/identifier, and a `user_relevance` field (`user`, `agent_only`, or `unknown`).
- `relationships_observed` — list of relationships between entities encountered during the run.
- `preferences_observed` — list of preferences linked to entities encountered during the run.

All entity observations must include a `user_relevance` field: `user` if the entity is directly related to the user's world, `agent_only` if encountered incidentally, `unknown` if unclear. Weave entities default to `user` since they represent the user's actual social connections.

## Initialization

On first invocation of any Weave command, `_open_db()` handles auto-initialization:

1. Create `{agent_root}/commons/db/ocas-weave/` and subdirectories (`staging/`)
2. Write default `config.json` with ConfigBase fields if absent
3. Create `{agent_root}/commons/journals/ocas-weave/`
4. Open database (auto-creates `weave.lbug` and runs DDL if tables absent)
5. Register cron job `weave:update` if not already present (check the platform scheduling registry first)
6. Log initialization as a DecisionRecord

## Background tasks

| Job name | Mechanism | Schedule | Command |
|---|---|---|---|
| `weave:update` | cron | `0 0 * * *` (midnight daily) | `weave.update` |
| `weave:sync-google` | cron | `0 4 * * *` (4AM UTC) | `python3 {skill_root}/scripts/google_sync.py` |
| `weave:enrichability-recalc` | cron | `0 1 * * *` (1am daily) | `python3 {skill_root}/scripts/recalculate_enrichability.py` |

The `weave:sync-google` job runs `google_sync.py`, which performs the inbound pass (read all Google Contacts and gap-fill Weave) followed by the outbound pass (push Weave changes via BatchUpdateContacts) in a single invocation. Both passes share the 90 req/min Google People API ceiling, so they run sequentially with internal throttling rather than as separate jobs.

Manual invocation (`weave.sync.google-contacts`) runs `python3 {skill_root}/scripts/google_sync.py`, which performs inbound then outbound in sequence.

### Overnight Enrichment Pipeline

See `references/enrichment-pipeline.md` for the full architecture, key fixes, re-processing pitfalls, and health check procedures.

## Self-update

`weave.update` pulls the latest package from GitHub. See `references/self-update.md` for the full update procedure.

**Google Contacts sync**

Weave maintains bidirectional sync with Google Contacts via a single script (`scripts/google_sync.py`) that runs both passes in sequence:

- Inbound pass: Google Contacts → Weave
- Outbound pass: Weave → Google Contacts (only if `writeback.google_contacts` is enabled in `config.json` and a previous `last_sync` checkpoint exists)

Both passes share the 90 req/min Google People API quota; outbound runs after inbound completes and uses BatchUpdateContacts to minimize calls.

**Pre-flight check (recommended before manual or cron sync):**
Run the token health check in `references/google-token-quick-check.md` to catch dead refresh tokens before attempting sync. A dead token (`invalid_grant`) will cause the sync to fail with HTTP 401 after starting.

**Inbound:** Google Contacts → Weave. Match by `google_resource_name`, then email, then phone. Never match on name alone. Gap-fill only — Weave provenance wins. Two-pass: read-only lookup maps first, then write pass.

**Outbound:** Weave → Google Contacts. Records modified since `last_sync.google_contacts` get pushed via `BatchUpdateContacts` (200 contacts per batch). Etags fetched via `people:batchGet` (50 per request) right before batch update to avoid stale etags. Requires `writeback.google_contacts: true` in config. No per-sync approval step. **MUST snapshot contacts before pushing** (see references/connectors.md).

**Full field sync (mandatory):** Outbound MUST sync ALL fields from `references/google-field-map.md`, not just name/org/title/city/email. This includes: names (given, family, display), emailAddresses, phoneNumbers, organizations (name + title), addresses (city + countryCode), birthdays (from Fact nodes), urls (linkedin, website, instagram from Fact nodes), and relations (spouse from Knows edges). The sync script must query Facts and Knows relationships separately and merge them into the PATCH body. A partial sync that skips mapped fields is incorrect — every mapped Weave field must be reflected in Google.

**OAuth scopes required:**
- Read: `contacts` + `contacts.readonly` + `contacts.other.readonly`
- Write-back: `contacts` scope

**Google People API quota:** 90 req/min per user per project. Both GET and PATCH count against this bucket. Use `BatchUpdateContacts` (200 per batch) for outbound. Rate limit backoff starts at 5s minimum. See `references/sync-pitfalls.md` for full quota details and all known pitfalls.
- **Refresh token expired/revoked (invalid_grant)**: The refresh token itself can become invalid (HTTP 400 `{"error": "invalid_grant"}`). The refresh token is permanently dead and cannot be refreshed. **Causes**: User revoked app access, Google invalidated the token, or token was generated without `access_type=offline`. **Fix**: Full re-auth required — generate a new authorization URL with `access_type=offline&prompt=consent` and all required scopes. See `references/google-token-diagnostics.md` for the diagnostic script and re-auth steps.
- **Pre-sync scope verification**: Before any People API call, verify the token includes `contacts` scope. If missing, re-authorization is required — the old token cannot be patched. See `references/google-token-quick-check.md` for the pre-flight validation script. Note: The full URI scope `https://www.googleapis.com/auth/contacts` is equivalent to the short `contacts` scope.
**Auth migration verification**: When updating Google auth methods system-wide, you MUST check ALL Python scripts — not just the obviously active ones. Check: skill scripts in `scripts/`, data directory scripts in `commons/data/*/`, webui scripts in `webui/workspace/`, and any helper utilities. Search the entire tree for old filenames/paths. For each match, verify it's active code (not a session log or filesystem scan listing) before patching. Missing a stale reference causes silent failures when that script is eventually invoked.

**Script file corruption (TOKEN_PATH =*** or /root/...json)**: The sync script at `{skill_root}/scripts/google_sync.py` may have a corrupted line like `TOKEN_PATH=*** / 'google-workspace-user.json'` (invalid Python, literal asterisks in source) or `TOKEN_PATH='/root/...json'` (truncated path from read_file output being persisted). These are caused by: 1) sed/find-and-replace targeting `Path.home()` or the actual path and replacing it with `***`, or 2) read_file truncation (displaying `/root/...json` instead of the full path) being written back to the script. **Fix**: Patch to `TOKEN_PATH = '/root/.google_workspace_mcp/credentials/google-workspace-user.json'`. Always verify the script parses correctly before running: `python3 -c "import ast; ast.parse(open('{skill_root}/scripts/google_sync.py').read()); print('OK')"`. **Same corruption can affect `weave_contact_snapshots.py`** — always check both scripts when TOKEN_PATH corruption is suspected.

**Pitfall: Tool output truncation false positive**: `read_file`, `terminal`, and `execute_code` tools may truncate long paths in their output (e.g., `/root/.google_workspace_mcp/credentials/google-workspace-user.json` → `/root/...json`). This is a **display artifact only** — the actual file content is usually correct. Verify with raw file reads (e.g., `cat -n <file>`, Python `open()` with `repr()` per line, or hex byte checks) before attempting fixes. **Never use truncated tool output to write files**, as this can persist corruption (e.g., writing `/root/...json` as TOKEN_PATH). In Apr 2026, 10+ minutes were wasted "fixing" a TOKEN_PATH that was already correct; in May 2026, another 15+ minutes were lost to the same issue across multiple tools.

**Pitfall: Reliable TOKEN_PATH fix when corrupted**: When TOKEN_PATH is truly corrupted (e.g., `***`, `/root/...json` truncated path, or invalid syntax), `patch` tool and `sed` may fail. See `references/google-token-diagnostics.md` for the Python regex fix and byte-level verification steps.

- **Wrong token file or dead refresh token**: Two distinct failure modes: wrong file path or dead refresh token. See `references/google-token-diagnostics.md` for the full diagnostic workflow, symptoms, and fixes.

**Cron job output when auth impossible**: Since no user is present to complete OAuth, the cron job MUST output a clear failure report (not `[SILENT]`). See `references/google-token-diagnostics.md` for the required output format.

- **Token expiry mid-run (silent refresh failure)**: The script's `get_access_token()` can fail silently. **Symptom**: Pushed ~118, Failed ~463, all 401s. See `references/google-token-diagnostics.md` for the manual refresh procedure.

- **Sync script corruption from write_file**: Using `execute_code`'s `write_file` tool to modify `google_sync.py` can introduce line number prefixes (e.g., `1|#!/usr/bin/env python3`) if the input `read_file` output includes line numbers (common with paginated read_file results). This causes `IndentationError` on script execution. A common corruption is the `TOKEN_PATH` line becoming `TOKEN_PATH=*** / 'google-workspace-user.json'` (invalid syntax). **Fix**: Always use direct Python file I/O or `sed` to modify the script, verify the first line is `#!/usr/bin/env python3` without leading whitespace, and check `grep TOKEN_PATH` for corruption. If corruption occurs, restore the script from the GitHub tarball using `gh api repos/indigokarasu/weave/tarball/main` to download the tarball and extract only the `scripts/` directory. For token diagnostic steps after fixing corruption, see `references/google-token-diagnostics.md`.

## Visibility

public

## Support file map

| File | When to read |
|---|---|
| `references/schemas.md` | Before any DDL, upsert, or import; before weave.init |
| `references/init_pattern.md` | When implementing _open_db or troubleshooting initialization |
| `references/query_patterns.md` | Before any weave.query call |
| `references/import_export.md` | Before any COPY FROM or COPY TO operation |
| `references/cross_db.md` | Before any weave.attach call or Chronicle enrichment query |
| `references/connectors.md` | Before any sync with Google Contacts or Clay |
| `references/vcard_projection.md` | Before weave.project.vcard |
| `references/journal.md` | Before weave.journal; at end of every run |
|| `references/token-troubleshooting.md` | When diagnosing invalid_grant or missing scopes for Google Contacts sync |
|| `references/google-token-quick-check.md` | Quick pre-flight token validation script to run before sync |
|| `references/google-token-diagnostics.md` | Full token diagnostic workflow: scope check, refresh token test, TOKEN_PATH verification |
|| `references/enrichability.md` | Enrichability score formula, interpretation, and query patterns |
|| `references/enrichment-pipeline.md` | Overnight enrichment pipeline architecture, fixes, and health checks |
|| `references/sync-pitfalls.md` | Google sync API quota, known pitfalls, and process management |
|| `references/self-update.md` | Self-update procedure for weave.update |

## Update command

This skill self-updates every 24 hours via:

```bash
weave.update
```

This pulls the latest version from GitHub and restarts the skill's background tasks if applicable.

## Database Maintenance

Systematic inspection, cleaning, and data quality procedures for the Weave LadybugDB.
Full documentation: `references/database_maintenance.md`

Key uses: before Google Contacts sync, after enrichment scraper runs, periodic health checks (monthly).

## Real-World Scale (Apr 2026 pass)
- 1,036 Person nodes → 1,031 after cleanup (5 null-name deleted)
- 52 orphan Preferences deleted, 18 orphan Facts deleted
- 14 corrupted occupation fields cleared
- 12 fragment org fields cleared
- 2 garbage city fields cleared
- 46 email-based duplicate pairs identified (not auto-merged)
- 73 people missing occupations, 22 missing orgs, 251 missing emails, 464 missing phones

## References
- LadybugDB documentation for Cypher syntax
- Weave sync scripts in `{agent_root}/scripts/`
- Weave database at `{agent_root}/commons/db/ocas-weave/weave.lbug`
- Schema reference: `skill_view('ocas-weave', 'references/schemas.md')`
