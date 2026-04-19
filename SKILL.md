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
  version: "2.6.0"
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


## Responsibility boundary

Weave owns the private social graph: people, relationships, preferences, and shared experiences.

Weave does not own: general world knowledge (Elephas/Chronicle), OSINT research (Scout), web research (Sift), task management (Triage).

Weave is a standalone database. It does not write to Chronicle and has no runtime dependency on Chronicle. If a person in Weave also exists in Chronicle, Chronicle may store a `weave:person_id` reference on its Entity node. That is Chronicle's concern, not Weave's.

## Ontology types

Weave works with these types from `spec-ocas-ontology.md`:

- **Entity/Person** — people in the social graph. Weave extracts and manages Person entities exclusively.

Weave may optionally emit Signals to Elephas for Person nodes with high-confidence identity markers, but this is not required for normal operation.

Each Signal emitted to Elephas must include a `user_relevance` field: `user` if the entity is directly related to the user's world, `agent_only` if encountered incidentally, `unknown` if unclear. Weave entities are almost always `user`-relevant since they represent the user's actual social connections.

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

**weave.update** -- Pull latest skill package from GitHub source. Preserves journals and data.


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

```
# Task declared in SKILL.md frontmatter metadata.{platform}.cron
```


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

Weave can be populated from Google Contacts via an inbound sync. Match contacts by `google_resource_name`, then email, then phone. Never match on name alone — risk of false duplicates is high.

**OAuth scopes required:**
- Read-only: `https://www.googleapis.com/auth/contacts.readonly`
- Full (incl. Other Contacts): `contacts` + `contacts.readonly` + `contacts.other.readonly`
- Write-back to Google: `contacts` scope + explicit per-sync user approval

**Known pitfalls:**
- `otherContacts()` API is unreliable — use REST with `contacts.other.readonly` scope instead
- `expiry` field in token may be ISO string or integer; handle both
- Scope expansion always requires re-auth with `prompt=consent&access_type=offline`
- Bulk imports (>100 rows) should use `COPY FROM` not individual inserts
- Phone numbers may arrive with malformed leading `1` (e.g. `+1 (141)...`) — validate before storing
- Provenance for imported contacts: `source_type='imported'`, `confidence=0.8`

**Write-back:** Requires `writeback.google_contacts: true` in config.json AND explicit user approval per sync. Never write back without both.


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
---

## Integrated: graph-expansion-pipeline (see references/)

The full Graph Expansion Pipeline documentation is stored in `references/graph-expansion-pipeline.md`. It covers the multi-phase Scout → Sift → Weave enrichment workflow for batch contact processing.

Related files:
- `references/graph-expansion-pipeline.md` — full pipeline spec
- `references/graph-expansion-pipeline-execution.md` — practical execution lessons
- `references/ocas-expansion.md` — expansion queue orchestration

---

## Integrated: weave-sync-performance

# Weave Sync Performance Fix (Updated Apr 2026)

## Problem
`weave:sync-contacts` outbound sync (Weave → Google) hits Google's 90 req/min People API quota. With 887 contacts, each requiring 2 API calls (etag GET + PATCH), cascading 429s cause timeout or partial completion.

## Root Causes

1. **Dual-quota consumption**: Both GET (etag fetch) and PATCH (update) count against the same 90 req/min bucket. With 2 calls/contact, ~45 contacts/min is the ceiling — not 60+.
2. **Quota contention between inbound and outbound**: Running them sequentially means outbound starts already rate-limited from inbound burning quota. Stagger by 30+ minutes.
3. **Backoff starting too low**: `backoff = 0.5s` is useless against a 90/min rolling window — it exhausts retries before the window clears. Start at **5s minimum**.
4. **Sleep too aggressive**: `0.3s` between records = 3.3 records/s = 200/min at 2 calls each, guaranteed 429s. Correct: **1.3s** (~45/min at 2 calls).

## Correct Values

| Parameter | Old | Correct |
|---|---|---|
| Sleep between contacts | 0.3s | **1.3s** |
| Backoff initial | 0.5s | **5.0s** |
| Backoff max attempts | 4 | 4 |
| Backoff escalation | 2x | 2x (5→10→20→40s) |
| 502 retry | none | **once after 5s** |

## Split Architecture (Recommended)

Run inbound and outbound as **two separate cron jobs**, staggered 30+ minutes:

```
weave:sync-google-inbound   0 4 * * *  (4AM UTC)
weave:sync-google-outbound 30 4 * * *  (4:30AM UTC)
```

