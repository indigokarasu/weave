---
name: ocas-weave
description: >
  Weave: private provenance-backed social graph. Maintains queryable records
  of people, relationships, preferences, and shared experiences for recall,
  gifting, hosting, introductions, and serendipity. Trigger phrases: 'who do I
  know in', 'what does X like', 'add this person', 'relationship with', 'gift
  ideas for', 'sync contacts', 'prepare for meeting with', 'update weave'. Use
  when storing or retrieving facts about a person, recording a relationship,
  or discovering connections between people.
metadata:
  author: Indigo Karasu
  email: mx.indigo.karasu@gmail.com
  version: "3.5.0"
  hermes:
    tags: [social-graph, people, relationships]
    category: memory
    required_commands: ["sherlock"]
    cron:
      - name: "weave:update"
        schedule: "25 7 * * *"
        command: "weave.update"
      - name: "weave:sync-contacts"
        schedule: "0 8 * * 0"
        command: "python3 {agent_root}/skills/ocas-weave/scripts/weave_full_sync.py"
      - name: "weave:overnight-enrichment"
        schedule: "0 2 * * *"
        command: "python3 {agent_root}/skills/ocas-weave/scripts/overnight_enrichment.py"
  openclaw:
    skill_type: system
    visibility: public
    filesystem:
      read:
        - "{agent_root}/commons/data/ocas-weave/"
        - "{agent_root}/commons/journals/ocas-weave/"
        - "{agent_root}/commons/db/ocas-weave/"
        - "{agent_root}/commons/db/ocas-elephas/chronicle.lbug"
      write:
        - "{agent_root}/commons/data/ocas-weave/"
        - "{agent_root}/commons/journals/ocas-weave/"
        - "{agent_root}/commons/db/ocas-weave/"
    self_update:
      source: "https://github.com/indigokarasu/weave"
      mechanism: "version-checked tarball from GitHub via gh CLI"
      command: "weave.update"
      requires_binaries: [gh, tar, python3]
    requires:
      credentials:
        - name: "google_contacts_oauth"
          description: "Google People API v1 OAuth credentials for contact sync"
          required: false
        - name: "clay_api_key"
          description: "Clay REST API Bearer token for CRM sync"
          required: false
    cron:
      - name: "weave:update"
        schedule: "25 7 * * *"
        command: "weave.update"
      - name: "weave:sync-contacts"
        schedule: "0 8 * * 0"
        command: "python3 {agent_root}/skills/ocas-weave/scripts/weave_full_sync.py"
      - name: "weave:overnight-enrichment"
        schedule: "0 2 * * *"
        command: "python3 {agent_root}/skills/ocas-weave/scripts/overnight_enrichment.py"
---

# Weave

Weave maintains a private, provenance-backed social graph of people, relationships, preferences, and shared experiences — queryable for meeting prep, gift ideas, hosting, introductions, city connections, and serendipity discovery. Every stored fact carries source type, reference, timestamp, and confidence score; the graph never silently merges two person records and never writes back to external systems without explicit per-sync approval. All queries use Cypher — no SQL. The database initializes automatically on first use.


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


## Integrated: google-contacts-weave-sync

Bidirectional sync between Google Contacts and Weave's LadybugDB social graph. Imports contacts as Person nodes, enriches existing records with missing fields, and tracks all changes for undo.

### When to use
- Initial Weave population from Google Contacts
- Periodic refresh to catch new/changed contacts
- Enriching existing Weave records with email, phone, org data
- Auditing contacts overlap between Google and Weave

### When not to use
- OSINT on people (use Scout)
- General contact research (use Sift)
- Writing back to Google without explicit approval

### Scope Requirements
| Direction | Required Scope | Notes |
|-----------|---------------|-------|
| Inbound (read) | `contacts.readonly` | My Contacts only |
| Inbound (full) | `contacts` + `contacts.readonly` + `contacts.other.readonly` | My Contacts + Other Contacts |
| Write-back (outbound) | `contacts` | Requires full scope |

### Token and OAuth
Google OAuth credentials at `<hermes-root>/owner_google_credentials.json`. Client ID is `<GOOGLE_OAUTH_CLIENT_ID>.apps.googleusercontent.com`.

### Inbound Sync Procedure
1. **Check/Initialize Weave Schema**: Verify database has tables. Refer to `references/schemas.md` for full DDL.
2. **Fetch Google Contacts**: Use REST API via `urllib.request` (preferred) or `googleapiclient` SDK.
3. **Load Existing Weave State**: Load Person nodes by email and phone for cross-referencing.
4. **Cross-Reference and Sync**: Match by `google_resource_name`, then email, then phone. Apply enrichment logic (only fill NULL/empty fields).
5. **Track Changes**: Write to `<hermes-root>/data/hermes-weave/sync_history.jsonl`.
6. **Update Config**: Update `last_sync` in `config.json`.

[{'resourceName}': 'updateContact` (NOT `{resourceName'}, [0]]

### Undo
Use `sync_id` from `sync_history.jsonl` to delete new records or revert enriched fields.

### Pitfalls
- **Other Contacts API**: `people_api.otherContacts()` is unreliable; use REST with `contacts.other.readonly`.
- **REST API Preference**: Use `urllib.request` as `googleapiclient` is missing in `execute_code` sandbox. Additionally, `googleapiclient.discovery.build` causes silent hangs (no output, no error) when run via `execute_code` or `terminal` background processes — always use `urllib.request` REST calls for Google People API.
- **sources enum**: The `sources` query parameter for People API connections must be `READ_SOURCE_TYPE_CONTACT` (not `READ_SOURCE_CONTACT`). This matters when calling the REST API directly.
- **Name Enrichment**: Incremental syncs typically focus on filling `name_given` and `name_family`.
- **Stale resource names**: If a GET on `people/{resourceName}` returns 404, the resource name in Weave is stale. Re-match via `people:searchContacts` or refresh from inbound sync before pushing.
- **Correct update endpoint**: Use `{resourceName}:updateContact` (not `{resourceName}`) for PATCH updates.
- **Top-level etag**: Use the top-level `etag` field from the GET response, NOT `metadata.sources[0].etag`. The source etag causes `FAILED_PRECONDITION`.
- **Social profiles from notes**: Extract `notes.social_profiles` JSON and push each `{platform, url}` as `urls` entries with `type` set to the platform name.
- **Scope Expansion**: Requires re-auth with `prompt=consent&access_type=offline`.
- **False Duplicates**: Never match on name alone.
- **Provenance**: Use `source_type='imported'`, `source_ref=<resourceName>`, `confidence=0.8`.
- **Performance**: Use `COPY FROM` for bulk imports (>100 rows).
- **Malformed US Phones**: Avoid numbers with extra leading `1` after country code (e.g. `+1 (141)...`).
- **Privacy Masking**: LadybugDB driver masks phone display but stores full digits.
- **Phone Hygiene**: Run cleanup pass to remove `.0` suffixes and invalid patterns.

