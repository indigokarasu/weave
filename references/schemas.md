# Schemas

SQLite schema for `weave.sqlite`. The database auto-initializes on first command via `WeaveDB.__init__()`. WAL mode allows concurrent reads + single write.

## Python usage

```python
import sys
sys.path.insert(0, "{skill_root}/scripts")
from weave_sqlite import WeaveDB

weave = WeaveDB()  # auto-inits schema, WAL mode

# Query
rows = weave.execute("SELECT * FROM persons WHERE name LIKE :name", {"name": "%Smith%"})
for row in rows:
    print(row["name"], row["org"])

# Upsert person (gap-fill: only fills empty fields)
weave.upsert_person({
    "id": "uuid-or-auto",
    "name": "Jane Doe",
    "email": "jane@example.com",
    "source_type": "direct",
    "source_ref": "user-stated",
    "confidence": 1.0,
})

# Direct write
weave.execute_write(
    "INSERT INTO persons (id, name, source_type, source_ref, confidence, record_time) VALUES (:id, :name, :st, :sr, :c, :now)",
    {"id": "...", "name": "...", "st": "imported", "sr": "", "c": 0.8, "now": "2026-..."},
)

# Read-back verification (required after every write)
result = weave.execute("SELECT id, name FROM persons WHERE id = :id", {"id": "..."})
assert result[0]["name"] == "Jane Doe"
```

## DDL

```sql
CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_given TEXT, name_family TEXT,
    email TEXT, phone TEXT, location_city TEXT, location_country TEXT,
    occupation TEXT, org TEXT, google_resource_name TEXT, clay_id TEXT,
    source_type TEXT NOT NULL DEFAULT 'imported', source_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.8, event_time TEXT,
    record_time TEXT NOT NULL DEFAULT (datetime('now')),
    valid_from TEXT, valid_until TEXT
);
CREATE TABLE IF NOT EXISTS preferences (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, value TEXT NOT NULL,
    valence TEXT NOT NULL DEFAULT 'like', confidence REAL NOT NULL DEFAULT 0.8,
    source_type TEXT NOT NULL DEFAULT 'imported', source_ref TEXT NOT NULL DEFAULT '',
    record_time TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY, predicate TEXT NOT NULL, value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.8, source_type TEXT NOT NULL DEFAULT 'imported',
    source_ref TEXT NOT NULL DEFAULT '', record_time TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
    rel_type TEXT NOT NULL DEFAULT 'Knows', strength REAL, since TEXT, context TEXT,
    source_ref TEXT, confidence REAL,
    record_time TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES persons(id)
);
CREATE INDEX IF NOT EXISTS idx_persons_name ON persons(name);
CREATE INDEX IF NOT EXISTS idx_persons_email ON persons(email);
CREATE INDEX IF NOT EXISTS idx_persons_grn ON persons(google_resource_name);
CREATE INDEX IF NOT EXISTS idx_persons_clay ON persons(clay_id);
CREATE INDEX IF NOT EXISTS idx_persons_record_time ON persons(record_time);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(rel_type);
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate);
CREATE INDEX IF NOT EXISTS idx_preferences_category ON preferences(category);
```

**Note on `edges.target_id` FK**: `target_id` is a polymorphic reference — it can point to `persons.id` (for `Knows` edges), `facts.id` (for `HasFact` edges), or `preferences.id` (for `HasPreference` edges). Only `source_id` has a FK constraint to `persons(id)`. Do NOT add `FOREIGN KEY (target_id) REFERENCES persons(id)` — it breaks HasFact and HasPreference edges.

## Person fields

| Field | Type | Notes |
|-------|------|-------|
| id | TEXT PK | UUID. Merge key. Never merge on name alone. |
| name | TEXT NOT NULL | Canonical display name. |
| name_given / name_family | TEXT | First / last name. |
| email | TEXT | Primary email. |
| phone | TEXT | E.164 preferred. |
| location_city / location_country | TEXT | ISO 3166-1 alpha-2 for country. |
| occupation / org | TEXT | Job title / organization. |
| google_resource_name | TEXT | Google Contacts foreign key. |
| clay_id | TEXT | Clay foreign key. |
| source_type | TEXT NOT NULL | direct / inferred / imported / user-stated. |
| source_ref | TEXT NOT NULL | Provenance reference. |
| confidence | REAL NOT NULL | 0.0–1.0. |
| event_time | TEXT | ISO 8601. When the real-world observation occurred. |
| record_time | TEXT NOT NULL | ISO 8601. When Weave wrote this record. |
| valid_from / valid_until | TEXT | ISO 8601. Validity window. |

