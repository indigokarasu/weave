---
name: ocas-weave
license: MIT
description: >-
  Private provenance-backed social graph. Maintains queryable records of people,
  relationships, preferences, and shared experiences for recall, gifting,
  hosting, introductions, and serendipity. Use for storing or retrieving facts
  about a person or relationship, recording how two people know each other, or
  discovering connections between people, and for Google Contacts / Clay sync.
  NOT for sending messages (use Dispatch), calendar management (use Sands),
  OSINT research (use Scout), general knowledge-graph entity resolution, CRM or
  sales pipeline automation, personality profiling without evidence, or web
  research with no social-graph need (use Sift).
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
    config:
    - key: HERMES_HOME
      description: Profile root that owns the canonical DB and skill tree
      default: ~/.hermes/profiles/indigo
    - key: AGENT_ROOT
      description: "Profile root for google_sync.py; must be set explicitly or the sync aborts (why: it resolves sibling skill paths against it)"
      default: ~/.hermes/profiles/indigo
    - key: HERMES_PROFILE
      description: Profile name, used to build canonical DB paths
      default: indigo
    - key: OCAS_OPERATOR_EMAIL
      description: Google account whose contacts Weave syncs (operator-only auth rule)
      default: ""
    - key: WEAVE_BATCH_SIZE
      description: Contacts per enrichment batch
      default: "10"
    - key: WEAVE_DEADLINE_HOUR_ET
      description: Hour (ET) after which overnight enrichment stops
      default: "5"
    - key: WEAVE_ENRICH_ALL
      description: Set to bypass the cooldown gate on enrichment runs
      default: ""
    - key: WEAVE_ENRICH_DIR
      description: Override the directory holding the weave_enrich module under test
      default: ""
    - key: WEAVE_IGNORE_COOLDOWN
      description: Force a run past the per-contact cooldown
      default: ""
    - key: WEAVE_ONLY_FILE
      description: Restrict a run to a single contact id
      default: ""
    - key: WORKSPACE_MCP_CREDENTIALS_DIR
      description: Directory holding workspace MCP credentials
      default: ""
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

A private, provenance-backed social graph of people, relationships,
preferences, and shared experiences. Every stored fact carries source type,
reference, timestamp, and confidence; the graph never silently merges two
person records and never writes back to an external system without explicit
per-sync approval.

**Support files:** `references/support-file-map.md` indexes everything bundled
here. Check it before working from assumptions about what is available.

## When to Use

- Contact management and relationship tracking
- Social graph queries (who knows whom, how)
- Contact enrichment from multiple sources
- Store or update a person, relationship, or preference
- Meeting, dinner, or introduction prep
- Find connections in a given city
- Gift ideas grounded in known preferences
- Serendipity discovery between people
- Google Contacts or Clay sync

## When NOT to Use

- Sending messages or emails (use Dispatch)
- Calendar management (use Sands)
- OSINT research (use Scout)
- Knowledge graph entity resolution
- Web research with no social-graph need (use Sift)
- CRM or sales pipeline automation
- Personality profiling without evidence

## Auth, integrity, and scope

**Auth — <operator> only.** Weave exclusively uses <operator>'s Google auth
(`<user-google-email>`); `TOKEN_PATH` in `google_sync.py` is hardcoded to
`<user-google-email>.json`. Using the agent's account silently fetches the
wrong contact set. On a revoked token the sync exits 2 — re-auth the operator,
do not fall back to the agent account and do not work around it.

**ALWAYS sync Contacts after changes** via `scripts/google_sync.py` — the
canonical sync path (why: Weave is the graph of record but Google is where the
human reads the contact; a write that never syncs looks like it never happened,
and the next inbound sync will not restore a field it treats as
enrichment-derived).

**Five integrity rules that are not negotiable:**

- **Never silently merge two person records** — repair deliberately with
  corroborating identifiers; a silent merge destroys the evidence that the
  duplicate existed.
- **Never invent a fact from an empty search result** — it means "no data",
  never "write a best guess". This is the rule the whole no-fabrication path
  defends.
- **Never obfuscate, mask, or rewrite a stored value** — canonicalization
  (`url_norm.py`) changes representation, not meaning.
- **Report a failed write; do not retry it silently** — a retry loop against a
  polymorphic FK loses the fact without a trace.