## Integrated: mesh-mcp-clay-connectors

Connect to the Mesh MCP (Clay/Me.sh successor) via Smithery for CRM sync with Weave.

### Current Status
- **Old Clay API: DEPRECATED** (`api.clay.com/v1/`, `api.clay.earth/v1/`).
- **Mesh MCP: Working via Smithery** (`https://server.smithery.ai/clay-inc/clay-mcp`). Transport: MCP over HTTP. Auth: Bearer token.

### Connecting
- **Smithery CLI**: `npx -y @smithery/cli@latest mcp add clay-inc/clay-mcp` (Requires OAuth flow).
- **mcporter**: Config via `mcpServers` in JSON.

### Available Tools
- `searchContacts`, `getContact`, `createContact`, `updateContact`, `addContactToGroup`, `get_user_information`.

### Auth Troubleshooting
- Clay API key does NOT work as Bearer token for Mesh MCP. Must complete OAuth flow at Smithery.ai.

### Pitfalls
- **API Key vs OAuth**: Migration from `clay.earth` to `me.sh` requires moving to Mesh MCP on Smithery.
- **Terminal Hangs**: Use `pty=true` for interactive `npx` or `mcporter` commands.

## Integrated: ocas-expansion

Orchestrates a multi-phase pipeline to enrich the personal social graph (Weave) combining interior knowledge with external OSINT and professional research.

### Execution Phases
1. **Structural Baseline (Scout)**: Establish "Current State" (`job_title`, `organization`, `location`) via OSINT.
2. **Intellectual Depth (Sift)**: Discover "Digital Footprints" (portfolios, blogs, press) and extract projects/philosophies.
3. **Synthesis & Permanence (Weave)**: Convert raw data into `Preference` or `Experience` nodes with provenance.

### Pitfalls & Workarounds
- **Search Failures**: When `web_search` (Firecrawl) fails with "Payment Required", use:
  - **Email Domain Inference**: Infer company from email domain (confidence 0.7).
  - **Semantic Scholar API**: Free academic profile lookup (unauthenticated).
  - **GitHub Public API**: Search commits by email to find activity/profiles.
  - **Direct Staff Directories**: Navigate to `{org_website}/about/staff-directory` (confidence 0.95).
  - **Brave Search/SearchX**: Use as alternatives to Firecrawl.
- **Disambiguation**: Cross-check academic publications against expected professional domain.
- **LinkedIn Authwall**: Browser scraping returns login page; use search snippets.
- **LadybugDB Constraints**: 
  - Use `CREATE` for relationship properties (not `MERGE` with inline props).
  - Create two directed edges for bidirectional relations.
  - Use `org` and `occupation` fields (not `company` or `job_title`).
- **Google Drive/Docs**: Use OAuth tokens (`~/.hermes/indigo_google_credentials.json`) instead of service accounts to avoid 403 quota/permission errors.

### Report Output
Final report link saved to `<hermes-root>/commons/data/ocas-expansion/last_run_report.txt`.

Weave owns the private social graph: people, relationships, preferences, and shared experiences.

Weave does not own: general world knowledge (Elephas/Chronicle), OSINT research (Scout), web research (Sift), task management (Triage).

Weave is a standalone database. It does not write to Chronicle and has no runtime dependency on Chronicle. If a person in Weave also exists in Chronicle, Chronicle may store a `weave:person_id` reference on its Entity node. That is Chronicle's concern, not Weave's.

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

## Storage layout

```
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
- **Pitfall: Sync script `notes` property references**: The `google_sync.py` script may retain deprecated references to the dropped `Person.notes` property, causing `Cannot find property notes for p` errors.
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
```


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

Script: `<hermes-root>/skills/ocas-weave/scripts/overnight_enrichment.py`
Logs: `<hermes-root>/data/weave-enrichment/run.log`
Progress: `<hermes-root>/data/weave-enrichment/progress.jsonl`
Recalculation: `<hermes-root>/skills/ocas-weave/scripts/recalculate_enrichability.py` (run nightly at 1am ET via cron)

**Architecture — 3-phase Scout → Sift → Write pipeline:**

1. **Scout Phase** (`searxng_search` + `build_scout_queries`): Identity-resolved SearXNG search using name + org. Builds targeted queries like `"First Last" LinkedIn` and `"Name" Company`. Returns URLs and snippets.

2. **Sift Phase** (`sift_extract_from_pages`): Fetches full page content from the top 3 non-auth-walled URLs using direct HTTP fetch (fast path) with Jina Reader fallback for JS-heavy sites. Extracts structured data (occupation, org, location, email) from the **full page content** — NOT from search snippets. This is the critical fix: the old code used regex on 160-character search snippets, which produced truncated/garbage data.

3. **Write Phase** (`enrich_weave_contact`): Validates extracted fields, writes as Fact nodes with full provenance (source_url, source_type, confidence, record_time), recalculates enrichability_score.

**Key fixes from the old pipeline:**
- **No more snippet regex**: The old `extract_info_from_search()` applied regex to search result snippets (title + ~160 char content), producing truncated fields like `"r Vice President"` and `"St"` instead of `"Stanford"`. The new `sift_extract_from_pages()` fetches full pages.
- **No `fact_key` on HasFact**: The old code used `CREATE (p)-[:HasFact {fact_key: $key}]->(f)` but `HasFact` has no properties in the schema. Fixed to `CREATE (p)-[:HasFact]->(f)`.
- **Source URL tracking**: Each extracted field now stores its source URL in `source_ref` for provenance.
- **Auth-walled domain skipping**: LinkedIn, Twitter/X, Facebook, Instagram are skipped during page fetch (they return login walls).

**Re-processing pitfall**: The progress file tracks all contacted person IDs, but ~65% of searches return "no extractable data." If the filter excludes ALL progress-file IDs permanently, contacts that failed enrichment are never retried.

**Do NOT filter by progress file at all.** The enrichment logic (`enrich_weave_contact`) only fills fields that are currently NULL/empty in the database — writing the same value twice is harmless. Filtering by progress entries caused a bug (Apr 2026): contacts with partial enrichment (e.g., `location_city` found, but `org` and `occupation` still missing) were permanently excluded because they had a non-empty `fields` entry in progress.jsonl. The simplest correct approach: query contacts with gaps directly from the database, skip no one, and let the SET clause only fill what's missing. The progress file should be used for logging/monitoring only, not for filtering candidates.

