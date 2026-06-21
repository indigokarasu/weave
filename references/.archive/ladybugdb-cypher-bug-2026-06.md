# LadybugDB Cypher Write Bug (2026-06)

## Bug Description

`MATCH (p:Person {id: $pid}) SET ...` sometimes matches the **wrong node** when combined with `SET`. The `$pid` parameter in curly-brace property matching does not reliably match by the `id` property — it may match by internal node ID instead.

## Reproduction

```python
from real_ladybug import Database, Connection

db = Database('/path/to/weave.lbug')
conn = Connection(db)

# This writes to the WRONG contact:
conn.execute(
    'MATCH (p:Person {id: $pid}) SET p.org = $org',
    {'pid': '02b39fe8-c6d3-5759-96ef-e9fdffa3ebc6', 'org': 'Salesforce'}
)
# Ido Mor (correct ID) remains unchanged
# Doordash (different contact) gets org='Salesforce'
```

Verified: after the write, `MATCH (p:Person) WHERE p.org = "Salesforce" RETURN count(p)` returned 1, but the wrong node was updated.

## Root Cause

LadybugDB's Cypher parser appears to use internal node IDs (not the `id` property) when matching with `{id: $pid}` in write queries. The behavior is inconsistent — read queries with `{id: $pid}` work correctly, but write queries (SET) do not.

## Workaround

**Use `{name: $name}` for write queries.** Name-based matching has been verified to correctly update only the target node:

```python
# CORRECT — verified to update only 1 node
conn.execute(
    'MATCH (p:Person {name: $name}) SET p.org = $org',
    {'name': 'Ido Mor', 'org': 'Salesforce'}
)
# Verification: MATCH (p:Person) WHERE p.org = "Salesforce" RETURN count(p) → 1
```

## Additional Cypher Quirks

- **RETURN after SET**: LadybugDB does not return rows from SET queries via `get_next()`. The write succeeds (no exception), but `get_next()` raises `Runtime exception: No more tuples`. Workaround: execute the write without RETURN, then run a separate read query.
- **Inequality operator**: Use `<>` not `!=`. `!=` causes `Parser exception: Unknown operation '!='`.
- **Parameterized queries**: `$param` syntax works for both reads and writes.

## Affected Versions

- `real_ladybug` 0.17.1 (Python 3.14 venv)
- LadybugDB extension 0.17.0

## Status

Workaround in place. The `ocas-weave` skill gotchas and enrichment pipeline docs have been updated.
