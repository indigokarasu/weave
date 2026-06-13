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
  version: 3.6.0
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

## Responsibility Boundary

Weave owns the social relationship graph: people, relationships, preferences, and shared experiences. It is the only skill that writes to its LadybugDB database.

Weave does not: perform OSINT research (Scout), manage calendars (Sands), organize files (Bower), or build the long-term knowledge graph (Elephas).

## Ontology types

- **Entity/Person** — people in the social graph. Weave extracts and manages Person entities exclusively.

Weave may optionally emit Signals to Elephas for Person nodes with high-confidence identity markers.

## LadybugDB usage guide

Read `references/ladybugdb-guide.md` for query result handling, iteration pitfalls, and import patterns.

## Storage layout

See `references/schemas.md` for the storage layout and record schemas. See `references/config-defaults.md` for default config.

## Database rules

LadybugDB is an embedded single-file database. One `READ_WRITE` process at a time. If another process holds the lock, operations fail immediately — surface the error, do not retry silently.

## Auto-initialization

Every command that opens the database runs `_ensure_init()` first. No manual init command needed.

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
- LadybugDB lock contention and bridge management
- Google OAuth token handling and cross-account contamination
- Python environment issues (`real_ladybug` vs `ladybug`)
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
| `weave:sync-google` | `0 4 * * *` | `python3 {skill_root}/scripts/google_sync.py` |
| `weave:enrichability-recalc` | `0 1 * * *` | `python3 {skill_root}/scripts/recalculate_enrichability.py` |

## Self-update

`weave.update` pulls the latest package from GitHub. See `references/self-update.md`.

## Database maintenance

See `references/database_maintenance.md`.

## Support File Map

| File | When to read |
|------|-------------|
| `references/schemas.md` | Before any DDL, upsert, or import |
| `references/gotchas-weave.md` | Before any Weave operation — full gotcha catalog |
| `references/connectors.md` | Before any Google/Clay sync |
| `references/ladybugdb-guide.md` | Query result handling, iteration, import patterns |
| `references/query_patterns.md` | Before any weave.query call |
| `references/import_export.md` | Before any COPY FROM/TO operation |
| `references/enrichment-pipeline.md` | Overnight enrichment architecture |
| `references/constraints.md` | Full constraint set |
| `references/config-defaults.md` | Default config structure |
| `references/self-update.md` | Self-update procedure |
| `references/database_maintenance.md` | Before Google sync, after enrichment runs |
| `references/recovery-weave.md` | Recovery contract details |

## Visibility

public