**Progress file duplicate monitoring**: Health checks must verify progress.jsonl duplicate rate (unique contact IDs / total entries) stays below 10%. Higher rates indicate the script is incorrectly filtering by progress file. If duplicates exceed 10%, truncate progress.jsonl and patch the script to remove any progress-file-based filtering.
  - Note: progress.jsonl uses the `id` field (not `contact_id`) for contact identifiers. When counting unique contact IDs, parse the `id` key from each JSON line in the file.
  - Recurring errors in progress.jsonl (e.g., `Connection.execute() got unexpected keyword argument 'occupation'`) indicate script bugs; truncate the file to clear stale entries and patch the script.

**Enrichment Pipeline Health Check**: Run these checks periodically (e.g., via cron) to verify pipeline health:
1. Check if enrichment process is running: `ps aux | grep overnight_weave_enrichment | grep -v grep`
2. If not running and before 6am PDT, restart: `python3 <hermes-root>/scripts/overnight_weave_enrichment.py`
3. Check progress.jsonl duplicates: Count unique `id` values vs total entries; truncate if duplicate rate >10%
4. Check enrichment stats: `cat <hermes-root>/data/weave-enrichment/stats.json`
5. Check last sync time: `cat <hermes-root>/commons/db/ocas-weave/config.json | grep last_sync`
6. Check recent sync activity: `tail -5 <hermes-root>/commons/data/ocas-weave/sync_log.jsonl`
7. Verify Google token scopes: Ensure `contacts` (or full URI `https://www.googleapis.com/auth/contacts`) is present in token scopes.


## Self-update

`weave.update` pulls the latest package from the `source:` URL in this file's frontmatter. Runs silently — no output unless the version changed or an error occurred.

1. Read `source:` from frontmatter → extract `{owner}/{repo}` from URL
2. Read local version from SKILL.md frontmatter `metadata.version`
3. Fetch remote version from SKILL.md frontmatter: `gh api "repos/{owner}/{repo}/contents/SKILL.md" --jq '.content' | base64 -d | grep 'version:' | head -1 | sed 's/.*"\(.*\)".*/\1/'`
4. If remote version equals local version → stop silently
5. Download and install:
   ```bash
   TMPDIR=$(mktemp -d)
   gh api "repos/{owner}/{repo}/tarball/main" > "$TMPDIR/archive.tar.gz"
   mkdir "$TMPDIR/extracted"
   tar xzf "$TMPDIR/archive.tar.gz" -C "$TMPDIR/extracted" --strip-components=1
   cp -R "$TMPDIR/extracted/"* ./
   rm -rf "$TMPDIR"
   ```
6. On failure → retry once. If second attempt fails, report the error and stop.
7. Output exactly: `I updated Weave from version {old} to {new}`


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

**Google People API quota (hard-won, Apr 2026):**
- `Critical read requests`: 90 req/min per user per project
- **Both GET (etag fetch) and PATCH (update) count against this same 90/min bucket**
- Outbound uses 2 API calls per contact (GET etag + PATCH) — at 90/min ceiling, that allows ~45 contacts/min
- **Use `BatchUpdateContacts` for outbound** — up to 200 contacts per batch request, reducing 2N calls to N/200 calls
- Recommended sleep between batches: 1.5s. Between individual PATCHes (if not batching): 1.3s minimum
- **Rate limit backoff must start at 5s minimum**, not 0.5s — starting too aggressive causes cascading 429s without giving the quota window time to clear
- On 429: exponential backoff starting at 5s, doubling per retry up to 4 attempts
- On 502 (Google server error): retry once after 5s, then mark failed
- On 404: contact was deleted from Google — clear `google_resource_name` in Weave so future syncs don't retry

**Known pitfalls:**
- `otherContacts()` API is unreliable — use REST with `contacts.other.readonly` scope instead
- `expiry` field in token may be ISO string or integer; handle both
- Scope expansion always requires re-auth with `prompt=consent&access_type=offline`
- Bulk imports (>100 rows) should use `COPY FROM` not individual inserts
- Provenance for imported contacts: `source_type='imported'`, `confidence=0.8`
- Outbound PATCH requires current etag from Google — fetch etag before update
- **Top-level etag**: Use the top-level `etag` field from the GET response, NOT `metadata.sources[0].etag`. The source etag causes `FAILED_PRECONDITION`.
- **Stale resource names**: If a GET on `people/{resourceName}` returns 404, the resource name in Weave is stale. Re-match via `people:searchContacts` or refresh from inbound sync before pushing.
- **Correct update endpoint**: Use `{resourceName}:updateContact` (not `{resourceName}`) for PATCH updates.
- **Social profiles from notes**: Extract `notes.social_profiles` JSON and push each `{platform, url}` as `urls` entries with `type` set to the platform name.
- **Phone numbers may arrive with malformed leading `1`** (e.g. `+1 (141)...`) — validate before storing
- **Token path**: use `<hermes-root>/owner_google_credentials.json` for owner's contacts (owner is the Google Contacts account owner, not Indigo)
- **execute_code timeout**: The full `weave_google_bidirectional_sync.py` script times out in `execute_code` (300s limit) when outbound has 200+ contacts (2 API calls × 1.3s sleep each). Manual sync workaround: run as background process via `terminal(background=true)` with `notify_on_complete=true`. The script handles its own checkpointing. Cron jobs don't have this issue since they run outside `execute_code`.
- **Manual sync via background process**: When invoking `weave_google_bidirectional_sync.py` manually, always use `terminal(background=true, notify_on_complete=true, timeout=600)` — do NOT use `execute_code` (300s cap) or foreground `terminal` (blocks agent). The script takes ~280s for ~900 contacts (inbound ~90s, outbound ~190s). If the process needs to be monitored, check the checkpoint file size: `wc -l staging/outbound_ckpt.txt` — each line is a pushed `google_resource_name`. **If the process is killed (exit 143 SIGTERM or 137 SIGKILL)**, this is usually memory pressure from the real_ladybug C extension (~500-800MB RSS with 900+ contacts). The checkpoint system survives kills — just clear the LadybugDB lock (see "LadybugDB lock not released after process kill" below), re-run, and it resumes automatically.
- **Multi-run resilience**: The script's checkpoint system (`staging/outbound_ckpt.txt`) survives process kills and restarts. If interrupted mid-outbound, re-running the script resumes from the last pushed contact. Example: 571 contacts pushed across 3 runs (150 + 50 + 471) without data loss or duplication. On successful completion, the checkpoint file is deleted automatically.
- **Process spawning**: The Python script spawns a child process (real_ladybug C extension). You'll see two PIDs: parent (bash shell, `do_wait`) and child (python3, doing actual work). This is normal — do not kill the child thinking it's a duplicate.
- **Output buffering**: When run as background process, stdout appears empty for 90-120+ seconds despite the script actively working. The `import real_ladybug` (21MB C extension) and initial database query take significant time before the first `_log()` output appears. Monitor progress via `ps aux | grep weave_google`, `/proc/<pid>/wchan`, or `ss -tnp | grep <pid>` (for active API calls). Do NOT kill the process thinking it's hung — check checkpoint file or process CPU usage first.
- **Do NOT use SIGUSR1**: Sending `kill -USR1` to the Python process causes it to crash with `RuntimeError` (no traceback handler installed). Use `/proc/<pid>/wchan`, `ss -tnp`, and checkpoint file inspection for diagnostics instead.
- **LadybugDB lock not released after process kill**: When the sync process is killed (SIGTERM/SIGKILL, exit codes 143/137), the LadybugDB file lock on `weave.lbug` can remain held by orphaned child processes. The next run fails with `RuntimeError: IO exception: Could not set lock on file`. **Diagnosis**: `fuser -v <hermes-root>/commons/db/ocas-weave/weave.lbug` shows which PID holds the lock. **Fix**:
  ```bash
  fuser -v <hermes-root>/commons/db/ocas-weave/weave.lbug 2>&1
  # Kill the orphaned process
  kill -9 <PID> 2>/dev/null
  # Clean up stale .wal file
  rm -f <hermes-root>/commons/db/ocas-weave/weave.lbug.wal
  # Verify lock is released
  fuser <hermes-root>/commons/db/ocas-weave/weave.lbug
  # Then retry the sync
  ```
  **Warning**: Multiple processes may need killing — `fuser` only shows one at a time. Kill, re-check, repeat until `fuser` returns empty. The `.wal` file from a killed process is always stale; removing it is safe. The script will re-create it on next run.
