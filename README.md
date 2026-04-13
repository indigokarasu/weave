# 🕸️ Weave

Weave maintains a private, provenance-backed social graph of people, relationships, preferences, and shared experiences -- queryable for meeting prep, gift ideas, hosting, introductions, city connections, and serendipity discovery. Every stored fact carries source type, reference, timestamp, and confidence score; the graph never silently merges two person records and never writes back to external systems without explicit per-sync approval.


Skill packages follow the [agentskills.io](https://agentskills.io/specification) open standard and are compatible with OpenClaw, Hermes Agent, and any agentskills.io-compliant client.

---

## Overview

Weave maintains a private social graph where every stored fact carries provenance -- source type, reference, timestamp, and confidence score. It is designed for the kind of knowledge that matters in relationships: who someone is, how they connect to others, what they like, what experiences you have shared. Queries support meeting prep, gift ideas, hosting context, city connections, and serendipity discovery across the graph. The database never silently merges two person records, never writes back to external systems without explicit per-sync approval, and uses only Cypher for all graph operations. The underlying database (LadybugDB) initializes automatically at `{agent_root}/commons/db/ocas-weave/weave.lbug`.

## Commands

| Command | Description |
|---|---|
| `weave.upsert.person` | Add or update a person record |
| `weave.upsert.relationship` | Add or update a Knows edge between two people |
| `weave.upsert.preference` | Store a provenance-backed preference for a person |
| `weave.import.csv` | Bulk import contacts via COPY FROM |
| `weave.query` | Query the graph (lookup, connection, serendipity, city, summarize, gift modes) |
| `weave.attach` | Query an external skill database read-only |
| `weave.export` | Export data to staging directory via COPY TO |
| `weave.sync.google-contacts` | Bidirectional sync with Google Contacts |
| `weave.sync.clay` | Bidirectional sync with Clay |
| `weave.project.vcard` | Generate vCard 4.0 draft |
| `weave.writeback.contacts` | Push records to Google Contacts or Clay (disabled by default) |
| `weave.init` | Diagnostic and repair: checks schema, creates missing tables |
| `weave.status` | Graph health and config state |
| `weave.journal` | Write journal for the current run |
| `weave.update` | Pull latest from GitHub source (preserves journals and data) |

## Setup

`weave.init` runs automatically on first invocation and creates all required directories, config.json, and the LadybugDB database. No manual setup is required. It also registers the `weave:update` cron job (midnight daily) for automatic self-updates.

## Dependencies

**OCAS Skills**
- [Elephas](https://github.com/indigokarasu/elephas) -- Chronicle enrichment read-only
- [Scout](https://github.com/indigokarasu/scout) -- OSINT findings as upsert candidates
- [Dispatch](https://github.com/indigokarasu/dispatch) -- social context for communication drafting

**External**
- LadybugDB -- embedded single-file graph database (auto-created at `{agent_root}/commons/db/ocas-weave/weave.lbug`)
- Google Contacts (optional bidirectional sync)
- Clay (optional bidirectional sync -- Clay is enrichment source, Weave provenance wins conflicts)

## Scheduled Tasks

| Job | Mechanism | Schedule | Command |
|---|---|---|---|
| `weave:update` | cron | `0 0 * * *` (midnight daily) | Self-update from GitHub source |

## Changelog

### v2.6.0 — April 12, 2026
- Document Google Contacts sync procedure, OAuth scopes, and pitfalls; Clay → Mesh MCP migration note

### v2.4.0 -- April 2, 2026
- Structured entity observations in journal payloads (`entities_observed`, `relationships_observed`, `preferences_observed`)
- `user_relevance` tagging on journal observations and optional signal emission (default `user` for social graph entities)
- Elephas journal cooperation in skill cooperation section

### v2.3.0 -- March 27, 2026
- Added `weave.update` command and midnight cron for automatic version-checked self-updates

### v2.2.0 -- March 22, 2026
- Routing improvements

### v2.1.0 -- March 22, 2026
- Mandatory journal entries at end of every run
- Standardized path usage across all commands

### v2.0.0 -- March 18, 2026
- Initial release as part of the unified OCAS skill suite
---

*Weave is part of the [OCAS Agent Suite](https://github.com/indigokarasu) -- a collection of interconnected skills for personal intelligence, autonomous research, and continuous self-improvement. Each skill owns a narrow responsibility and communicates with others through structured signal files, shared journals, and Chronicle, a long-term knowledge graph that accumulates verified facts over time.*