- **Confirm a conflict resolution in the record, not a log line** — provenance
  fields travel with the fact.

**Entity types.** Person is the only entity Weave extracts and manages; a
record that is an organisation is gated out by `scripts/company_gate.py` and
never sent to person-OSINT. Chronicle observations attach to a Person only
when it carries high-confidence identity markers — otherwise a shared-venue
observation attaches two strangers to one person. Facts hang off a Person
through the `edges` table, not a `facts.person_id` column.

**Responsibility boundary.** Weave owns the social relationship graph and is
the only skill that writes to `weave.sqlite`. It does not do OSINT research
(Scout), calendars (Sands), or file organization (Bower).

## Storage and access

- **Backend**: SQLite in WAL mode via `weave_sqlite.WeaveDB` — many concurrent
  readers, one writer. If a write fails, surface the error; do not retry
  silently. Replaced LadybugDB in June 2026.
- **Schema, storage layout, Python usage pattern**: `references/schemas.md`.
  Import: `from weave_sqlite import WeaveDB`.
- **SQL templates for every query mode**: `references/query_patterns.md`.
- **Defaults**: `references/config-defaults.md`.
- **Auto-init**: every DB open runs `_ensure_init()` via `WeaveDB.__init__()`.
  There is no manual init command; `weave.init` is diagnostic and repair only.
  Pattern: `references/init_pattern.md`.

## Commands

- **weave.upsert.person / .relationship / .preference** — add or update a
  person, a `Knows` edge (confirm both Person nodes exist first), or a
  provenance-backed preference. Auto-inits the DB on first call.
- **weave.query** — query the graph. Modes: `lookup`, `connection`,
  `serendipity`, `city`, `summarize`, `gift`. `format: "concise"` (default —
  name, org, relationship category; ~70% fewer tokens for identity
  disambiguation) or `"detailed"` (all fields with provenance). Return only
  stored facts, with provenance.
- **weave.import.csv / weave.export** — bulk `COPY FROM` / `COPY TO`
  (`references/import_export.md`).
- **weave.sync.google-contacts / weave.sync.clay** — bidirectional sync
  (`references/connectors.md`).
- **weave.writeback.contacts** — push to Google or Clay. On by default;
  enrichment-derived values are withheld so only owner-sourced data ships.
- **weave.attach** — query an external skill database read-only.
- **weave.project.vcard** — generate a vCard 4.0 draft.
- **weave.status / weave.init** — report graph health; diagnose and repair.
- **weave.journal** — Write journal for the current run.

## Workflow

The Weave social graph pipeline: **record → enrich → query → discover**.

- [ ] Record people, relationships, preferences, and shared experiences
- [ ] Enrich contacts via Scout/Sift/Sherlock pipeline
- [ ] Query the graph for recall, gifting, hosting, introductions
- [ ] Discover serendipitous connections between people

## Run completion

After every Weave command:
- [ ] Persist new or updated records to the database
- [ ] Log material decisions to `decisions.jsonl`
- [ ] Write the journal via `weave.journal`
- [ ] **Read back every write** by primary key and confirm it matches intent.
      Never claim success unconfirmed.

## Provenance

Every written fact requires `source_type` (direct / inferred / imported /
user-stated), `source_ref`, `record_time` (ISO 8601), and `confidence`
(0.0–1.0). A fact without them is indistinguishable from a guess.

## Enrichment Pipeline Execution

**Before starting any enrichment run, read
`references/enrichment-pipeline-execution.md`** — the pre-flight checklist
(inbound sync, SearXNG health, discovery probe, junk sweep, stale-DB removal,
edges FK check) and the I/O examples for each direction.

The three most often skipped, and what each prevents:

- [ ] **Inbound sync first** — else gap queries read stale rows and facts land
      against contacts that no longer exist
- [ ] **Discovery probe** — an all-down result is not a null result; it means
      defer, do not fabricate
- [ ] **Stale-DB removal** — a `parents[2]` bug left three non-canonical
      database roots; subagent writes land in one and appear to vanish

## Google Contacts sync

Three rules carry most of the risk; full detail in `references/connectors.md`:
- Match by `google_resource_name`, then email, then phone. **Never match on name
  alone** — a name-only match merges a stranger's contact into your record.