- **Stale etags on BatchUpdateContacts after multi-run resume**: When the sync process is killed and restarted multiple times, the script loads all modified contacts at startup (including their current Google etags). But on resume, contacts already in the checkpoint are filtered out. For contacts NOT yet pushed, the etags loaded at script start may become stale if those contacts were modified on Google between runs. **Symptom**: HTTP 400 with `FAILED_PRECONDITION: Request must set person.etag or person.metadata.sources.etag`. **Fix**: Re-run the sync again — the next invocation re-fetches fresh etags for all remaining contacts. The checkpoint system skips already-pushed contacts, so only the stale-etag contacts are retried with fresh etags. **Better fix**: Always use `people:batchGet` to fetch fresh etags right before batch update, not at script startup.
- **BatchUpdateContacts empty response**: The API may return HTTP 200 with empty body `{}` instead of `updateResult`. Empty response = all contacts updated successfully. Must handle both cases in code.
- **Batch etag fetching with people:batchGet**: Fetch 50 etags per request vs individual GETs. For 580 contacts: 12 batch GETs (50 each) + 3 batch updates (200 each) = 15 API calls vs 1162 individual calls.
- **Refresh token expired/revoked (invalid_grant)**: The refresh token itself can become invalid (HTTP 400 `{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}`). This is **different** from the silent refresh failure below — the refresh token is permanently dead and cannot be refreshed. **Causes**: User revoked app access, Google invalidated the token, or token was generated without `access_type=offline`. **Fix**: Full re-auth required — generate a new authorization URL with `access_type=offline&prompt=consent` and all required scopes, exchange the auth code for a new token with a fresh refresh_token. Use this diagnostic:
  ```python
  import json, urllib.request, urllib.parse
  with open('<hermes-root>/google_token.json') as f:
      td = json.load(f)
  req = urllib.request.Request(
      'https://oauth2.googleapis.com/token',
      data=urllib.parse.urlencode({
          'client_id': td['client_id'],
          'client_secret': td['client_secret'],
          'refresh_token': td['refresh_token'],
          'grant_type': 'refresh_token'
      }).encode(),
      headers={'Content-Type': 'application/x-www-form-urlencoded'}
  )
  try:
      resp = urllib.request.urlopen(req, timeout=30)
      print('Refresh OK')
  except urllib.error.HTTPError as e:
      body = e.read().decode()
      print(f'HTTP {e.code}: {body}')  # Look for invalid_grant
  ```
- **Pre-sync scope verification**: Before attempting any People API call, verify the token's scopes include contacts. The credentials at `<hermes-root>/owner_google_credentials.json` may have been generated for Gmail/Calendar/Drive only (missing `contacts`, `contacts.readonly`, `contacts.other.readonly`). Check with: `python3 -c "import json; td=json.load(open('<hermes-root>/google_token.json')); print(td.get('scopes', []))"`. If contacts scopes are missing, the token must be re-authorized with the correct scopes — the old token cannot be patched. For a complete diagnostic workflow including refresh token validity testing and re-auth steps, see `references/google-token-diagnostics.md`.
  - Note: The full URI scope `https://www.googleapis.com/auth/contacts` is equivalent to the short `contacts` scope. When checking scopes, accept either form.
**Script file corruption (TOKEN_PATH =*** or /root/...json)**: The sync script at `<hermes-root>/skills/ocas-weave/scripts/google_sync.py` may have a corrupted line like `TOKEN_PATH=*** / 'google_token.json'` (invalid Python, literal asterisks in source) or `TOKEN_PATH='/root/...json'` (truncated path from read_file output being persisted). These are caused by: 1) sed/find-and-replace targeting `Path.home()` or the actual path and replacing it with `***`, or 2) read_file truncation (displaying `/root/...json` instead of the full path) being written back to the script. **Fix**: Patch to `TOKEN_PATH = '<hermes-root>/google_token.json'`. Always verify the script parses correctly before running: `python3 -c "import ast; ast.parse(open('<hermes-root>/skills/ocas-weave/scripts/google_sync.py').read()); print('OK')"`. **Same corruption can affect `weave_contact_snapshots.py`** — always check both scripts when TOKEN_PATH corruption is suspected.

**Pitfall: Tool output truncation false positive**: `read_file`, `terminal`, and `execute_code` tools may truncate long paths in their output (e.g., `<hermes-root>/google_token.json` → `/root/...json`). This is a **display artifact only** — the actual file content is usually correct. Verify with raw file reads (e.g., `cat -n <file>`, Python `open()` with `repr()` per line, or hex byte checks) before attempting fixes. **Never use truncated tool output to write files**, as this can persist corruption (e.g., writing `/root/...json` as TOKEN_PATH). In Apr 2026, 10+ minutes were wasted "fixing" a TOKEN_PATH that was already correct; in May 2026, another 15+ minutes were lost to the same issue across multiple tools.