Rationale: The inbound `connections().list()` paginates through ALL Google contacts and consumes significant quota before outbound even starts. Separating them lets each run in a fresh quota window.

Manual runs: use the bidirectional script if you want both in sequence, but expect rate limiting on large datasets.

## Correct Fixes (in-place)

### Patch the shared script
File: `/root/.hermes/scripts/weave_google_bidirectional_sync.py`

```python
# Line ~234: backoff start
backoff = 5.0   # was 0.5

# Line ~252: per-contact sleep
time.sleep(1.3)  # was 0.3

# In the retry loop for 429:
backoff *= 2  # 5 → 10 → 20 → 40s

# Add after existing 429 handler:
elif "502" in err:
    if attempt < 2:
        time.sleep(5.0)  # retry once
    else:
        failed += 1
```

## Outbound-Only Script

When only outbound sync is needed (no new Google contacts), bypass the full bidirectional script entirely:

```
python3 /root/.hermes/scripts/weave_outbound_only.py
```

This skips the paginated `connections().list()` read and goes straight to pushing Weave records — saves ~10+ quota units on large address books.

## Performance Numbers

- Before fix: cascading 429s, timeout, 0 contacts synced
- With backoff=5s, sleep=1.3s: 887 contacts pushed, 1 transient 502 failure, 22 rate-limited (all recovered), completed in ~22 min
- Throughput: ~45 contacts/min (at the 90/min ceiling with 2 calls/contact)

## Key Discovery (Apr 2026)

The `Refresh session has been revoked` error was a transient Nous auth issue. The real bottleneck was always rate limit cascading from dual-call-per-contact + insufficient backoff + no staggering between inbound and outbound.

## Token & Account Mismatch (Critical, Apr 2026)

**There are two Google token files, each tied to a DIFFERENT Google account:**

| File | Account | Has `contacts` scope? | Can access Weave contacts? |
|---|---|---|---|
| `/root/.hermes/google_token.json` | Jared | No (not listed) | **YES** — use this one |
| `/root/.hermes/indigo_google_token.json` | Indigo | Yes | No — 404 on all Weave contacts |

**Why this works:** `google_token.json` was issued with contacts scope at some point but the `scopes` field in the JSON doesn't reflect it. The token still works for all People API operations (list, get, patch).

**Why this is dangerous:** If you re-authorize `google_token.json` without explicitly requesting contacts scope, the token will lose access. Always include `contacts` scope in re-auth.

**Rule:** Always use `/root/.hermes/google_token.json` for Weave ↔ Google sync. Never use `indigo_google_token.json` — it's a different account and will return 404 on every contact.

## searchContacts Scope Requirements

The `POST /v1/people:searchContacts` endpoint **requires `contacts` scope** — returns 404 without it. The `GET /v1/people/me/connections` endpoint works with just `directory.readonly`.

**Workaround when searchContacts is unavailable:** Paginate through ALL contacts via `connections.list` and build a name→resourceName map:

```python
name_to_rn = {}
page_token = None
while True:
    url = f"https://people.googleapis.com/v1/people/me/connections?personFields=names&pageSize=1000"
    if page_token:
        url += f"&pageToken={page_token}"
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {access_token}'})
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    for c in data.get('connections', []):
        na = c.get('names', [])
        if na and na[0].get('displayName'):
            name_to_rn[na[0]['displayName']] = c.get('resourceName', '')
    page_token = data.get('nextPageToken')
    if not page_token:
        break
    time.sleep(0.3)
```

This takes ~2-3 seconds per 1000 contacts (with pagination). For ~900 contacts, about 3 seconds total.

## Recovering Cleared google_resource_name

If `google_resource_name` gets cleared from Weave records (e.g., wrong token used, or contact deleted from Google), re-link them:

1. Build the name→resourceName map from Google (see above)
2. For each Weave record missing `google_resource_name`, look up by display name
3. Update Weave: `MATCH (p:Person {name: $name}) SET p.google_resource_name = $rn`
4. Then proceed with normal outbound PATCH sync

**Do NOT clear `google_resource_name` on 404 from a wrong token** — verify the token is correct first. A 404 from `indigo_google_token.json` doesn't mean the contact is gone; it means you're on the wrong account.

## Database Lock Contention

LadybugDB is single-writer. If another process holds the lock (e.g., a cron sync job running in the background), operations fail immediately.

**Detection:**
```bash
lsof /root/.hermes/commons/db/ocas-weave/weave.lbug
```

