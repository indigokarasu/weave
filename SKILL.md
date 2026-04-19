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
  version: "3.1.0"
  hermes:
    tags: [social-graph, people, relationships]
    category: memory
    cron:
      - name: "weave:update"
        schedule: "0 0 * * *"
        command: "weave.update"
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
        schedule: "0 0 * * *"
        command: "weave.update"
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
Google OAuth token at `<hermes-root>/google_token.json`. Client ID is `<GOOGLE_OAUTH_CLIENT_ID>.apps.googleusercontent.com`.

### Inbound Sync Procedure
1. **Check/Initialize Weave Schema**: Verify database has tables. Refer to `references/schemas.md` for full DDL.
2. **Fetch Google Contacts**: Use REST API via `urllib.request` (preferred) or `googleapiclient` SDK.
3. **Load Existing Weave State**: Load Person nodes by email and phone for cross-referencing.
4. **Cross-Reference and Sync**: Match by `google_resource_name`, then email, then phone. Apply enrichment logic (only fill NULL/empty fields).
5. **Track Changes**: Write to `<hermes-root>/data/hermes-weave/sync_history.jsonl`.
6. **Update Config**: Update `last_sync` in `config.json`.

### Write-Back (Outbound)
Requires `writeback.google_contacts: true` in config.json AND explicit per-sync user approval.

### Undo
Use `sync_id` from `sync_history.jsonl` to delete new records or revert enriched fields.

### Pitfalls
- **Other Contacts API**: `people_api.otherContacts()` is unreliable; use REST with `contacts.other.readonly`.
- **REST API Preference**: Use `urllib.request` as `googleapiclient` is missing in `execute_code` sandbox. Additionally, `googleapiclient.discovery.build` causes silent hangs (no output, no error) when run via `execute_code` or `terminal` background processes — always use `urllib.request` REST calls for Google People API.
- **sources enum**: The `sources` query parameter for People API connections must be `READ_SOURCE_TYPE_CONTACT` (not `READ_SOURCE_CONTACT`). This matters when calling the REST API directly.
- **Name Enrichment**: Incremental syncs typically focus on filling `name_given` and `name_family`.
- **Token Expiry**: `expiry` field can be ISO string or integer; handle both.
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
- **Google Drive/Docs**: Use OAuth tokens (`~/.hermes/google_token.json`) instead of service accounts to avoid 403 quota/permission errors.

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
- **Import pattern**: `from real_ladybug import Database` (top-level). `READ_ONLY`/`READ_WRITE` constants are NOT exported from `real_ladybug` — use `Database(path, read_only=True)` parameter instead. Connection: `lb.Connection(db)` then `conn.execute(cypher, params)`.

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


## Commands

**weave.upsert.person** -- Add or update a person. Auto-inits DB on first call. MERGE on `id`. Read back after write; report failure if no row returned — never claim success unconfirmed.

**weave.upsert.relationship** -- Add or update a `Knows` edge. Confirm both Person nodes exist first. Halt and report which is missing.

**weave.upsert.preference** -- Store a provenance-backed preference. Each preference is a distinct `CREATE` (not merged). Link to Person via `HasPreference` edge.

**weave.import.csv** -- Bulk import contacts via `COPY FROM`. Read `references/import_export.md`. Pre-process CSV to staging dir first. Check `CALL show_warnings() RETURN *` after. Report: N imported, N skipped (with reasons), N failed.

**weave.query** -- Query the graph. Read `references/query_patterns.md`. Modes: `lookup`, `connection`, `serendipity`, `city`, `summarize`, `gift`. Return only stored facts with provenance. Never speculate.

**weave.attach** -- Query an external skill database read-only. Read `references/cross_db.md`.

**weave.export** -- Export data to staging dir via `COPY TO`. Read `references/import_export.md`.

**weave.sync.google-contacts** -- Split into two independent jobs. Inbound: Google Contacts → Weave. Outbound: Weave → Google Contacts via BatchUpdateContacts. Staggered 30+ minutes apart to avoid 90 req/min quota contention. Outbound requires `writeback.google_contacts: true` in config — no per-sync approval step. Read `references/connectors.md` before any sync.

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

