# Query Patterns

SQL templates for all `weave.query` modes. Uses SQLite with WAL mode.
Parameters use `:name` syntax (sqlite3 named parameters).

## lookup

By ID:
```sql
SELECT p.*,
  (SELECT GROUP_CONCAT(e.rel_type || ':' || other.name, '; ')
   FROM edges e JOIN persons other ON other.id = e.target_id
   WHERE e.source_id = :person_id AND e.rel_type = 'Knows') AS relationships,
  (SELECT GROUP_CONCAT(pref.category || ':' || pref.value, '; ')
   FROM edges ep JOIN preferences pref ON pref.id = ep.target_id
   WHERE ep.source_id = :person_id AND ep.rel_type = 'HasPreference') AS preferences
FROM persons p
WHERE p.id = :person_id
```

By name (fuzzy):
```sql
SELECT id, name, org, location_city
FROM persons
WHERE name LIKE :name_query
ORDER BY name LIMIT 10
```

## connection

Shortest path (bounded depth recursive CTE):
```sql
WITH RECURSIVE path(target_id, depth, path_ids) AS (
    SELECT target_id, 1, source_id || ',' || target_id
    FROM edges WHERE source_id = :from_id AND rel_type = 'Knows'
    UNION ALL
    SELECT e.target_id, p.depth + 1, p.path_ids || ',' || e.target_id
    FROM edges e
    JOIN path p ON p.target_id = e.source_id
    WHERE e.rel_type = 'Knows' AND p.depth < 4
      AND ',' || p.path_ids || ',' NOT LIKE '%,' || e.target_id || ',%'
)
SELECT DISTINCT n.name, MIN(p.depth) AS distance
FROM path p
JOIN persons n ON n.id = p.target_id
WHERE p.target_id = :to_id
GROUP BY n.name
```

## serendipity

Shared preferences:
```sql
SELECT pa.category, pa.value
FROM edges ea
JOIN preferences pa ON pa.id = ea.target_id
JOIN preferences pb ON pb.category = pa.category AND pb.value = pa.value
JOIN edges eb ON eb.target_id = pb.id
WHERE ea.source_id = :person_a_id AND ea.rel_type = 'HasPreference'
  AND eb.source_id = :person_b_id AND eb.rel_type = 'HasPreference'
  AND pa.valence = 'like'
```

Mutual connections:
```sql
SELECT DISTINCT n.name, n.id, n.org
FROM edges e1
JOIN edges e2 ON e2.source_id = e1.target_id
JOIN persons n ON n.id = e1.target_id
WHERE e1.source_id = :person_a_id AND e2.target_id = :person_b_id
  AND e1.rel_type = 'Knows' AND e2.rel_type = 'Knows'
  AND e1.target_id != :person_b_id
```

## city

```sql
SELECT name, org, occupation, id
FROM persons
WHERE location_city LIKE :city
ORDER BY name
```

## summarize

```sql
SELECT
  p.name AS name, p.org AS org, p.occupation AS role, p.location_city AS city,
  (SELECT GROUP_CONCAT(e.rel_type || ':' || other.name, '; ')
   FROM edges e JOIN persons other ON other.id = e.target_id
   WHERE e.source_id = :person_id AND e.rel_type = 'Knows') AS relationships,
  (SELECT GROUP_CONCAT(pref.category || ':' || pref.valence, '; ')
   FROM edges ep JOIN preferences pref ON pref.id = ep.target_id
   WHERE ep.source_id = :person_id AND ep.rel_type = 'HasPreference'
     AND pref.confidence >= 0.6) AS preferences
FROM persons p
WHERE p.id = :person_id
```

Present as: who they are, how you know them, what to know (like), what to avoid (dislike/constraint).

## gift

```sql
SELECT category, value, confidence
FROM preferences p
JOIN edges e ON e.target_id = p.id
WHERE e.source_id = :person_id AND e.rel_type = 'HasPreference'
  AND valence = 'like' AND confidence >= 0.5
  AND category IN ('gift', 'food', 'interest', 'travel')
ORDER BY confidence DESC
```

Never fabricate preferences not in the graph.

## sync diff

Modified since last sync:
```sql
SELECT id, name, email, phone, org, record_time
FROM persons
WHERE record_time > :last_sync_at
ORDER BY record_time DESC
```

Not yet synced to Google:
```sql
SELECT id, name, email
FROM persons
WHERE google_resource_name IS NULL OR google_resource_name = ''
```

Not yet synced to Clay:
```sql
SELECT id, name, email
FROM persons
WHERE clay_id IS NULL OR clay_id = ''
```

## weave.status

```sql
SELECT
  (SELECT count(*) FROM persons) AS people,
  (SELECT count(*) FROM edges WHERE rel_type = 'Knows') AS relationships,
  (SELECT count(*) FROM preferences) AS preferences,
  (SELECT count(*) FROM facts) AS facts;
```

## Error handling

Person not found by ID — halt. Report: "No Person with id={id}."
Person not found by name, 0 results — report: "No match for '{name}'. Use weave.upsert.person."
Person not found by name, 2+ results — return candidate list, ask which to use.
Lock error — no longer applicable (SQLite WAL mode handles concurrency).
Write read-back returns no row — report: "Write may have failed — read-back returned no result."

## Upsert pattern

```sql
-- Insert or update (gap-fill only)
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

After every write, immediately query by primary key:
```sql
-- Write
UPDATE persons SET org = :org, record_time = :now WHERE id = :id;

-- Read-back (required)
SELECT id, name, org FROM persons WHERE id = :id;
```