**Resolution:**
```bash
kill -9 $(lsof -t /root/.hermes/commons/db/ocas-weave/weave.lbug)
```

Wait 1-2 seconds after killing before opening the database. Common lock holders:
- Cron job `weave:sync-google-inbound` or `weave:sync-google-outbound`
- Manual bidirectional sync script
- Previous execute_code session that didn't close cleanly

**Warning:** Killing a sync mid-operation may leave partial state. Check `last_sync` in config.json and re-run if needed.

---

## Integrated: weave-icloud-sync

# Weave → iCloud Contact Sync (No-Directional / Push-Only)

## Overview

Push contacts from the Weave social graph to iCloud Contacts using Apple's CardDAV API.
**Weave is the source of truth. iCloud is a read-only mirror.**

This is intentionally **no-directional**: no inbound sync, no conflict resolution,
no delete propagation. Weave pushes outward; iCloud never pulls back.

## Why Push-Only?

Full bidirectional sync with iCloud is complex because:
- iCloud doesn't expose a clean "last modified" timestamp per contact
- Conflict resolution (iCloud edit vs Weave edit) requires a merge strategy
- Delete propagation (contact deleted in iCloud) needs reconciliation logic

Push-only captures the key value: contacts created/updated in Weave appear in the
iCloud app on iPhone/Mac, accessible to any iOS app, without building a sync engine.

## Credentials Required

| Credential | Where to get |
|---|---|
| Apple ID email | Your Apple account |
| App-Specific Password | `appleid.apple.com` → Sign In → Security → App-Specific Passwords |

⚠️ **Never use your regular Apple password.** App-specific passwords are required for
two-factor authentication with third-party apps.

## CardDAV API Reference

**Base URL:** `https://contacts.icloud.com`

### Step 1 — Discover Principal URL

```
GET /principal
Authorization: Basic <base64(email:app-password)>
```

Response is a DAV:multistatus XML. Extract the `DAV:href` containing `/principal` —
this is your **principal URL**.

### Step 2 — Discover Address Book Home Set

```
GET /homeset
Authorization: Basic <base64(email:app-password)>
```

Response contains `DAV:collection` entries. Find the one with
`DAV:resourcetype` containing `addressbook`. Its `DAV:href` is your
**addressbook URL**, e.g. `/homeset/<guid>/`.

### Step 3 — Push Contacts (PUT per contact)

```
PUT {addressbook_url}/{guid}.vcf
Authorization: Basic <base64(email:app-password)>
Content-Type: text/vcard; charset=utf-8
```

Body: VCARD 3.0 string. On success: `201 Created` (new) or `200 OK` (update).

### Step 4 — Track Sync State

Store in `config.json`:
- `last_sync.icloud` — ISO timestamp of last sync
- `weave_icloud_uid_map` — dict mapping Weave person `id` → iCloud `guid` (without `.vcf`)

## Weave → VCARD 3.0 Field Map

| Weave field | VCARD property | Notes |
|---|---|---|
| `name` | `FN` | Required. Display name |
| `name_given` + `name_family` | `N` | `N;TYPE=work:family;given;extra;;;` |
| `email` | `EMAIL;TYPE=INTERNET` | First email only |
| `phone` | `TEL;TYPE=CELL` | First phone only |
| `org` | `ORG` | Company name |
| `occupation` | `TITLE` | Job title |
| `location_city` | `ADR;TYPE=INTL` | City part only |
| `location_country` | `ADR;TYPE=INTL` | Country part only |
| `notes` | `NOTE` | Free text |

**Minimal VCARD 3.0 template:**
```
BEGIN:VCARD
VERSION:3.0
FN:{name}
N:{family};{given};;;
END:VCARD
```

## Implementation Pattern