**Pitfall: Reliable TOKEN_PATH fix when corrupted**: When TOKEN_PATH is truly corrupted (e.g., `***`, `/root/...json` truncated path, or invalid syntax), `patch` tool and `sed` may fail due to special characters or escaping issues. Use Python with regex to replace any TOKEN_PATH assignment regardless of current corruption pattern:
```python
import re
with open('<hermes-root>/skills/ocas-weave/scripts/google_sync.py', 'rb') as f:
    content = f.read()
# Replace any TOKEN_PATH="<any value>" with the correct full path
new_content = re.sub(
    rb'TOKEN_PATH\s*=\s*"[^"]*"',
    rb'TOKEN_PATH="<hermes-root>/google_token.json"',
    content
)
with open('<hermes-root>/skills/ocas-weave/scripts/google_sync.py', 'wb') as f:
    f.write(new_content)
```
Verify fix using byte-level checks (tool output like `grep`/`terminal` may truncate long paths):
- Hexdump check: `hexdump -C <hermes-root>/skills/ocas-weave/scripts/google_sync.py | grep -A1 TOKEN_PATH`
- Python byte check: `python3 -c "with open('script.py', 'rb') as f: c=f.read(); idx=c.find(b'TOKEN_PATH'); print(c[idx:idx+60])"`
- **Wrong token file or dead refresh token (Apr 2026)**: The script at `<hermes-root>/skills/ocas-weave/scripts/google_sync.py` may point to the wrong token file, OR the token file may have a dead refresh token. Two distinct failure modes:
1. **Wrong file path**: Script points to `owner_google_credentials.json` (lacks `contacts` scope) instead of `google_token.json` (has correct scopes).
2. **Dead refresh token**: `google_token.json` has correct scopes including `contacts`, but the refresh token itself is expired/revoked (HTTP 400 `invalid_grant` — permanently dead, requires full re-auth).

**Symptom**: Script fails with "Token refresh failed: HTTP Error 400: Bad Request" then 401 Unauthorized on the People API call.

**Diagnosis**:
1. Check which file the script reads: `grep TOKEN_PATH <hermes-root>/skills/ocas-weave/scripts/google_sync.py`
2. Verify the token file's scopes: `python3 -c "import json; td=json.load(open('<hermes-root>/google_token.json')); print(td.get('scopes', []))"`
3. Test refresh token validity (see `references/google-token-diagnostics.md`)
4. Check alternate token file: `python3 -c "import json; td=json.load(open('<hermes-root>/owner_google_credentials.json')); print(td.get('scopes', []), 'has_refresh:', 'refresh_token' in td)"`

**Fix**:
- If wrong file: Patch `TOKEN_PATH` to `<hermes-root>/google_token.json`
- If dead refresh token: Full re-auth required with `access_type=offline&prompt=consent`
- If both files are problematic (e.g., `google_token.json` has scope but dead token, `owner_google_credentials.json` has alive token but no `contacts` scope): Full re-auth is required regardless. 

**Cron job output when auth impossible**: Since no user is present to complete OAuth, the cron job MUST output a clear failure report (not `[SILENT]`). Format:
```
## Weave Google Contacts Sync Failed — Final Report

**Status**: Failed — No valid OAuth token available
**Root Cause**: Both token files have dead refresh tokens (invalid_grant)
**Required Action (User Side)**: Run re-auth with access_type=offline&prompt=consent
**Cron Job Note**: This sync will continue to fail until valid tokens are in place.
```

**Note**: `owner_google_credentials.json` may have a valid refresh token but lacks `contacts` scopes. Always verify scopes AND test refresh token before assuming the token is usable.
- **Token expiry mid-run (silent refresh failure)**: The script's `get_access_token()` function has internal refresh logic, but it can fail silently — the refresh call may throw an exception that gets caught and logged to stdout (which is buffered for 90-120s), causing the script to fall through and return the expired token. When this happens, inbound succeeds (token was still valid) but outbound fails with HTTP 401 on most contacts. **Symptom**: Pushed ~118, Failed ~463, all 401s. **Fix**: Manually refresh the token before retrying:
  ```python
  python3 -c "
  import json, urllib.request, urllib.parse
  from datetime import datetime, timezone, timedelta
  with open('<hermes-root>/google_token.json') as f:
      td = json.load(f)
  resp = urllib.request.urlopen(urllib.request.Request(
      'https://oauth2.googleapis.com/token',
      data=urllib.parse.urlencode({
          'client_id': td['client_id'],
          'client_secret': td['client_secret'],
          'refresh_token': td['refresh_token'],
          'grant_type': 'refresh_token'
      }).encode()))
  new = json.loads(resp.read())
  td['token'] = new['access_token']
  td['expiry'] = (datetime.now(timezone.utc) + timedelta(seconds=new['expires_in'])).isoformat()
  with open('<hermes-root>/owner_google_credentials.json', 'w') as f:
      json.dump(td, f, indent=2)
  print('Token refreshed, expires:', td['expiry'])
  "
  ```
  Then re-run the sync script. The checkpoint system (`staging/outbound_ckpt.txt`) ensures the retry picks up where it left off — no duplicate pushes. **Why this works**: The refresh_token itself is valid; the issue is the script's internal refresh logic failing, not the credentials being revoked.
- **Sync script corruption from write_file**: Using `execute_code`'s `write_file` tool to modify `google_sync.py` can introduce line number prefixes (e.g., `1|#!/usr/bin/env python3`) if the input `read_file` output includes line numbers (common with paginated read_file results). This causes `IndentationError` on script execution. A common corruption is the `TOKEN_PATH` line becoming `TOKEN_PATH=*** / 'google_token.json'` (invalid syntax). **Fix**: Always use direct Python file I/O or `sed` to modify the script, verify the first line is `#!/usr/bin/env python3` without leading whitespace, and check `grep TOKEN_PATH` for corruption. If corruption occurs, restore the script from the GitHub tarball using `gh api repos/indigokarasu/weave/tarball/main` to download the tarball and extract only the `scripts/` directory. For token diagnostic steps after fixing corruption, see `references/google-token-diagnostics.md`.


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
| `references/token-troubleshooting.md` | When diagnosing invalid_grant or missing scopes for Google Contacts sync |
| `references/google-token-quick-check.md` | Quick pre-flight token validation script to run before sync |
| `references/google-token-diagnostics.md` | Full token diagnostic workflow: scope check, refresh token test, TOKEN_PATH verification |
| `references/enrichability.md` | Enrichability score formula, interpretation, and query patterns |

## Update command

This skill self-updates every 24 hours via:

```bash
weave.update
```

This pulls the latest version from GitHub and restarts the skill's background tasks if applicable.


## Integrated: weave-db-maintenance


# Weave Database Maintenance Skill

## Purpose
Perform systematic inspection, cleaning, and preparation of the Weave (LadybugDB) database to ensure data integrity before synchronization with Google Contacts or other systems.

## When to Use
- Before running Weave ↔ Google Contacts synchronization
- When experiencing sync failures due to data quality issues
- Periodic database health maintenance (monthly)
- After importing large amounts of data containing inconsistencies
- After the overnight enrichment scraper has run (known corruption bug)

## LadybugDB Query Quirks (Critical)

LadybugDB (embedded Cypher) has important differences from Neo4j Cypher:

