# Enrichment pipeline execution

The pre-flight and write procedure for an enrichment run. Read this when
starting a run — not as background reading. Under cron, read
`cron-mode-notes.md` first for the tooling constraints.

## Pre-Enrichment Checklist
- [ ] Run inbound Google sync (`AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root python3 -u {skill_root}/scripts/google_sync.py`) — inbound must land before enrichment, else gap queries read stale rows and write facts against contacts that no longer exist
- [ ] Check SearXNG health (diagnostic curl in `references/enrichment-pipeline.md`) — a dead engine silently returns empty, which the no-fabrication rule then reads as "no data"
- [ ] **Probe ALL discovery sources before committing to the run**: `python3 scripts/discovery_probe.py`. If every source reports down (web_search empty, SearXNG engines suspended, DDG anomaly-blocked, no LinkedIn MCP), do NOT start per-contact processing — follow the **Discovery Source Availability & No-Fabrication Rule** (defer real people, skip non-persons/unresolvables, write no facts). If ≥1 source is live, proceed with the fallback chain in `references/discovery-fallback.md`.
- [ ] **Clear pre-existing garbage** — scan for known junk values before enriching (why: COALESCE preserves whatever is already there, so a stale junk org/title survives forever if you enrich on top of it)
- [ ] **Remove stale DB copies** — a `parents[2]` path bug in scripts can create DB files at `<hermes-home>/commons/db/ocas-weave/weave.sqlite`, `<hermes-home>/profiles/commons/db/ocas-weave/weave.sqlite`, and `<hermes-home>/profiles/indigo/skills/commons/db/ocas-weave/weave.sqlite`. Only the canonical path (`<hermes-home>/profiles/indigo/commons/db/ocas-weave/weave.sqlite`) is correct. Stale DBs silently absorb subagent enrichment writes, so the work appears to vanish.
- [ ] **Check edges FK constraint** — run `python3 -c "import sqlite3; c=sqlite3.connect('<hermes-home>/profiles/indigo/commons/db/ocas-weave/weave.sqlite'); r=c.execute('PRAGMA foreign_key_list(edges)').fetchall(); print(r)"`. If `target_id` references `persons(id)`, recreate the `edges` table without that FK before enriching. `target_id` is polymorphic (can point to `facts.id` or `preferences.id`) — a wrong FK makes `HasFact` edge inserts fail with no error surfaced.
- [ ] Query contacts with gaps (`references/query_patterns.md`)
- [ ] Process each contact through Scout → Sift → Sherlock → Write
- [ ] Run periodic Google sync after every 10 enriched contacts
- [ ] Final Google sync

**I/O examples**

| Direction | Example | Result |
|---|---|---|
| In (inbound) | `AGENT_ROOT=... python3 -u scripts/google_sync.py` | Contact rows + identifiers updated; `sources.sync_checkpoint` advances |
| In (enrich) | `python3 scripts/discovery_probe.py` | Per-source availability table; exit 1 if all sources down |
| In (read) | `python3 -c "from weave_sqlite import WeaveDB; ..."` | `persons` rows joined to `facts` via `edges` |
| Out (write) | three-step persons UPDATE → facts INSERT → edges INSERT (`references/enrichment-write-pattern.md`) | One new fact with `source_type`/`source_ref`/`confidence` set |
| Out (sync) | `python3 scripts/google_sync.py` | PATCH to People API for `writeback.google_contacts`-eligible fields only |

## Unresolvable Contacts Protocol
When a contact's name is common and no email/phone/location disambiguates,
**skip** — never guess a match. Log to `decisions.jsonl` with reason
`skip_unresolvable`. Full decision flow: `references/unresolvable-contacts.md`.

**Script note**: shared extraction/search/validation lives in
`scripts/weave_enrich.py`; `quick_enrich.py` and `overnight_enrichment.py` both
import it — do not re-implement it per script.

Manual enrichment of a high-value contact, Google sync match rules, constraints,
and the gotcha catalog: `references/enrichment-operations.md`.

## After the run

- [ ] Final Google sync
- [ ] Read back every write by primary key
- [ ] Log material decisions to `decisions.jsonl`
- [ ] Journal the run