1. **Read** — Query Weave for the existing Person record. Confirm the record exists before any enrichment.
2. **Search** — Use the searchx skill (SearXNG via `execute_code`) for web research. If SearchX is not available, fallback to the native `web_search` tool.
3. **Enrich** — Add new Facts, Preferences, and relationship edges with full provenance (`source_type`, `source_ref`, `confidence`, `record_time`) on every write.
4. **Write** — Persist changes to Weave via `MERGE` on Person nodes and `CREATE` on Fact/Preference nodes. Always read back after write to confirm success.
5. **Search again** — With newly enriched data (company names, locations, titles), construct follow-up searches to find deeper information.
6. **Sync** — After every Weave write that touches data mapped in `references/google-field-map.md`, immediately sync to Google Contacts. Never update one without mirroring the other.

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

## Constraints

- Never use SQL.
- Never report a write as successful before read-back confirms it.
- Never parse or modify `.lbug`, `.wal`, `.shadow`, or `.tmp` files directly.
- Never write to Chronicle or any other skill's database.
- Never silently collapse two Person records into one.
- Use ontology standard relationship types in `Knows.rel_type`.
- Store useful, durable, socially actionable facts only.
- No outbound sync without explicit per-sync user approval.
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
| `weave:sync-google-inbound` | cron | `0 4 * * *` (4AM UTC) | `python3 {skill_root}/scripts/weave_sync_inbound.py` |
| `weave:sync-google-outbound` | cron | `30 4 * * *` (4:30AM UTC) | `python3 {skill_root}/scripts/weave_sync_outbound.py` |

Stagger inbound and outbound by 30+ minutes to prevent quota contention on the 90 req/min Google People API ceiling.

The `weave:sync-google-inbound` job reads all Google Contacts and gap-fills Weave. The `weave:sync-google-outbound` job pushes Weave changes to Google using BatchUpdateContacts to minimize API calls.

Manual invocation (`weave.sync.google-contacts`) runs both in sequence via `<hermes-root>/scripts/weave_google_bidirectional_sync.py`.

### Overnight Enrichment Pipeline

Script: `<hermes-root>/scripts/overnight_weave_enrichment.py`
Logs: `<hermes-root>/data/weave-enrichment/run.log`
Progress: `<hermes-root>/data/weave-enrichment/progress.jsonl`

**Re-processing pitfall**: The progress file tracks all contacted person IDs, but ~65% of searches return "no extractable data." If the filter excludes ALL progress-file IDs permanently, contacts that failed enrichment are never retried. Fix: only skip IDs that have `fields` (non-empty list) in the progress entry — contacts with empty `fields` should be re-attempted on subsequent runs since different search queries may yield results.


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


## Google Contacts sync

Weave maintains bidirectional sync with Google Contacts via two separate scripts (inbound and outbound run as independent cron jobs, staggered 30+ minutes apart to avoid quota contention):

- Inbound: `scripts/weave_sync_inbound.py` — Google Contacts → Weave
- Outbound: `scripts/weave_sync_outbound.py` — Weave → Google Contacts

**Why separate jobs?** Both inbound (paginated list of ALL contacts) and outbound (per-contact PATCH + etag GET) consume from the same 90 req/min Google People API quota. Running them sequentially causes cascading 429s. Staggering by 30+ minutes lets the quota window reset between runs.

**Inbound:** Google Contacts → Weave. Match by `google_resource_name`, then email, then phone. Never match on name alone. Gap-fill only — Weave provenance wins. Two-pass: read-only lookup maps first, then write pass.

**Outbound:** Weave → Google Contacts. Records modified since `last_sync.google_contacts` get PATCHed back to Google via `BatchUpdateContacts` (halves API consumption). Requires `writeback.google_contacts: true` in config. No per-sync approval step.

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
- Phone numbers may arrive with malformed leading `1` (e.g. `+1 (141)...`) — validate before storing
- **Token path**: use `<hermes-root>/google_token.json` for owner's contacts (owner is the Google Contacts account owner, not Indigo)
- **execute_code timeout**: The full `weave_google_bidirectional_sync.py` script times out in `execute_code` (300s limit) when outbound has 200+ contacts (2 API calls × 1.3s sleep each). Manual sync workaround: run inbound in one `execute_code` call (fast, ~30s), then run outbound in checkpointed batches. Use `staging/outbound_ckpt.txt` (one `google_resource_name` per line) to track progress — append after each successful push, load on resume to skip already-pushed contacts. Each batch handles ~150 contacts. Cron jobs don't have this issue since they run outside `execute_code`.


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

## Update command

This skill self-updates every 24 hours via:

```bash
weave.update
```

This pulls the latest version from GitHub and restarts the skill's background tasks if applicable.