### Return Format
- **`RETURN p`** (whole node): each row is a **dict** with properties + `_ID` and `_LABEL`
- **`RETURN p.id, p.name, count(p)`** (column selectors): each row is a **list** (NOT dict)
- Always check with `r.get_column_names()` before accessing columns
- Access list rows by index: `row[cols.index('p.id')]` or hardcode `row[0]`

### Unsupported Features
| Feature | Workaround |
|---------|------------|
| `NOT EXISTS(...)` subquery | Use `OPTIONAL MATCH ... WHERE ... IS NULL` |
| `type()` function | Not available — skip `type(r)` queries |
| `randomUUID()` | Generate UUIDs in Python via `uuid.uuid4()` |
| `CREATE INDEX IF NOT EXISTS` | Not supported — PKs are auto-indexed |
| `EXISTS()` in WHERE | Not supported for relationship checks |

### Row Iteration Pattern (large tables)
```python
def safe_get_all(conn, query):
    r = conn.execute(query)
    cols = r.get_column_names()
    rows = []
    while True:
        try:
            row = r.get_next()
            rows.append(row)
        except Exception as e:
            if "No more tuples" in str(e):
                break
            if "utf-8" in str(e).lower():
                continue  # skip corrupt row
            raise
    r.close()
    return cols, rows
```

`r.get_next()` raises `Runtime exception: No more tuples in QueryResult` when exhausted — NOT `StopIteration`.

### Database Locking Issues
- `fuser -v /path/to/weave.lbug` shows which PID holds the lock
- After a killed process (SIGTERM/SIGKILL), orphan processes may hold the lock
- **Kill orphan**: `kill -9 <PID>` — repeat until `fuser` returns empty
- **Stale WAL**: `rm -f <hermes-root>/commons/db/ocas-weave/weave.lbug.wal` after killed processes
- The `.wal` file from a killed process is always stale; removing it is safe

## Person Properties (for reference in queries)
```
id, name, name_given, name_family, email, phone,
location_city, location_country, occupation, org, notes,
google_resource_name, clay_id,
source_type, source_ref, confidence,
event_time, record_time, valid_from, valid_until
```

No `city`, `location_region`, or `company` properties — use exact names above.

## Steps

### 1. Database Inspection (via execute_code Python)

```python
from real_ladybug import Database, Connection
import json

db = Database("<hermes-root>/commons/db/ocas-weave/weave.lbug", read_only=True)
conn = Connection(db)

def safe_get_all(conn, query):
    r = conn.execute(query)
    cols = r.get_column_names()
    rows = []
    while True:
        try:
            row = r.get_next()
            rows.append(row)
        except Exception as e:
            if "No more tuples" in str(e):
                break
            if "utf-8" in str(e).lower():
                continue
            raise
    r.close()
    return cols, rows
```

#### Basic Counts
```python
# Use column-selector pattern: count() returns list
for label, query in [
    ("person_count", "MATCH (p:Person) RETURN count(p)"),
    ("preference_count", "MATCH (p:Preference) RETURN count(p)"),
    ("fact_count", "MATCH (f:Fact) RETURN count(f)"),
    ("knows_count", "MATCH ()-[r:Knows]->() RETURN count(r)"),
    ("haspref_count", "MATCH ()-[r:HasPreference]->() RETURN count(r)"),
    ("hasfact_count", "MATCH ()-[r:HasFact]->() RETURN count(r)"),
]:
    cols, rows = safe_get_all(conn, query)
    print(f"{label}: {rows[0][0] if rows else 0}")
```

#### Null/Empty Field Detection
```python
# Null names
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.name IS NULL RETURN p.id, p.occupation, p.org, p.email")

# Null occupations
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NULL OR p.occupation = '' RETURN p.id, p.name, p.org")

# Null orgs
r = conn.execute("MATCH (p:Person) WHERE p.org IS NULL OR p.org = '' RETURN count(p)")
print(f"People missing org: {r.get_next()[0]}")  # list row!

# Missing field summary
for field in ["email", "phone", "org", "location_city"]:
    cols, rows = safe_get_all(conn, f"MATCH (p:Person) WHERE p.{field} IS NULL OR p.{field} = '' RETURN count(p)")
    print(f"Missing {field}: {rows[0][0] if rows else '?'}")
```

### 2. Detect Enrichment Scraper Corruption

The overnight enrichment scraper has a known bug: it extracts substrings incorrectly, storing truncated/garbled text in occupation, org, and location_city. Run these checks after any enrichment cycle.

#### Checks for Truncated Occupations (missing first chars)
```python
# Occupations that start with lowercase or mid-word — check in Python
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NOT NULL AND p.occupation <> '' RETURN p.id, p.name, p.occupation, p.org")
import re
for r in rows:
    occ = r[2]
    if occ and (re.match(r'^[a-z]', occ) or len(occ) < 5 or len(occ) > 200):
        print(f"  SUSPICIOUS: {r[1]} occ='{occ[:80]}...'")
```

#### Checks for Fragment Orgs (enrichment got only first word)
```python
# Suspicious single-word orgs that aren't real company names
suspicious_orgs = {
    "Senior", "North", "Spring", "Work", "Product", "Finance",
    "Serial", "Serving", "Greater", "Atlantic", "Alameda", "Dedham",
    "Colorado", "Keystone", "Laurentian", "General", "Director",
    "Lead", "Executive", "US", "CMO"
}
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.org IS NOT NULL AND p.org <> '' RETURN p.id, p.name, p.org, p.occupation")
for r in rows:
    if r[2] and r[2] in suspicious_orgs:
        print(f"  FRAGMENT ORG: {r[1]} org='{r[2]}' occ='{r[3]}'")
```

#### Checks for Occupation-as-Org (job title in org field)
```python
occupation_keywords = {"Senior", "Director", "Partner", "Lead", "Chief",
                       "Head", "Principal", "Staff", "VP", "SVP", "EVP",
                       "CMO", "CFO", "CTO", "CEO", "COO", "Managing",
                       "Founder", "Co-Founder", "President", "Executive"}
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.org IS NOT NULL AND p.org <> '' RETURN p.id, p.name, p.org, p.occupation")
for r in rows:
    first = r[2].split()[0] if r[2] else ""
    if first in occupation_keywords:
        print(f"  OCC_IN_ORG: {r[1]} org='{r[2]}' occ='{r[3]}'")
```

#### Checks for Name in Occupation or City field
```python
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NOT NULL OR p.location_city IS NOT NULL RETURN p.id, p.name, p.occupation, p.location_city, p.org")
for r in rows:
    name = (r[1] or "").lower()
    occ = (r[2] or "").lower()
    city = (r[3] or "").lower()
    # Occupation contains the person's own first name
    name_parts = name.split()
    if occ and name_parts and name_parts[0] in occ:
        print(f"  NAME_IN_OCC: {r[1]} occ='{r[2]}'")
    # City contains name (e.g. "Heather Scoville Ladora, IA")
    if city and name_parts and (name_parts[0] in city or (len(name_parts) > 1 and name_parts[-1] in city)):
        print(f"  NAME_IN_CITY: {r[1]} city='{r[3]}'")
```