```python
import requests, uuid, base64
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import weavelib as lb
from pathlib import Path

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", "/root"))
DB_PATH = AGENT_ROOT / "commons/db/ocas-weave/weave.lbug"
CONFIG_PATH = AGENT_ROOT / "commons/db/ocas-weave/config.json"

APPLE_EMAIL = os.environ.get("ICLOUD_EMAIL")
APP_PASSWORD = os.environ.get("ICLOUD_APP_PASSWORD")
ADDRESSBOOK_URL = None  # discovered at runtime

# ─── VCARD conversion ────────────────────────────────────────────────────────

def weave_to_vcard(person: dict) -> str:
    """Convert a Weave Person dict to a VCARD 3.0 string."""
    def esc(s):
        return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")

    name = esc(person.get("name", ""))
    given = esc(person.get("name_given", ""))
    family = esc(person.get("name_family", ""))
    email = esc(person.get("email", ""))
    phone = esc(person.get("phone", ""))
    org = esc(person.get("org", ""))
    title = esc(person.get("occupation", ""))
    city = esc(person.get("location_city", ""))
    country = esc(person.get("location_country", ""))
    notes = esc(person.get("notes", ""))

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{name}",
        f"N:{family};{given};;;",
    ]
    if email:
        lines.append(f"EMAIL;TYPE=INTERNET:{email}")
    if phone:
        lines.append(f"TEL;TYPE=CELL:{phone}")
    if org:
        lines.append(f"ORG:{org}")
    if title:
        lines.append(f"TITLE:{title}")
    if city or country:
        lines.append(f"ADR;TYPE=INTL:;;{city};;;{country}")
    if notes:
        lines.append(f"NOTE:{notes}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


# ─── CardDAV discovery ────────────────────────────────────────────────────────

def discover_addressbook(email: str, password: str) -> str:
    """Discover the user's iCloud addressbook URL via CardDAV principal + homeset."""
    auth = base64.b64encode(f"{email}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    base = "https://contacts.icloud.com"

    # 1. principal
    resp = requests.get(f"{base}/principal", headers=headers, timeout=15)
    resp.raise_for_status()
    # Parse DAV:href from XML
    import re
    hrefs = re.findall(r"<d:href>([^<]+)</d:href>", resp.text)
    principal = next((h for h in hrefs if "/principal" in h), None)
    if not principal:
        raise RuntimeError("No principal URL found in response")

    # 2. homeset
    resp = requests.get(f"{base}/homeset", headers=headers, timeout=15)
    resp.raise_for_status()
    hrefs = re.findall(r"<d:href>([^<]+)</d:href>", resp.text)
    # Find addressbook collection
    ab_url = next((h for h in hrefs if "/addressbook/" in h), None)
    if not ab_url:
        raise RuntimeError("No addressbook URL found in homeset response")
    return ab_url.rstrip("/")


# ─── Sync ────────────────────────────────────────────────────────────────────

def sync_outbound_icloud(db_path: Path, config_path: Path,
                         email: str, password: str,
                         dry_run: bool = False) -> dict:
    """
    Push Weave contacts modified since last_sync to iCloud via CardDAV.
    Returns: {pushed, failed, skipped}
    """
    auth = base64.b64encode(f"{email}:{password}".encode()).decode()
    headers_base = {"Authorization": f"Basic {auth}"}

    with open(config_path) as f:
        config = json.load(f)

    last_sync = config.get("last_sync", {}).get("icloud")
    uid_map = config.get("weave_icloud_uid_map", {})
    ab_url = config.get("icloud_addressbook_url")

    # Lazily discover addressbook URL
    if not ab_url:
        ab_url = discover_addressbook(email, password)
        config["icloud_addressbook_url"] = ab_url

    conn = lb.Connection(str(db_path))

    # Fetch modified Weave people
    query = """
        MATCH (p:Person)
        WHERE p.record_time > $ts
        RETURN p.id, p.name, p.name_given, p.name_family,
               p.email, p.phone, p.org, p.occupation,
               p.location_city, p.location_country, p.notes
    """
    rows = list(conn.execute(query, {"ts": last_sync or "1970-01-01T00:00:00Z"}))

    pushed = failed = skipped = 0
    for row in rows:
        pid = row[0]
        person = {
            "name": row[1], "name_given": row[2], "name_family": row[3],
            "email": row[4], "phone": row[5], "org": row[6],
            "occupation": row[7], "location_city": row[8],
            "location_country": row[9], "notes": row[10],
        }
        if not person.get("name"):
            skipped += 1
            continue

        guid = uid_map.get(pid) or str(uuid.uuid4())
        vcard = weave_to_vcard(person)
        url = f"{ab_url}/{guid}.vcf"

        if dry_run:
            print(f"[DRY] PUT {url}\n{vcard}")
            pushed += 1
            continue

        try:
            resp = requests.put(url, headers={**headers_base, "Content-Type": "text/vcard; charset=utf-8"},
                                data=vcard.encode("utf-8"), timeout=20)
            if resp.status_code in (200, 201):
                uid_map[pid] = guid
                pushed += 1
            else:
                failed += 1
                print(f"iCloud PUT failed ({resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            failed += 1
            print(f"iCloud error for {pid}: {e}")

    # Persist state
    config["last_sync"]["icloud"] = datetime.now(timezone.utc).isoformat()
    config["weave_icloud_uid_map"] = uid_map
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    return {"pushed": pushed, "failed": failed, "skipped": skipped}
```

