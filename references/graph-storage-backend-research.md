# Graph Storage Backend Research

**Date:** 2026-06-15
**Status:** Research complete, migration not started
**Trigger:** Evaluate alternatives to LadybugDB for Weave's social graph storage

## Problem

LadybugDB (`real_ladybug` Python package) is an embedded single-file graph database with a Cypher-like query language. Critical constraint: **only one `READ_WRITE` process at a time**. If another process holds the lock, operations fail immediately. This is a problem because Weave operations run from multiple cron jobs and interactive sessions concurrently.

## Evaluated Alternatives

### 1. SQLite Adjacency Lists ✅ RECOMMENDED

Store edges in a table `(source_id, target_id, relationship_type, properties_json)`. Use recursive CTEs for graph traversal.

**Pros:**
- Zero new dependencies (stdlib `sqlite3`)
- WAL mode allows concurrent reads while writing
- Battle-tested, no file-locking issues with proper WAL configuration
- Can add indexes on `source_id`, `target_id` for fast lookups
- Simple backup (single file copy)

**Cons:**
- Recursive CTEs for multi-hop queries are O(n^h) where h = hop depth
- No native Cypher support — all queries become SQL
- Path-finding, shortest-path, and cycle detection require manual SQL
- Performance degrades on graphs >100K edges with deep traversals

**Verdict:** Best fit for Weave's scale. Weave has ~thousands of people/relationships, not millions. Recursive CTEs with proper indexing will be fast enough. WAL mode solves the file-locking problem entirely.

### 2. DuckDB + DuckPGQ Extension

DuckDB with the DuckPGQ community extension implements SQL/PGQ (property graph queries from SQL:2023 standard).

**Pros:** SQL/PGQ `MATCH` syntax closer to Cypher; excellent analytical performance; columnar storage good for aggregations
**Cons:** Community extension (not officially supported); still maturing; significant dependency; overkill for Weave's scale; still has single-writer limitations
**Verdict:** Interesting but overkill.

### 3. Python NetworkX + Serialization

In-memory graph with `networkx.Graph()`, serialize to disk via pickle/JSON/GraphML.

**Pros:** Rich graph algorithms (shortest path, centrality, community detection); pure Python; no database file-locking
**Cons:** Entire graph must fit in memory; pickle serialization fragile across Python versions; JSON serialization 25x slower; no concurrent access; no query language
**Verdict:** Not suitable for persistent storage. Could be useful as an in-memory cache layer.

### 4. Kùzu (Embedded Graph Database)

C++ embedded graph database with Cypher support.

**Pros:** Native Cypher; embedded; good performance on large graphs; property graph model
**Cons:** Newer project; C++ dependency; single-writer limitations (same as LadybugDB); doesn't solve the core file-locking problem
**Verdict:** Good Cypher support but lateral move from LadybugDB.

### 5. ArcadeDB / Memgraph / FalkorDB (Server-based)

Full graph databases running as server processes.

**Pros:** Excellent performance, full Cypher support, concurrent access
**Cons:** Require running a server process; significant operational overhead; overkill for a personal agent's social graph
**Verdict:** Overkill for Weave's use case.

## Recommendation: SQLite Adjacency Lists with WAL Mode

1. **Solves the file-locking problem** — WAL mode allows concurrent reads + single write
2. **Zero new dependencies** — `sqlite3` is in stdlib
3. **Weave's scale is small** — thousands of nodes/edges, not millions
4. **Simple migration path** — export LadybugDB nodes/edges → SQLite tables
5. **Proven pattern** — this is how many production graph systems work at moderate scale

## SQLite Schema Draft

```sql
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT 'Person',
    name TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS node_properties (
    node_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (node_id, key),
    FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rel_type TEXT NOT NULL DEFAULT 'Knows',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES nodes(id),
    FOREIGN KEY (target_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS edge_properties (
    edge_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (edge_id, key),
    FOREIGN KEY (edge_id) REFERENCES edges(id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(rel_type);
```

## Example Queries

```sql
-- Find all people someone knows (1 hop)
SELECT n2.name, e.rel_type
FROM edges e
JOIN nodes n2 ON n2.id = e.target_id
WHERE e.source_id = ?;

-- Find connections of connections (2 hop)
SELECT DISTINCT n3.name
FROM edges e1
JOIN edges e2 ON e2.source_id = e1.target_id
JOIN nodes n3 ON n3.id = e2.target_id
WHERE e1.source_id = ? AND e2.target_id != ?;

-- Shortest path (bounded depth recursive CTE)
WITH RECURSIVE path(target_id, depth) AS (
    SELECT target_id, 1
    FROM edges WHERE source_id = ?
    UNION ALL
    SELECT e.target_id, p.depth + 1
    FROM edges e
    JOIN path p ON p.target_id = e.source_id
    WHERE p.depth < 4
)
SELECT DISTINCT n.name, MIN(p.depth) as distance
FROM path p
JOIN nodes n ON n.id = p.target_id
WHERE p.target_id = ?
GROUP BY n.name;
```

## Migration Plan

1. Create SQLite schema: `nodes` table, `edges` table, `properties` table
2. Export existing LadybugDB data to SQLite
3. Replace `real_ladybug` calls with SQLite queries
4. Use WAL mode + proper connection management for concurrent access
5. Keep the existing Weave command interface unchanged
