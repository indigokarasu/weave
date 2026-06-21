# Database Maintenance

Systematic inspection, cleaning, and data quality validation for the Weave LadybugDB database.

## When to Use

- Before running Weave ↔ Google Contacts synchronization
- When experiencing sync failures due to data quality issues
- Periodic database health maintenance (monthly)
- After importing large amounts of data containing inconsistencies
- After the overnight enrichment scraper has run (known corruption bug)

## LadybugDB Query Patterns

LadybugDB has important differences from Neo4j Cypher:

### Return Format
- **`RETURN p`** (whole node): each row is a **dict** with properties + `_ID` and `_LABEL`
- **`RETURN p.id, p.name, count(p)`** (column selectors): each row is a **list** (NOT dict)
- Always check with `r.get_column_names()` before accessing columns

### Row Iteration for Large Tables
```python
def safe_get_all(conn, query):
    r = conn.execute(query)
    cols = r.get_column_names()
    rows = []
    while True:
        try:
            row = r.get_next()
            rows.append(row)
        except Exception as e:
            if "No more tuples" in str(e):
                break
            if "utf-8" in str(e).lower():
                continue  # skip corrupt row
            raise
    r.close()
    return cols, rows
```

### Database Locking Issues
- `fuser -v /path/to/weave.lbug` shows which PID holds the lock
- After a killed process (SIGTERM/SIGKILL), orphan processes may hold the lock
- **Kill orphan**: `kill -9 <PID>` — repeat until `fuser` returns empty
- **Stale WAL**: `rm -f <hermes-root>/commons/db/ocas-weave/weave.lbug.wal` after killed processes

## Maintenance Operations

### 1. Database Inspection
```python
from real_ladybug import Database, Connection

db = Database("<hermes-root>/commons/db/ocas-weave/weave.lbug", read_only=True)
conn = Connection(db)

# Basic counts
for label, query in [
    ("person_count", "MATCH (p:Person) RETURN count(p)"),
    ("preference_count", "MATCH (p:Preference) RETURN count(p)"),
    ("fact_count", "MATCH (f:Fact) RETURN count(f)"),
    ("knows_count", "MATCH ()-[r:Knows]->() RETURN count(r)"),
]:
    cols, rows = safe_get_all(conn, query)
    print(f"{label}: {rows[0][0] if rows else 0}")

# Missing fields
for field in ["email", "phone", "org", "location_city"]:
    cols, rows = safe_get_all(conn, 
        f"MATCH (p:Person) WHERE p.{field} IS NULL OR p.{field} = '' RETURN count(p)")
    print(f"Missing {field}: {rows[0][0] if rows else '?'}")
```

### 2. Data Corruption Detection

The enrichment scraper has a known bug where it extracts substrings incorrectly:

#### Truncated Occupations
```python
# Check for occupations missing first characters
cols, rows = safe_get_all(conn, 
    "MATCH (p:Person) WHERE p.occupation IS NOT NULL AND p.occupation <> '' "
    "RETURN p.id, p.name, p.occupation, p.org")

import re
for r in rows:
    occ = r[2]
    if occ and (re.match(r'^[a-z]', occ) or len(occ) < 5 or len(occ) > 200):
        print(f"  SUSPICIOUS: {r[1]} occ='{occ[:80]}...'")
```

#### Fragment Organizations
```python
# Single-word org fragments that aren't real companies
suspicious_orgs = {
    "Senior", "North", "Spring", "Work", "Product", "Finance",
    "Serial", "Serving", "Greater", "Atlantic", "General", "Director"
}

cols, rows = safe_get_all(conn, 
    "MATCH (p:Person) WHERE p.org IS NOT NULL AND p.org <> '' "
    "RETURN p.id, p.name, p.org, p.occupation")

for r in rows:
    if r[2] and r[2] in suspicious_orgs:
        print(f"  FRAGMENT ORG: {r[1]} org='{r[2]}' occ='{r[3]}'")
```

### 3. Duplicate Detection

#### By Email (most reliable)
```python
cols, rows = safe_get_all(conn, """
    MATCH (p:Person) WHERE p.email IS NOT NULL AND p.email <> ''
    WITH p.email AS email, count(p) AS cnt,
         collect(p.id) AS ids, collect(p.name) AS names,
         collect(p.org) AS orgs
    WHERE cnt > 1
    RETURN email, cnt, ids, names, orgs
""")
print(f"Duplicate emails: {len(rows)}")
```

### 4. Orphan Detection

Use OPTIONAL MATCH instead of NOT EXISTS (unsupported in LadybugDB):

```python
# Orphan Preferences
cols, rows = safe_get_all(conn, """
    MATCH (pref:Preference)
    OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
    WITH pref, person WHERE person.id IS NULL
    RETURN pref.id, pref.category, pref.value
""")
print(f"Orphan preferences: {len(rows)}")

# Orphan Facts
cols, rows = safe_get_all(conn, """
    MATCH (f:Fact)
    OPTIONAL MATCH (person:Person)-[:HasFact]->(f)
    WITH f, person WHERE person.id IS NULL
    RETURN f.id, f.predicate, f.value
""")
print(f"Orphan facts: {len(rows)}")
```

### 5. Data Cleanup

```python
def execute(conn, query, params=None):
    r = conn.execute(query, params or {})
    try:
        while True:
            r.get_next()
    except Exception:
        pass
    r.close()

# Clear corrupted fields
execute(conn, "MATCH (p:Person {id: $id}) SET p.occupation = ''", {"id": person_id})
execute(conn, "MATCH (p:Person {id: $id}) SET p.org = ''", {"id": person_id})

# Delete null-name isolated records (check no relationships first)
execute(conn, "MATCH (p:Person {id: $id}) DETACH DELETE p", {"id": person_id})

# Delete orphan preferences/facts
execute(conn, "MATCH (p:Preference {id: $id}) DELETE p", {"id": pref_id})
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
# Verify cleanup success
for check, query in [
    ("Null names", "MATCH (p:Person) WHERE p.name IS NULL RETURN count(p)"),
    ("Orphan prefs", """
        MATCH (pref:Preference)
        OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
        WITH pref, person WHERE person.id IS NULL
        RETURN count(pref)
    """),
    ("Orphan facts", """
        MATCH (f:Fact)
        OPTIONAL MATCH (person:Person)-[:HasFact]->(f)
        WITH f, person WHERE person.id IS NULL
        RETURN count(f)
    """),
]:
    cols, rows = safe_get_all(conn, query)
    print(f"{check}: {rows[0][0] if rows else 0}")
```

## Safe Deletion Criteria

Only delete Person nodes when:
- Name is null AND
- No relationships exist (Knows, HasPreference, HasFact)
- Verified via relationship check

Only delete orphan Preferences/Facts when confirmed disconnected from any Person.