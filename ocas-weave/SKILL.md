---
name: ocas-weave
description: >
  Private provenance-backed social graph. Maintains queryable records of people,
  relationships, preferences, and experiences for recall, gifting, hosting,
  introductions, and serendipity.
---

# Weave

Weave maintains a private, queryable social graph with provenance for every material fact. It supports recall, gifting, hosting, introductions, travel context, and serendipity suggestions.

## When to use

- Store or update information about a person, relationship, or preference
- Query who a person is, how they relate to others, or what they like
- Prepare for a meeting or dinner with someone
- Find who we know in a given city
- Generate gift ideas grounded in known preferences
- Find serendipity connections between people based on stored evidence

## When not to use

- Web research without social graph need — use Sift
- OSINT investigations — use Scout
- CRM or sales pipeline automation
- Syncing contacts without explicit approval
- Personality profiling without evidence

## Core promise

Maintain and query a private social graph where every material fact has provenance. Queries are deterministic against the same graph state. No speculation presented as fact.

## Commands

- `weave.upsert.person` — create or update a person record with provenance
- `weave.upsert.relationship` — create or update a typed relationship between people
- `weave.upsert.preference` — store a provenance-backed preference, interest, or constraint
- `weave.query` — run a graph query (lookup, serendipity, or summarize mode)
- `weave.project.vcard` — generate a strict vCard 4.0 draft from a person record
- `weave.writeback.contacts` — optional gated writeback to external contacts (disabled by default)
- `weave.status` — graph health: people count, facts count, relationships count, writeback status

## Operating rules

- Every persisted fact requires provenance (source type, timestamp, evidence reference, confidence)
- Queries are deterministic: same query + same graph state = same result set
- Writeback is disabled by default. Enabling in config is necessary but not sufficient — each writeback also requires explicit user approval
- Minimize storage: store useful, durable, socially actionable facts. Avoid speculative inference about private identity categories.

## Support file map

- `references/schemas.md` — Person, Relationship, Fact, Experience, QueryRequest, VCardProjection
- `references/query_patterns.md` — deterministic query examples for common use cases
- `references/vcard_projection.md` — conservative vCard 4.0 projection rules

## Storage layout

```
.weave/
  config.json
  graph.jsonl
  facts.jsonl
  decisions.jsonl
  exports/
  reports/
```

## Validation rules

- Every material fact has provenance with source, timestamp, and confidence
- Writeback requires both config enablement and action-time approval
- vCard projection omits uncertain data rather than inventing fields
- Deterministic queries return consistent results