#### Checks for Bios/LinkedIn Text in Occupation
```python
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NOT NULL AND p.occupation <> '' RETURN p.id, p.name, p.occupation")
for r in rows:
    occ = r[2]
    if occ and len(occ) > 80:
        print(f"  LONG_OCC: {r[1]} len={len(occ)} '{occ[:80]}...'")
    # LinkedIn post text commonly starts with "LinkedIn"
    if occ and occ.lower().startswith("linkedin"):
        print(f"  LINKEDIN_TEXT: {r[1]} occ='{occ[:80]}...'")
```

### 3. Duplicate Detection

#### By Email (most reliable)
```python
cols, rows = safe_get_all(conn, """
    MATCH (p:Person) WHERE p.email IS NOT NULL AND p.email <> ''
    WITH p.email AS email, count(p) AS cnt,
         collect(p.id) AS ids, collect(p.name) AS names,
         collect(p.org) AS orgs
    WHERE cnt > 1
    RETURN email, cnt, ids, names, orgs
""")
print(f"Duplicate emails: {len(rows)}")
for r in rows[:5]:
    for i in range(r[1]):
        print(f"  {r[0]}: id={r[2][i]} name={r[3][i]} org={r[4][i]}")
```

#### By Name (same name, different IDs — complementary)
```python
cols, rows = safe_get_all(conn, """
    MATCH (p:Person) WHERE p.name IS NOT NULL AND p.name <> ''
    WITH p.name AS name, count(p) AS cnt,
         collect(p.id) AS ids, collect(p.org) AS orgs, collect(p.email) AS emails
    WHERE cnt > 1
    RETURN name, cnt, ids, orgs, emails ORDER BY cnt DESC
""")
```

### 4. Orphan Detection (nodes with no person edges)

Use OPTIONAL MATCH instead of NOT EXISTS (unsupported in LadybugDB):

```python
# Orphan Preferences
cols, rows = safe_get_all(conn, """
    MATCH (pref:Preference)
    OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
    WITH pref, person WHERE person.id IS NULL
    RETURN pref.id, pref.category, pref.value
""")

# Orphan Facts
cols, rows = safe_get_all(conn, """
    MATCH (f:Fact)
    OPTIONAL MATCH (person:Person)-[:HasFact]->(f)
    WITH f, person WHERE person.id IS NULL
    RETURN f.id, f.predicate, f.value
""")
```

### 5. Data Cleanup Operations

#### Clear Corrupted Fields
```python
def execute(conn, query, params=None):
    r = conn.execute(query, params or {})
    try:
        while True:
            r.get_next()
    except Exception:
        pass
    r.close()

# Clear truncated occupations
for pid, field in [(owner_UUID, "occupation")]:
    execute(conn, f"MATCH (p:Person {{id: $id}}) SET p.{field} = ''", {"id": pid})

# Clear fragment orgs
for pid in FRAGMENT_IDS:
    execute(conn, f"MATCH (p:Person {{id: $id}}) SET p.org = ''", {"id": pid})

# Clear garbage city field
for pid in GARBAGE_CITY_IDS:
    execute(conn, f"MATCH (p:Person {{id: $id}}) SET p.location_city = ''", {"id": pid})
```

#### Delete Null-Name Isolated Records
```python
# First check they have no relationships
for pid in null_name_ids:
    cols, rows = safe_get_all(conn, f"""
        OPTIONAL MATCH (p:Person {{id: '{pid}'}})-[r:Knows]->()
        OPTIONAL MATCH (p)-[r2:HasPreference]->()
        OPTIONAL MATCH (p)-[r3:HasFact]->()
        RETURN count(r) + count(r2) + count(r3)
    """)
    if rows and rows[0][0] == 0:
        execute(conn, f"MATCH (p:Person {{id: '{pid}'}}) DETACH DELETE p")
```

#### Delete Orphan Preferences/Facts
```python
# Delete orphan preferences
r = conn.execute("""
    MATCH (pref:Preference)
    OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
    WITH pref, person WHERE person.id IS NULL
    RETURN pref.id
""")
orphan_ids = []
while True:
    try:
        row = r.get_next()
        orphan_ids.append(row[0])
    except Exception as e:
        if "No more tuples" in str(e):
            break
        raise
r.close()

for pid in orphan_ids:
    execute(conn, f"MATCH (p:Preference {{id: '{pid}'}}) DELETE p")

# Same pattern for orphan Facts
```

### 6. Validation After Cleanup

```python
# Null names: should be 0 after cleanup
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.name IS NULL RETURN count(p)")
print(f"Null names: {rows[0][0] if rows else 0}")

# Orphan preferences: should be 0
cols, rows = safe_get_all(conn, """
    MATCH (pref:Preference)
    OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
    WITH pref, person WHERE person.id IS NULL
    RETURN count(pref)
""")
print(f"Orphan pref: {rows[0][0] if rows else 0}")

# Orphan facts: should be 0
cols, rows = safe_get_all(conn, """
    MATCH (f:Fact)
    OPTIONAL MATCH (person:Person)-[:HasFact]->(f)
    WITH f, person WHERE person.id IS NULL
    RETURN count(f)
""")
print(f"Orphan facts: {rows[0][0] if rows else 0}")
```

### 7. Snapshot Before Cleanup (optional)
```bash
cp <hermes-root>/commons/db/ocas-weave/weave.lbug \
   <hermes-root>/commons/db/ocas-weave/snapshots/weave-$(date +%Y%m%d-%H%M%S).lbug
```

## Key Considerations

### Safe Deletion Criteria
Only delete Person nodes when:
- Name is null AND
- No relationships of any type exist (Knows, HasPreference, HasFact)
- Verified via check above

Only delete orphan Preferences/Facts when confirmed disconnected from Person.

### Relationship Preservation
- **Knows edges**: never delete these — they link two real people
- **HasPreference/HasFact**: check person_id before deleting nodes, not edges
- Never `DETACH DELETE` a person with Knows edges unless you've confirmed it's a true duplicate

### What to Clear vs What to Delete
- **Corrupted occupation/org/city**: SET to empty string `''`
- **Garbage notes field**: SET to empty string
- **Null-name isolated Person**: DETACH DELETE
- **Orphan Preference/Fact with no Person edge**: DELETE
- **Duplicate Person with Knows edges**: MERGE properties into survivor, transfer edges, DELETE duplicate

## Known Data Corruption Patterns (from enrichment bug, discovered Apr 2026)