## Rate & Error Handling

- **Rate limit**: Apple does not publish CardDAV limits. Use **1–2s delay** between contacts.
- **409 Conflict**: Contact was modified on iCloud since last sync — fetch iCloud version,
  merge field-by-field (Weave provenance wins), PUT back.
- **404 on PUT**: Addressbook URL changed. Re-run discovery.
- **401**: App-specific password expired or was revoked. Prompt user to regenerate.
- **Stale UID (404 on known guid)**: Contact was deleted from iCloud. Remove from `uid_map`.

## Config Extension

Add to `config.json`:

```json
{
  "last_sync": {
    "google_contacts": null,
    "clay": null,
    "icloud": null
  },
  "writeback": {
    "google_contacts": true,
    "clay": false,
    "icloud": true
  },
  "weave_icloud_uid_map": {},
  "icloud_addressbook_url": null
}
```

## Trigger Phrases

- "sync to iCloud"
- "push to iCloud"
- "export contacts to iCloud"
- "backup my contacts to iCloud"

## Environment Variables

| Variable | Description |
|---|---|
| `ICLOUD_EMAIL` | Apple ID email |
| `ICLOUD_APP_PASSWORD` | App-specific password (from appleid.apple.com) |

## Limitations

- **No inbound sync**: iCloud edits never come back to Weave
- **No delete propagation**: Deleted Weave contacts are NOT deleted from iCloud
- **No conflict resolution**: iCloud edits to contacts that Weave also modified are overwritten on next push
- **Single email/phone**: Only first email and phone per contact is pushed
- **App-specific password required**: Cannot use regular Apple ID password

## Future Enhancement Path

If bidirectional sync becomes needed:
1. Implement CardDAV **addressbook-query** (search by UID) to detect iCloud changes
2. Add inbound sync phase: compare iCloud modification timestamps vs `last_sync.icloud`
3. Weave provenance wins on conflicts (with optional merge pass for specific fields)
4. Track deleted UIDs via CardDAV addressbook-match addressbook-multiget with prop-filter

---

## Integrated: google-workspace-setup

# Google Workspace Setup

Configures Google Workspace access for Hermes Agent with proper account isolation.

## When to Use

- Setting up Gmail, Calendar, Drive, Contacts, Sheets, Docs access
- Adding a new Google account (personal and agent accounts must use separate profiles)
- Enabling Google Cloud APIs via service account
- Fixing authentication issues with existing Google integrations

## Account Isolation Rules

- The user's personal account (e.g. jared.zimmerman@gmail.com) uses /root/.hermes/
- The agent's account (e.g. mx.indigo.karasu@gmail.com) must use a SEPARATE profile directory
- Never mix tokens, clients, or credentials between accounts
- Each account needs its own OAuth Client ID + Client Secret from Google Cloud Console

## OAuth Setup (User Account)

1. Create OAuth Client in Google Cloud Console
2. Add Authorized JavaScript origins: http://localhost
3. Add Authorized redirect URIs: http://localhost:1
4. Configure scopes needed:
   - Gmail: gmail.readonly, gmail.send, gmail.modify
   - Calendar: calendar
   - Drive: drive.readonly
   - Contacts: contacts.readonly
   - Sheets: spreadsheets
   - Docs: documents.readonly
   - People: contacts or directory.readonly
5. Generate authorization URL with PKCE flow
6. User clicks URL in browser, authenticates with Google account
7. User pastes back the callback URL from browser address bar
8. Exchange code for tokens and save to /root/.hermes/google_token.json

For agent account (mx.indigo.karasu@gmail.com):
```bash
# Create separate profile directory
mkdir -p /root/.hermes-indigo/
# Use separate client ID/secret for agent
# Save token to /root/.hermes-indigo/google_token.json
```

## Service Account Setup

**Step 1: Create Service Account**
1. Go to Google Cloud Console → IAM & Admin → Service Accounts
2. Create service account (e.g., hermes@project.iam.gserviceaccount.com)
3. Grant appropriate roles (Editor for full access)
4. Create key → JSON format → download

**Step 2: Save Key**
```bash
mkdir -p ~/.hermes/credentials && chmod 700 ~/.hermes/credentials
# Save JSON key to: ~/.hermes/credentials/{project-name}.json
```

**Step 3: Activate with gcloud**
```bash
gcloud auth activate-service-account {sa-email} --key-file={path-to-key} --project={project}
```

