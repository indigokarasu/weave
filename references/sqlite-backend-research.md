# Storage Backend Research — SQLite Alternative

Research date: 2026-06-15

## Problem

LadybugDB (current backend) has a single-writer file-locking constraint. Only one `READ_WRITE` process at a time. Concurrent access from multiple cron jobs and interactive sessions causes failures.

## Recommendation: SQLite Adjacency Lists with WAL Mode

**Why:**
- WAL mode allows concurrent reads + single write (solves the locking problem)
- Zero new dependencies (stdlib `sqlite3`)
- Weave's scale (thousands of nodes) is well within SQLite recursive CTE performance
- Simple migration path from LadybugDB

## SQLite Schema

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

## Migration Notes

- Export LadybugDB nodes/edges → SQLite tables
- Replace `real_ladybug` calls with SQLite queries
- Use WAL mode: `PRAGMA journal_mode=WAL;`
- Keep existing Weave command interface unchanged
- Full research at `data/ocas-tasks/weave-storage-research.md`
