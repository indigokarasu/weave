# Database Maintenance

Systematic inspection, cleaning, and data quality validation for the Weave SQLite database.

## When to Use

- Before running Weave ↔ Google Contacts synchronization
- When experiencing sync failures due to data quality issues
- Periodic database health maintenance (monthly)
- After importing large amounts of data containing inconsistencies
- After the overnight enrichment pipeline has run

## Quick Health Check

```bash
cd <hermes-home>/commons/db/ocas-weave && sqlite3 weave.sqlite "
SELECT 'persons' as tbl, COUNT(*) FROM persons
UNION ALL SELECT 'edges', COUNT(*) FROM edges
UNION ALL SELECT 'facts', COUNT(*) FROM facts
UNION ALL SELECT 'preferences', COUNT(*) FROM preferences;"
```

## Maintenance Operations

All operations below use `weave_sqlite.WeaveDB`. Import: `from weave_sqlite import WeaveDB`

### 1. Database Inspection

```python
weave = WeaveDB()

# Basic counts
for table in ["persons", "edges", "facts", "preferences"]:
    rows = weave.execute(f"SELECT COUNT(*) as cnt FROM {table}")
    print(f"{table}: {rows[0]['cnt']}")

# Missing fields
for field in ["email", "phone", "org", "location_city"]:
    rows = weave.execute(
        f"SELECT COUNT(*) as cnt FROM persons WHERE {field} IS NULL OR {field} = ''"
    )
    print(f"Missing {field}: {rows[0]['cnt']}")
```

### 2. Data Corruption Detection

Common enrichment scraper issues:

```python
import re

# Check for truncated occupations (missing first characters)
rows = weave.execute(
    "SELECT id, name, occupation, org FROM persons WHERE occupation IS NOT NULL AND occupation != ''"
)
for r in rows:
    occ = r["occupation"]
    if occ and (re.match(r'^[a-z]', occ) or len(occ) < 5 or len(occ) > 200):
        print(f"  SUSPICIOUS: {r['name']} occ='{occ[:80]}...'")

# Check for fragment organizations
suspicious_orgs = {"Senior", "North", "Spring", "Work", "Product", "Finance",
                   "Serial", "Serving", "Greater", "Atlantic", "General", "Director"}
rows = weave.execute("SELECT id, name, org, occupation FROM persons WHERE org IS NOT NULL AND org != ''")
for r in rows:
    if r["org"] in suspicious_orgs:
        print(f"  FRAGMENT ORG: {r['name']} org='{r['org']}' occ='{r['occupation']}'")

# Check for job titles in city field
rows = weave.execute("SELECT id, name, location_city FROM persons WHERE location_city IS NOT NULL AND location_city != ''")
for r in rows:
    if re.search(r'(Executive|Manager|Director|Engineer|VP|President|Chief)', r["location_city"]):
        print(f"  TITLE IN CITY: {r['name']} city='{r['location_city']}'")
```

### 3. Duplicate Detection

```python
# By email (most reliable)
rows = weave.execute("""
    SELECT email, COUNT(*) as cnt, GROUP_CONCAT(name, ', ') as names
    FROM persons WHERE email IS NOT NULL AND email != ''
    GROUP BY email HAVING cnt > 1
""")
for r in rows:
    print(f"  DUPE EMAIL: {r['email']} ({r['cnt']}) - {r['names']}")

# By name (fuzzy — review manually)
rows = weave.execute("""
    SELECT name, COUNT(*) as cnt, GROUP_CONCAT(id, ', ') as ids
    FROM persons WHERE name IS NOT NULL AND name != ''
    GROUP BY name HAVING cnt > 1
""")
for r in rows:
    print(f"  DUPE NAME: {r['name']} ({r['cnt']}) - ids: {r['ids']}")
```

### 4. Orphan Detection

```python
# Orphan preferences (no HasPreference edge)
rows = weave.execute("""
    SELECT p.id, p.category, p.value FROM preferences p
    LEFT JOIN edges e ON e.target_id = p.id AND e.rel_type = 'HasPreference'
    WHERE e.id IS NULL
""")
print(f"Orphan preferences: {len(rows)}")

# Orphan facts (no HasFact edge)
rows = weave.execute("""
    SELECT f.id, f.predicate, f.value FROM facts f
    LEFT JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
    WHERE e.id IS NULL
""")
print(f"Orphan facts: {len(rows)}")

# Persons with no relationships at all
rows = weave.execute("""
    SELECT p.id, p.name FROM persons p
    LEFT JOIN edges e ON e.source_id = p.id
    WHERE e.id IS NULL
""")
print(f"Isolated persons: {len(rows)}")
```

### 5. Data Cleanup

```python
# Clear corrupted fields
weave.execute_write("UPDATE persons SET occupation = '' WHERE id = :id", {"id": person_id})
weave.execute_write("UPDATE persons SET org = '' WHERE id = :id", {"id": person_id})
weave.execute_write("UPDATE persons SET location_city = '' WHERE id = :id", {"id": person_id})

# Delete null-name isolated records (check no relationships first)
rows = weave.execute("""
    SELECT p.id FROM persons p
    LEFT JOIN edges e ON e.source_id = p.id OR e.target_id = p.id
    WHERE (p.name IS NULL OR p.name = '') AND e.id IS NULL
""")
for r in rows:
    weave.execute_write("DELETE FROM persons WHERE id = :id", {"id": r["id"]})

# Delete orphan preferences
weave.execute("""
    DELETE FROM preferences WHERE id IN (
        SELECT p.id FROM preferences p
        LEFT JOIN edges e ON e.target_id = p.id AND e.rel_type = 'HasPreference'
        WHERE e.id IS NULL
    )
""")

# Delete orphan facts
weave.execute("""
    DELETE FROM facts WHERE id IN (
        SELECT f.id FROM facts f
        LEFT JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        WHERE e.id IS NULL
    )
""")
```

## Known Corruption Patterns

| Pattern | Example | Fix |
|---------|---------|-----|
| Occupation missing first chars | `'r Vice President Product'` | Clear field |
| LinkedIn text in occupation | `'LinkedIn Excited to start'` | Clear field |
| Fragment org | `'Greater'`, `'Atlantic'`, `'Senior'` | Clear org |
| Job title in org field | `org='Director'`, `org='CMO'` | Clear org |
| Name in city field | `'Heather Scoville Ladora, IA'` | Clear city |

## Validation After Cleanup

```python
checks = {
    "Null names": "SELECT COUNT(*) FROM persons WHERE name IS NULL OR name = ''",
    "Orphan prefs": """
        SELECT COUNT(*) FROM preferences p
        LEFT JOIN edges e ON e.target_id = p.id AND e.rel_type = 'HasPreference'
        WHERE e.id IS NULL
    """,
    "Orphan facts": """
        SELECT COUNT(*) FROM facts f
        LEFT JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        WHERE e.id IS NULL
    """,
}
for label, query in checks.items():
    rows = weave.execute(query)
    print(f"{label}: {rows[0][0] if rows else 0}")
```

## Safe Deletion Criteria

Only delete Person nodes when:
- Name is null AND
- No relationships exist (Knows, HasPreference, HasFact — check both source and target)
- Verified via relationship check

Only delete orphan Preferences/Facts when confirmed disconnected from any Person.

## Backup

```bash
# Simple file copy (WAL mode — copy all three files)
cp <hermes-home>/commons/db/ocas-weave/weave.sqlite{,-wal,-shm} /backup/path/

# Or use SQLite's backup API
sqlite3 <hermes-home>/commons/db/ocas-weave/weave.sqlite ".backup /backup/path/weave.sqlite"
```