| Pattern | Example | Fix |
|---------|---------|-----|
| Occupation missing first chars | `'r Vice President Product and Exper'` | Clear field |
| Occupation mid-string | `'ing ManagerDesign'` | Clear field |
| LinkedIn text stored as occupation | `'LinkedIn Excited to start'` / `'LinkedIn sr vp'` | Clear field |
| Bio instead of occupation | `'Exciting progress as the foundation is being poured'` | Clear field |
| Person's own name in occupation | `'Benjamin Brown'` / `'Heather Scoville'` | Clear field |
| Single-word fragment org | `'Greater'`, `'Atlantic'`, `'Serving'`, `'Senior'` | Clear org |
| Job title in org field | `org='Senior'`, `org='CMO'`, `org='Director'` | Clear org |
| Person's own name in org | `'Jeffrey Hutchison & Associates'` | Clear org |
| Surname in org field | `'Georgeson'` (not a company) | Clear org |
| Name+location in city field | `'Heather Scoville Ladora, IA'` | Clear city |
| Occupation-as-org | org='Senior'/'Product'/'Finance'/'General' | Clear org |

## Fact Node Creation (LadybugDB-Specific)

LadybugDB **does not support MERGE** for creating nodes with unknown properties. The `CREATE (f:Fact {...})` pattern requires ALL node properties to be specified at creation time, including `id`. MERGE with `ON CREATE SET` fails with "expects primary key id as input".

### Correct Pattern for Creating Facts

```python
import uuid
from real_ladybug import Database, Connection

db = Database(DB_PATH)  # read_only=False
conn = Connection(db)

# 1. CREATE the Fact node with ALL properties including id
fact_id = str(uuid.uuid4())
conn.execute(f"""
    CREATE (f:Fact {{id: '{fact_id}', predicate: 'predicate_name', value: 'some value',
        source_type: 'system', source_ref: 'reference_tag',
        confidence: 0.9, record_time: '{now}'}})
""")

# 2. CREATE the relationship separately (HasFact has NO properties)
conn.execute(f"""
    MATCH (p:Person {{id: '{person_id}'}}), (f:Fact {{id: '{fact_id}'}})
    CREATE (p)-[:HasFact]->(f)
""")

# 3. For updating existing facts, use SET:
conn.execute(f"""
    MATCH (p:Person {{id: '{person_id}'}})-[:HasFact]->(f:Fact {{predicate: 'predicate_name'}})
    SET f.value = 'new value', f.record_time = '{now}'
""")

# 4. To check if a fact exists before creating:
r = conn.execute("""
    MATCH (p:Person {id: $person_id})-[:HasFact]->(f:Fact {predicate: 'predicate_name'})
    RETURN count(f)
""", {'person_id': person_id})
existing = r.get_next()[0] if r.get_next() else 0
```

**Key rules:**
- `value` must be a string (even for numbers: `'7.7'` not `7.7`)
- `confidence` must be a float (0.0-1.0)
- Relationship `HasFact` has **no properties** (no `fact_key`)
- Always use parameterized queries to prevent SQL injection

## System Fact Nodes (Quality & Enrichment Tracking)

Two system Fact predicates track data quality and enrichment status (created Apr 2026):

### data_quality_score (0-10 scale)
```python
# Score calculation considers:
# - Full name: 0.75 points
# - Contact methods: email (0.25), phone (0.5), custom email domain (0.25 bonus)
# - Multiple contact methods: 0.5 bonus
# - Work data: org (0.5), occupation (0.5)
# - Location: city (0.5), country (0.25)
# - Socials from Facts: up to 2.0 points
# - Family/relationships from Facts: up to 1.5 points
# - Career history from Facts: up to 1.0 points
# - Interests from Facts: up to 1.0 points
# - Education from Facts: up to 0.5 points
# - Content/publications from Facts: up to 0.5 points
# - Enrichment source quality: up to 1.5 points
# Total capped at 10.0
```

### enrichment_status
- `enriched` — properly researched via Scout methodology (44 contacts)
- `enriched_corrupt` — old web_enrichment pipeline (broken data) (534 contacts)
- `not_enriched` — untouched since Google import (453 contacts)

### enrichability_score (0-10 scale)

A system-computed Fact predicate that ranks how much a contact would benefit from an enrichment pass. Higher = more to gain. Stored as a Fact node (like `data_quality_score`) since LadybugDB does not support dynamic Person properties.

**Score components (0-10 scale):**
- **Remaining gaps** (0-4 pts): number of empty enrichable fields (org, occupation, location_city, email, phone) not yet covered by web_enrichment facts. More gaps = higher score.
- **Seed quality** (0-3 pts): how much data exists to search with. Full name (given+family) = 1pt base, each additional filled field = 0.4pt. Better seed = more likely to find data.
- **Connection value** (0-2 pts): log2(connections+1) * 0.6. More connected contacts are higher-value enrichment targets.
- **Source reliability** (0-1 pts): imported=1.0, direct=0.9, scout_research=0.8, inferred=0.5, web_enrichment=0.4, user-stated=0.3. Imported contacts from Google are preferred.
- **Enrichment penalty** (-0.5 per field): each gap already covered by web_enrichment facts reduces score (diminishing returns).
- **Completeness penalty** (-0 to -1): data_quality_score / 10. Already-complete contacts score lower.

**Score interpretation:**
- **7-10**: Best candidates — good seed data, multiple gaps, imported source, not yet enriched
- **4-6**: Moderate — some gaps remain, decent seed data
- **1-3**: Low priority — few gaps or poor seed data
- **0.5**: All gaps already covered by enrichment (may still have value for re-enrichment)
- **0.0**: Complete (no gaps) or insufficient seed data (<2 fields)

**Lifecycle:**
- Populated initially by `scripts/recalculate_enrichability.py` (batch recalculation for all contacts)
- Updated automatically after each successful `enrich_weave_contact()` call in `overnight_enrichment.py`
- Should be recalculated periodically (e.g. nightly cron) to stay current as contacts are manually edited or synced

**Query to find most enrichable contacts:**
```cypher
MATCH (p:Person)-[:HasFact]->(f:Fact {predicate: 'enrichability_score'})
WHERE toFloat(f.value) >= 5.0
RETURN p.name, f.value AS enrichability
ORDER BY enrichability DESC
LIMIT 20
```
Note: `toFloat()` is not available in LadybugDB. Sort by string value (works for same-length numbers) or sort in Python.

**Recalculation script:**
```bash
python3 <hermes-root>/skills/ocas-weave/scripts/recalculate_enrichability.py
```

These Facts are internal to Weave and **do not sync** to Google Contacts (sync only exports Person-level fields).

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
- Weave sync scripts in `<hermes-root>/scripts/`
- Weave database at `<hermes-root>/commons/db/ocas-weave/weave.lbug`
- Schema reference: `skill_view('ocas-weave', 'references/schemas.md')`