**Step 4: Enable APIs**
```bash
# Enable via gcloud
gcloud services enable gmail.googleapis.com --project={project}
gcloud services enable drive.googleapis.com --project={project}
gcloud services enable people.googleapis.com --project={project}

# Or enable via Python API (works when gcloud has OpenSSL issues)
python3 -c "
from google.oauth2 import service_account
from googleapiclient.discovery import build
creds = service_account.Credentials.from_service_account_file('{key-path}')
su = build('serviceusage', 'v1', credentials=creds)
for api in ['gmail.googleapis.com', 'drive.googleapis.com', 'people.googleapis.com']:
    su.services().enable(name=f'projects/{project}/services/{api}', body={}).execute()
    print(f'Enabled {api}')
"
```

## Common API Endpoints to Enable

| API | When Needed |
|-----|-------------|
| gmail.googleapis.com | Email access |
| calendar.googleapis.com | Calendar management |
| drive.googleapis.com | File organization |
| people.googleapis.com | Google Contacts |
| sheets.googleapis.com | Spreadsheet access |
| docs.googleapis.com | Document editing |
| admin.googleapis.com | Workspace admin operations |
| cloudresourcemanager.googleapis.com | Project management |
| iam.googleapis.com | Service account management |
| serviceusage.googleapis.com | API enablement |

## Pitfalls

### Scope Expansion Requires User Re-Auth

When adding a new scope (e.g. `contacts`) to an existing token, `google_token.json` shows the old scopes. Simply re-authorizing with `prompt=consent&access_type=offline` upgrades the token with the new scopes. No need to delete/recreate the client.

**Workflow:**
```bash
# 1. Check what scopes are missing
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --check
# Exit 1 + AUTH_SCOPE_MISMATCH = scopes need upgrading

# 2. Generate auth URL with all scopes including new ones
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --auth-url
# Send URL to user, user visits → gets redirected → pastes code

# 3. Exchange code for upgraded token
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --auth-code {CODE}

# 4. Verify
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --check
```

**NOTE:** `setup.py --auth-url` always regenerates a fresh PKCE challenge. The pending auth state is saved to `google_oauth_pending.json`. The auth URL includes all scopes listed in `SCOPES` in `setup.py`, so `setup.py` must have the desired scopes listed BEFORE generating the URL.

### Token Refresh Fails with `invalid_scope` (Corrupted Token)

Even when `google_token.json` shows correct scopes, Google may reject token refresh with:
```
google.auth.exceptions.RefreshError: ('invalid_scope: Bad Request', {'error': 'invalid_scope'})
```

This means the token is corrupted and must be re-issued. Do not try to repair it.

**Re-authentication flow:**

```bash
# 1. Check current auth status (requires PYTHONPATH)
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --check

# 2. Generate fresh auth URL
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --auth-url

# 3. User visits URL, authorizes, copies the `code=` from redirect URL

# 4. Exchange code for new token
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --auth-code {CODE}

# 5. Verify
PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --check
```

The `--check` command returns exit code 0 on success, 1 on failure.

### Private Key Format Issues
- If gcloud says "Could not deserialize key data", check:
  - The key file must have actual newlines, not \n strings
  - The key must be exactly 64 chars per line
  - Check if the key is corrupted during paste/transfer

### Service Account Can't Access Personal Drive Files
- Service accounts have their own isolated Drive
- To access user's files: share files WITH the service account email
- For shared Drive access: add service account as member

### gcloud OpenSSL Compatibility
- gcloud sometimes has OpenSSL 3.0 compatibility issues
- Python google-api-python-client works reliably as alternative
- Always have both installed for fallback

### OAuth Token Expiry
- Access tokens expire in ~1 hour
- Refresh tokens should persist indefinitely
- Always check token validity before API calls:
```python
from google.auth.transport.requests import Request
if not creds.valid and creds.expired and creds.refresh_token:
    creds.refresh(Request())
```

### Google Calendar API Script Limitations

The `google_api.py` script has specific behaviors not obvious from usage:

**No Update Command:**
- `calendar update` does NOT exist
- To modify an event: delete then recreate
- Delete uses positional event_id: `calendar delete {event_id}` (not `--id` or `--event-id`)

**Timezone Required:**
- Start/end times MUST include timezone offset
- Valid: `2026-04-16T11:30:00-07:00`
- Invalid: `2026-04-16T11:30:00` (returns "Missing time zone definition")

