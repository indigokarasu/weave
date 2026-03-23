---
name: ocas-weave
description: Weave: private provenance-backed social graph. Maintains queryable records of people, relationships, preferences, and shared experiences for recall, gifting, hosting, introductions, and serendipity. Trigger phrases: 'who do I know in', 'what does X like', 'add this person', 'relationship with', 'gift ideas for', 'sync contacts', 'prepare for meeting with'. Use when storing or retrieving facts about a person, recording a relationship, or discovering connections between people.
metadata: {"openclaw":{"emoji":"🕸️"}}
---

# Weave

Weave maintains a private, provenance-backed social graph using LadybugDB. All queries use Cypher. No SQL. The database initializes automatically on first use — no manual setup required.


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


## Responsibility boundary

Weave owns the private social graph: people, relationships, preferences, and shared experiences.

Weave does not own: general world knowledge (Elephas/Chronicle), OSINT research (Scout), web research (Sift), task management (Triage).

Weave is a standalone database. It does not write to Chronicle and has no runtime dependency on Chronicle. If a person in Weave also exists in Chronicle, Chronicle may store a `weave:person_id` reference on its Entity node. That is Chronicle's concern, not Weave's.


## Storage layout

```
~/openclaw/db/ocas-weave/
  weave.lbug          — LadybugDB database (auto-created on first use)
  config.json         — connector and sync configuration
  staging/            — temporary import/export files

~/openclaw/journals/ocas-weave/
  YYYY-MM-DD/
    {run_id}.json     — one journal per run
```


Default config.json:
```json
{
  "skill_id": "ocas-weave",
  "skill_version": "2.2.0",
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

```python
import real_ladybug as lb
from pathlib import Path


ROOT = Path("~/openclaw").expanduser()
DB_PATH = ROOT / "db/ocas-weave/weave.lbug"
STAGING = ROOT / "db/ocas-weave/staging"
JOURNALS = ROOT / "journals/ocas-weave"

def _open_db(read_only=False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    STAGING.mkdir(parents=True, exist_ok=True)
    db = lb.Database(str(DB_PATH), read_only=read_only)
    conn = lb.Connection(db)
    if not read_only:
        _ensure_init(conn)
    return db, conn

def _ensure_init(conn):
    tables = {row[0] for row in conn.execute("CALL show_tables() RETURN *")}
    if "Person" not in tables:
        _run_ddl(conn)

def _run_ddl(conn):
    # Full DDL from references/schemas.md
    pass  # See references/schemas.md for complete DDL
```


## Commands

**weave.upsert.person** -- Add or update a person. Auto-inits DB on first call. MERGE on `id`. Read back after write; report failure if no row returned — never claim success unconfirmed.

**weave.upsert.relationship** -- Add or update a `Knows` edge. Confirm both Person nodes exist first. Halt and report which is missing.

**weave.upsert.preference** -- Store a provenance-backed preference. Each preference is a distinct `CREATE` (not merged). Link to Person via `HasPreference` edge.

**weave.import.csv** -- Bulk import contacts via `COPY FROM`. Read `references/import_export.md`. Pre-process CSV to staging dir first. Check `CALL show_warnings() RETURN *` after. Report: N imported, N skipped (with reasons), N failed.

**weave.query** -- Query the graph. Read `references/query_patterns.md`. Modes: `lookup`, `connection`, `serendipity`, `city`, `summarize`, `gift`. Return only stored facts with provenance. Never speculate.

**weave.attach** -- Query an external skill database read-only. Read `references/cross_db.md`.

**weave.export** -- Export data to staging dir via `COPY TO`. Read `references/import_export.md`.

**weave.sync.google-contacts** -- Bidirectional sync with Google Contacts. Read `references/connectors.md`. Inbound before outbound. Outbound requires `writeback.google_contacts: true` AND explicit per-sync approval.

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


## Run completion

After every Weave command that reads or writes data:

1. Persist any new or updated records to the database
2. Log material decisions to `decisions.jsonl`
3. Write journal via `weave.journal` — Observation Journal for queries/upserts/imports, Action Journal for syncs/writebacks

## Provenance

Every written fact requires: `source_type` (direct / inferred / imported / user-stated), `source_ref`, `record_time` (ISO 8601), `confidence` (0.0–1.0). Use `event_time` when the real-world occurrence has a distinct time. Never write facts without provenance.


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
- Scout — receive OSINT findings about people as upsert candidates
- Dispatch — provide social graph context for communication drafting


## Journal outputs

- Observation Journal — query runs, upsert runs, import runs
- Action Journal — sync runs, writeback runs


## Initialization

On first invocation of any Weave command, `_open_db()` handles auto-initialization:

1. Create `~/openclaw/db/ocas-weave/` and subdirectories (`staging/`)
2. Write default `config.json` with ConfigBase fields if absent
3. Create `~/openclaw/journals/ocas-weave/`
4. Open database (auto-creates `weave.lbug` and runs DDL if tables absent)
5. Log initialization as a DecisionRecord

Weave is purely reactive. No cron jobs or heartbeat entries.


## Visibility

public


## Reference file map

File | When to read
`references/schemas.md` | Before any DDL, upsert, or import; before weave.init
`references/query_patterns.md` | Before any weave.query call
`references/import_export.md` | Before any COPY FROM or COPY TO operation
`references/cross_db.md` | Before any weave.attach call or Chronicle enrichment query
`references/connectors.md` | Before any sync with Google Contacts or Clay
`references/vcard_projection.md` | Before weave.project.vcard
`references/journal.md` | Before weave.journal; at end of every run