## Preference fields

| Field | Type | Notes |
|-------|------|-------|
| id | TEXT PK | UUID per record. |
| category | TEXT NOT NULL | food / gift / travel / interest / constraint / other. |
| value | TEXT NOT NULL | Free text. |
| valence | TEXT NOT NULL | like / dislike / constraint. |
| confidence | REAL | 0.0–1.0. |
| source_type / source_ref / record_time | TEXT NOT NULL | Provenance. |

Each preference is a distinct INSERT — never MERGE on value.

## Edge fields

| Field | Type | Notes |
|-------|------|-------|
| id | TEXT PK | UUID. |
| source_id | TEXT NOT NULL FK→persons | Always a person. |
| target_id | TEXT NOT NULL | Polymorphic: person, fact, or preference depending on rel_type. |
| rel_type | TEXT NOT NULL | Knows / HasFact / HasPreference. |
| strength | REAL | 0.0–1.0. Knows only. |
| since | TEXT | ISO 8601 date. |
| context | TEXT | e.g. 'spouse' for Knows edges. |
| source_ref / record_time | TEXT | Provenance. |
| confidence | REAL | 0.0–1.0. |

## Fact fields

| Field | Type | Notes |
|-------|------|-------|
| id | TEXT PK | UUID. |
| predicate | TEXT NOT NULL | Field name: org, occupation, location_city, email, phone, etc. |
| value | TEXT NOT NULL | The extracted value. |
| confidence | REAL | 0.0–1.0. |
| source_type / source_ref / record_time | TEXT NOT NULL | Provenance. |

## Upsert pattern

```sql
-- Insert or gap-fill (only fills empty/null fields)
INSERT INTO persons (id, name, source_type, source_ref, confidence, record_time)
VALUES (:id, :name, :source_type, :source_ref, :confidence, :now)
ON CONFLICT(id) DO UPDATE SET
    name = COALESCE(NULLIF(name, ''), excluded.name),
    email = COALESCE(NULLIF(email, ''), excluded.email),
    phone = COALESCE(NULLIF(phone, ''), excluded.phone),
    org = COALESCE(NULLIF(org, ''), excluded.org),
    record_time = excluded.record_time;
```

## Write pattern (always read back)

```sql
-- Write
UPDATE persons SET org = :org, record_time = :now WHERE id = :id;

-- Read-back (required)
SELECT id, name, org FROM persons WHERE id = :id;
```

## Enrichment write pattern (agent-driven)

For writing enrichment data gathered from web searches, the `facts` table has **no `person_id` column**. Linkage is via `edges`:

```python
# 1. UPDATE persons (gap-fill)
db.execute_write("UPDATE persons SET occupation = COALESCE(?, occupation), org = COALESCE(?, org) WHERE id = ?", (occ, org, pid))
# 2. INSERT fact
db.execute_write("INSERT INTO facts (id, predicate, value, source_type, source_ref, confidence, record_time) VALUES (?, 'enrichment', ?, ?, ?, ?, ?)", (fact_id, payload_json, st, sr, conf, now))
# 3. INSERT edge
db.execute_write("INSERT INTO edges (id, source_id, target_id, rel_type, source_ref, confidence, record_time) VALUES (?, ?, ?, 'HasFact', ?, ?, ?)", (edge_id, pid, fact_id, sr, conf, now))
```

See `references/enrichment-write-pattern.md` for the full pattern with batch processing.

## Storage Layout

```
{agent_root}/commons/db/ocas-weave/
  weave.sqlite       — SQLite database (auto-created on first use)
  weave.sqlite-wal   — WAL file (auto-created)
  weave.sqlite-shm   — Shared memory file (auto-created)
  config.json        — connector and sync configuration
  staging/           — temporary import/export files
  snapshots/         — contact sync snapshots

{agent_root}/commons/data/ocas-weave/
  config.json        — connector and sync configuration
```

## CSV import column map

For bulk import via `weave.bulk_import("persons", rows)`. Column names match field names exactly.

Required: id (auto-generated UUID if absent), name, source_type (default: imported), source_ref (default: filename), confidence (default: 0.8), record_time (default: now)
Optional: name_given, name_family, email, phone, location_city, location_country, occupation, org, google_resource_name, clay_id, event_time, valid_from, valid_until