**Create Command Arguments:**
```bash
python3 google_api.py calendar create \
  --summary "Event Title" \
  --start "2026-04-16T11:30:00-07:00" \
  --end "2026-04-16T12:30:00-07:00" \
  --location "Full Address" \
  --description "Details"
```

### Cron Job Fallback When Token is Expired

When a **cron job** encounters `invalid_grant` (no user present for re-auth):

1. **Do NOT suppress the error.** Surface it prominently in the briefing/output.
2. **Fall back to cached data** from `events.jsonl` or previous journal runs. The events.jsonl in `{agent_root}/commons/data/ocas-sands/` contains queried events with timestamps.
3. **Label all output as "cached/stale"** — make it clear the data may not reflect current calendar state.
4. **Generate the re-auth URL** so the user can fix it when they're next available:
   ```bash
   PYTHONPATH=/root/.hermes/hermes-agent python3 /root/.hermes/skills/productivity/google-workspace/scripts/setup.py --auth-url
   ```
5. **Log the auth failure** to decisions.jsonl and the skill journal.
6. **Write the briefing anyway** from cached data — a stale briefing is better than no briefing.

**Pattern for reading cached events:**
```python
# Read events.jsonl for previously-queried events on target date
import json
events = []
with open(f"{data_dir}/events.jsonl") as f:
    for line in f:
        ev = json.loads(line.strip())
        if target_date in ev.get("start", ""):
            events.append(ev)
```

**Key insight:** The Sands evening briefing queries events for the *next* day and writes them to events.jsonl. So the morning briefing can fall back to the previous evening's data even when the Google API is unreachable.

### Skill Invocation Pattern

