# 🕸️ Weave

> **Private provenance-backed social graph — contacts, relationships, preferences, and shared experiences.**

## Why Weave?

You know a lot about the people in your life — their preferences, how you met, what you've shared — but that knowledge is scattered across emails, conversations, and your memory. Weave brings it all together in a private, queryable social graph. Every fact carries provenance: who told you, when, and how confident you should be.

Skill packages follow the [agentskills.io](https://agentskills.io/specification) open standard and are compatible with OpenClaw, Hermes Agent, Claude, and any agentskills.io-compliant client.

## Quick Start

```
# Add a person
"I met Alex Chen at the conference last week, he's into robotics"

# Query the graph
"Who do I know in the robotics space?"

# Meeting prep
"What should I know about Sarah before our meeting?"

# Gift ideas
"What would be a good gift for Alex?"
```

Weave auto-initializes on first use. The graph never silently merges two person records and never writes back to external systems without explicit approval.

## What It Does

Weave maintains a private social graph where every stored fact carries provenance — source type, reference, timestamp, and confidence score. It supports meeting prep, gift ideas, hosting context, city connections, and serendipity discovery. The underlying database (LadybugDB) initializes automatically.

## Commands

| Command | Description |
|---|---|
| `weave.upsert.person` | Add or update a person record |
| `weave.upsert.relationship` | Add or update a Knows edge |
| `weave.upsert.preference` | Store a preference for a person |
| `weave.import.csv` | Bulk import contacts |
| `weave.query` | Query the graph (lookup, connection, serendipity, city, gift) |
| `weave.attach` | Query an external skill database read-only |
| `weave.export` | Export data to staging directory |
| `weave.sync.google-contacts` | Bidirectional sync with Google Contacts |
| `weave.writeback.contacts` | Push records to Google Contacts or Clay |
| `weave.init` | Diagnostic and repair |
| `weave.status` | Graph health and config state |
| `weave.journal` | Write journal |
| `weave.update` | Self-update |

## Dependencies

- [Elephas](https://github.com/indigokarasu/elephas) — Chronicle enrichment
- [Scout](https://github.com/indigokarasu/scout) — OSINT findings as upsert candidates
- LadybugDB (embedded graph database)
- Google Contacts, Clay (optional sync)

## Scheduled Tasks

| Job | Schedule | Command |
|---|---|---|
| `weave:update` | `0 0 * * *` | Self-update |

## Changelog

### v3.3.1 — April 26, 2026
- Removed stale migration scripts, cleaned SKILL.md references

### v2.6.0 — April 12, 2026
- Documented Google Contacts sync procedure and pitfalls

### v2.0.0 — March 18, 2026
- Initial release

---

*Weave is part of the [OCAS Agent Suite](https://github.com/indigokarasu).*