- Gap-fill only — Weave provenance wins conflicts.
- Outbound needs `writeback.google_contacts: true` (the default) AND a previous
  sync checkpoint; without one there is no baseline, so the sync would push the
  whole book. Enrichment-derived values are withheld: Weave's guesses must not
  become the contact's own record.

## Discovery Source Availability & No-Fabrication Rule

**No-fabrication is non-negotiable:** an empty discovery result means "no data,"
never "write a best guess."

Run `python3 scripts/discovery_probe.py` at pipeline start. Fallback playbook,
rate-limit handling, and the three-way decision matrix:
`references/discovery-fallback.md`:
- ≥1 source live → proceed with the fallback chain (LinkedIn MCP → web_search →
  SearXNG → DDG)
- ALL sources down → defer real people, skip unresolvables, write NO facts
- `web_search` empty payload = non-functional; SearXNG `too many requests` =
  backoff and retry; `access denied` = won't recover this run

## OKRs, journals, and cooperation

- **OKR definitions and targets**: `references/okrs.md`.
- **Observation Journal** — query runs, upsert runs, import runs.
  **Action Journal** — sync runs, writeback runs.
- **Cooperation**: Chronicle supplies entity enrichment via journal payloads;
  Scout hands over OSINT findings as upsert candidates; Dispatch reads social
  context when drafting; Clay (Mesh MCP) is the CRM sync target.

## Background tasks

| Job name | Schedule | Command |
|---|---|---|
| `weave:sync-google` | `0 4 * * *` | `AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root python3 -u {skill_root}/scripts/google_sync.py` |
| `weave:enrichability-recalc` | `0 1 * * *` | `python3 {skill_root}/scripts/recalculate_enrichability.py` |

Skill updates are **not** one of these — see "Updating this skill" below.

## Cron invocation and overnight enrichment

**When running as a scheduled cron job: read `references/cron-mode-notes.md`
BEFORE acting** — the stale-runbook override table, the agent-driven
enrichment checklist, the schema reminder, and the cron tooling constraints
(`execute_code` is blocked; use `terminal`).

The short version: the cron runbook in the invocation message is not
authoritative — this skill is. Never stop a "bridge" or run
`enrichment_data.py`; both are gone.

## Updating this skill

`weave.update` is **retired**; updates run fleet-wide from the centralized
`skills:update-fleet` cron (`update_skill.sh` at the agent root), which never
discards uncommitted or unpushed local work. Do not `git pull` this skill in
place and do not re-add a per-skill updater — removed 2026-09-24 for
duplicating and contradicting the centralized updater. What to check when the
skill is stale: `references/self-update.md`.

## Error handling

Failure → symptom → handling table, and the three failures that cost
the most: `references/error-handling.md`. The short version: an empty search
result is not a null result, a 0-row UPDATE means the id you used does not
exist, and enrichment that vanished was written to a non-canonical database.

## Database maintenance

Audit, repair, and rollback procedures: `references/database_maintenance.md`.
Read it before any destructive purge, merge, or repair — each has a rollback.
Storage-backend evaluation (why SQLite adjacency lists replaced LadybugDB):
`references/graph-storage-backend-research.md`.

## Pitfalls

50+ entries covering migration, data integrity, enrichment quality, tool/API,
architecture, cron, and behavioral rules:
`references/pitfalls-weave.md`. The ones that bite hardest:

- **Never leave broken scripts after a migration** — update ALL dependents in
  the same session
- **Wrong `person_id` → silent 0-row UPDATE + orphaned fact** — copy IDs
  programmatically, never by hand
- **Imports inside `if __name__ == "__main__"` are invisible to module-level
  functions** — import at file top. A *function-local* import is fine and is the
  fix when the module lives in another skill (it keeps `--help` working when
  that skill is absent)
- **SQLite FK on polymorphic `edges.target_id`** — do NOT add
  `FOREIGN KEY (target_id) REFERENCES persons(id)`
- **`enrichment_data.py` does not exist** — use direct WeaveDB queries
- **Never obfuscate, mask, or invent stored data**

## Support File Map

Full index — the reach-for table plus ~70 one-off repair/audit helpers — is in
`references/support-file-map.md`.

## Visibility

public