OCAS skills (sands, weave, scout, etc.) are NOT CLI executables. Do NOT attempt:
- `hermes sands.event.create` — fails (not a hermes CLI command)
- `openclaw weave.upsert.person` — fails (openclaw CLI doesn't exist)
- `hermes chat --skill sands` — fails (no such flag)

**Correct approaches:**
1. Use the Google Workspace API scripts directly for Calendar/Gmail/Drive
2. Use `delegate_task` with a subagent that has the skill loaded
3. Access LadybugDB (Weave) directly via Python if the module is installed
4. For Scout: use web_search/web_extract directly for OSINT

If a skill's CLI entry point is missing, fall back to direct API calls or database access.

## Verification

Test each service after setup:
```python
# Gmail
from googleapiclient.discovery import build
service = build('gmail', 'v1', credentials=creds)
profile = service.users().getProfile(userId='me').execute()
print(f"Gmail connected: {profile['emailAddress']}")

# Drive
drive = build('drive', 'v3', credentials=creds)
results = drive.files().list(pageSize=5, fields="files(id, name)").execute()
files = results.get('files', [])
print(f"Drive: {len(files)} files visible")

# Calendar
calendar = build('calendar', 'v3', credentials=creds)
calendars = calendar.calendarList().list().execute()
print(f"Calendar: {len(calendars.get('items', []))} calendars")

# Contacts
people = build('people', 'v1', credentials=creds)
count = 0
page_token = None
while True:
    result = people.people().connections().list(
        resourceName='people/me',
        pageSize=1000,
        personFields='names,emailAddresses',
        pageToken=page_token
    ).execute()
    count += len(result.get('connections', []))
    page_token = result.get('nextPageToken')
    if not page_token:
        break
print(f"Contacts: {count} contacts")
```

## Environment Variables

```bash
# User's personal account
export GOOGLE_CLIENT_ID=your-oauth-client-id
export GOOGLE_CLIENT_SECRET=your-oauth-client-secret

# Agent's account (if separate)
export AGENT_GOOGLE_CLIENT_ID=agent-oauth-client-id
export AGENT_GOOGLE_CLIENT_SECRET=agent-oauth-client-secret

# Service account (alternative auth method)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

## Storage Locations

```
~/.hermes/
├── google_token.json              # User's OAuth token
├── google_client_secret.json      # User's OAuth client secrets
├── credentials/
│   ├── project-name.json          # Service account key
│   └── ...
└── hermes-indigo/                 # Agent's separate profile
    └── google_token.json
```

## Cron Job Pattern for Background Tasks

When registering background tasks from skills:
```bash
hermes cron list  # Check existing first

# Create new job
hermes cron add --name {skill:task} \
  --schedule "{cron_expression}" \
  --command "{skill.command}" \
  --sessionTarget isolated \
  --lightContext true \
  --timezone America/Los_Angeles
```

Common schedules:
- Updates: `0 0 * * *` (midnight daily)
- Morning briefs: `0 6 * * *`
- Evening briefs: `0 20 * * *`
- Weekly deep scans: `0 1 * * 0` (Sunday 1am)
- Weekday morning tasks: `0 6 * * 1-5`

All cron jobs:
- deliver to `local` to avoid chat spam
- Use isolated sessions
- Light context enabled
- America/Los_Angeles timezone

---

## Integrated: google-token-audit

# Google Token Audit

## When Google API Returns 403 or invalid_grant

**STOP.** Do NOT conclude that access is revoked or scopes are insufficient until you have:

1. **Found ALL token files on disk.** Search broadly:
   - `find /root/.hermes* -name "google_token*" -type f`
   - `find /root/.hermes* -name "*.json" | xargs grep -l "refresh_token"`
   - Check backup dirs (`*.backup`, `*.bak`, dated directories)

2. **Tested EACH token individually.** Load each one, attempt the failing API call, record success/failure.

3. **Compared scopes.** Decode each token's scope field. The broadest-scope working token wins.

## Key Lessons (Apr 2026)

- The Drive API allows **root-level file listing** even with narrow scopes (e.g., `contacts` only). This creates false confidence — you can list folders but not query inside them.
- `invalid_grant` during refresh does NOT always mean the token is dead. You may be using the wrong client secret, the wrong token file, or initializing the client incorrectly.
- **The user is almost always right** when they say "it's not a permission issue." Check your code before blaming the API.
- Legacy backup tokens can be valid and have broader scopes than the "current" one.

## Fix Pattern

1. Audit all google_token*.json files
2. Test each against a real Drive query (e.g., list files in a specific folder)
3. Identify the token with broadest scopes that actually works
4. Copy it to `~/.hermes/google_token.json`
5. Delete all obsolete tokens to prevent future confusion
6. Re-run the operation that was failing

## Common Locations

- `/root/.hermes/google_token.json` — primary
- `/root/.hermes/google_token.json.backup` — backup
- `/root/.hermes/google_token.json.bak` — backup
- `/root/.hermes/2026-04-06_21-34-18/google_token.json` — legacy backup
- `/root/.hermes-indigo/google_token.json` — Indigo account token

---

## Integrated: google-token-architecture

# Google Token Architecture

## Token Files (MANDATORY NAMING)
- **Jared's token**: `/root/.hermes/jared_google_token.json` (jared.zimmerman@gmail.com)
- **Indigo's token**: `/root/.hermes/indigo_google_token.json` (mx.indigo.karasu@gmail.com)
- **NEVER** use generic `google_token.json` — always use the explicit prefixed filenames

## OAuth Clients
- Jared's client_id: `112292610034-1revbmnkves56ago2c2t5dul5mj9bc17` (secret: `/root/.hermes/google_client_secret.json`)
- Indigo's client_id: `550801240087-vmc47b1gflj2biblqdr6bkekl7qqm8ls` (secret: `/root/.hermes-indigo/google_client_secret.json`)

## Scopes
Both tokens were re-authorized Apr 15 2026 with ALL possible Google Workspace scopes (gmail.readonly, gmail.send, gmail.modify, calendar, drive, contacts, directory.readonly, spreadsheets, documents, presentations, forms).

## Email Delivery Architecture
- **ocas-dispatch** owns the email lifecycle (send, scan, label, draft)
- **send_message_tool.py** — email path REMOVED; only a dumb pipe for non-email platforms (Telegram, Discord, Slack, etc.)
- **google_api.py** — low-level engine, callable by Dispatch but NOT directly for cron delivery
- **email-delivery-routing** — documentation skill only, not executable code
- **himalaya** — listed as a skill but NOT the correct tool; do NOT use for email
- PR posted: `dispatch-email-ownership` on indigokarasu GitHub

## Critical Rules
1. NEVER overwrite one account's token with another's. This was the root cause of the morning briefing delivery failure (Apr 12-14 2026).
2. When checking email delivery, check BOTH inboxes directly using the correct named tokens — never ask the user to confirm receipt.
3. If `invalid_grant` appears, check which token is actually being used before assuming the API is down.
4. When generating OAuth auth URLs, use the correct client_secret for the target account, and save the PKCE verifier to complete the exchange.

## Cron Delivery
- All cron jobs switched from Telegram delivery to `local` (Apr 15 2026) to stop spamming user's channel.
- If cron output still appears on Telegram, check for hardcoded delivery calls inside skill scripts bypassing the registry.
- Midnight UTC collision: many cron jobs fire simultaneously → rate limit cascade → API 429 errors.
