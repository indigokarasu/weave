---
name: weave-db-maintenance
description: Perform systematic inspection, cleaning, and preparation of the Weave (LadybugDB) database to ensure data integrity before synchronization with Google Contacts or other systems.
category: ocas-weave
---

# Weave Database Maintenance Skill

## Purpose
Perform systematic inspection, cleaning, and preparation of the Weave (LadybugDB) database to ensure data integrity before synchronization with Google Contacts or other systems.

## When to Use
- Before running Weave ↔ Google Contacts synchronization
- When experiencing sync failures due to data quality issues
- Periodic database health maintenance (monthly)
- After importing large amounts of data containing inconsistencies
- After the overnight enrichment scraper has run (known corruption bug)

## LadybugDB Query Quirks (Critical)

LadybugDB (embedded Cypher) has important differences from Neo4j Cypher:

### Return Format
- **`RETURN p`** (whole node): each row is a **dict** with properties + `_ID` and `_LABEL`
- **`RETURN p.id, p.name, count(p)`** (column selectors): each row is a **list** (NOT dict)
- Always check with `r.get_column_names()` before accessing columns
- Access list rows by index: `row[cols.index('p.id')]` or hardcode `row[0]`

### Unsupported Features
| Feature | Workaround |
|---------|------------|
| `NOT EXISTS(...)` subquery | Use `OPTIONAL MATCH ... WHERE ... IS NULL` |
| `type()` function | Not available — skip `type(r)` queries |
| `randomUUID()` | Generate UUIDs in Python via `uuid.uuid4()` |
| `CREATE INDEX IF NOT EXISTS` | Not supported — PKs are auto-indexed |
| `EXISTS()` in WHERE | Not supported for relationship checks |

### Row Iteration Pattern (large tables)
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

`r.get_next()` raises `Runtime exception: No more tuples in QueryResult` when exhausted — NOT `StopIteration`.

### Database Locking Issues
- `fuser -v /path/to/weave.lbug` shows which PID holds the lock
- After a killed process (SIGTERM/SIGKILL), orphan processes may hold the lock
- **Kill orphan**: `kill -9 <PID>` — repeat until `fuser` returns empty
- **Stale WAL**: `rm -f /root/.hermes/commons/db/ocas-weave/weave.lbug.wal` after killed processes
- The `.wal` file from a killed process is always stale; removing it is safe

## Person Properties (for reference in queries)
```
id, name, name_given, name_family, email, phone,
location_city, location_country, occupation, org, notes,
google_resource_name, clay_id,
source_type, source_ref, confidence,
event_time, record_time, valid_from, valid_until
```

No `city`, `location_region`, or `company` properties — use exact names above.

## Steps

### 1. Database Inspection (via execute_code Python)

```python
from real_ladybug import Database, Connection
import json

db = Database("/root/.hermes/commons/db/ocas-weave/weave.lbug", read_only=True)
conn = Connection(db)

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
                continue
            raise
    r.close()
    return cols, rows
```

#### Basic Counts
```python
# Use column-selector pattern: count() returns list
for label, query in [
    ("person_count", "MATCH (p:Person) RETURN count(p)"),
    ("preference_count", "MATCH (p:Preference) RETURN count(p)"),
    ("fact_count", "MATCH (f:Fact) RETURN count(f)"),
    ("knows_count", "MATCH ()-[r:Knows]->() RETURN count(r)"),
    ("haspref_count", "MATCH ()-[r:HasPreference]->() RETURN count(r)"),
    ("hasfact_count", "MATCH ()-[r:HasFact]->() RETURN count(r)"),
]:
    cols, rows = safe_get_all(conn, query)
    print(f"{label}: {rows[0][0] if rows else 0}")
```

#### Null/Empty Field Detection
```python
# Null names
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.name IS NULL RETURN p.id, p.occupation, p.org, p.email")

# Null occupations
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NULL OR p.occupation = '' RETURN p.id, p.name, p.org")

# Null orgs
r = conn.execute("MATCH (p:Person) WHERE p.org IS NULL OR p.org = '' RETURN count(p)")
print(f"People missing org: {r.get_next()[0]}")  # list row!

# Missing field summary
for field in ["email", "phone", "org", "location_city"]:
    cols, rows = safe_get_all(conn, f"MATCH (p:Person) WHERE p.{field} IS NULL OR p.{field} = '' RETURN count(p)")
    print(f"Missing {field}: {rows[0][0] if rows else '?'}")
```

### 2. Detect Enrichment Scraper Corruption

The overnight enrichment scraper has a known bug: it extracts substrings incorrectly, storing truncated/garbled text in occupation, org, and location_city. Run these checks after any enrichment cycle.

#### Checks for Truncated Occupations (missing first chars)
```python
# Occupations that start with lowercase or mid-word — check in Python
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NOT NULL AND p.occupation <> '' RETURN p.id, p.name, p.occupation, p.org")
import re
for r in rows:
    occ = r[2]
    if occ and (re.match(r'^[a-z]', occ) or len(occ) < 5 or len(occ) > 200):
        print(f"  SUSPICIOUS: {r[1]} occ='{occ[:80]}...'")
```

#### Checks for Fragment Orgs (enrichment got only first word)
```python
# Suspicious single-word orgs that aren't real company names
suspicious_orgs = {
    "Senior", "North", "Spring", "Work", "Product", "Finance",
    "Serial", "Serving", "Greater", "Atlantic", "Alameda", "Dedham",
    "Colorado", "Keystone", "Laurentian", "General", "Director",
    "Lead", "Executive", "US", "CMO"
}
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.org IS NOT NULL AND p.org <> '' RETURN p.id, p.name, p.org, p.occupation")
for r in rows:
    if r[2] and r[2] in suspicious_orgs:
        print(f"  FRAGMENT ORG: {r[1]} org='{r[2]}' occ='{r[3]}'")
```

#### Checks for Occupation-as-Org (job title in org field)
```python
occupation_keywords = {"Senior", "Director", "Partner", "Lead", "Chief",
                       "Head", "Principal", "Staff", "VP", "SVP", "EVP",
                       "CMO", "CFO", "CTO", "CEO", "COO", "Managing",
                       "Founder", "Co-Founder", "President", "Executive"}
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.org IS NOT NULL AND p.org <> '' RETURN p.id, p.name, p.org, p.occupation")
for r in rows:
    first = r[2].split()[0] if r[2] else ""
    if first in occupation_keywords:
        print(f"  OCC_IN_ORG: {r[1]} org='{r[2]}' occ='{r[3]}'")
```

#### Checks for Name in Occupation or City field
```python
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NOT NULL OR p.location_city IS NOT NULL RETURN p.id, p.name, p.occupation, p.location_city, p.org")
for r in rows:
    name = (r[1] or "").lower()
    occ = (r[2] or "").lower()
    city = (r[3] or "").lower()
    # Occupation contains the person's own first name
    name_parts = name.split()
    if occ and name_parts and name_parts[0] in occ:
        print(f"  NAME_IN_OCC: {r[1]} occ='{r[2]}'")
    # City contains name (e.g. "Heather Scoville Ladora, IA")
    if city and name_parts and (name_parts[0] in city or (len(name_parts) > 1 and name_parts[-1] in city)):
        print(f"  NAME_IN_CITY: {r[1]} city='{r[3]}'")
```

#### Checks for Bios/LinkedIn Text in Occupation
```python
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.occupation IS NOT NULL AND p.occupation <> '' RETURN p.id, p.name, p.occupation")
for r in rows:
    occ = r[2]
    if occ and len(occ) > 80:
        print(f"  LONG_OCC: {r[1]} len={len(occ)} '{occ[:80]}...'")
    # LinkedIn post text commonly starts with "LinkedIn"
    if occ and occ.lower().startswith("linkedin"):
        print(f"  LINKEDIN_TEXT: {r[1]} occ='{occ[:80]}...'")
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
for r in rows[:5]:
    for i in range(r[1]):
        print(f"  {r[0]}: id={r[2][i]} name={r[3][i]} org={r[4][i]}")
```

#### By Name (same name, different IDs — complementary)
```python
cols, rows = safe_get_all(conn, """
    MATCH (p:Person) WHERE p.name IS NOT NULL AND p.name <> ''
    WITH p.name AS name, count(p) AS cnt,
         collect(p.id) AS ids, collect(p.org) AS orgs, collect(p.email) AS emails
    WHERE cnt > 1
    RETURN name, cnt, ids, orgs, emails ORDER BY cnt DESC
""")
```

### 4. Orphan Detection (nodes with no person edges)

Use OPTIONAL MATCH instead of NOT EXISTS (unsupported in LadybugDB):

```python
# Orphan Preferences
cols, rows = safe_get_all(conn, """
    MATCH (pref:Preference)
    OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
    WITH pref, person WHERE person.id IS NULL
    RETURN pref.id, pref.category, pref.value
""")

# Orphan Facts
cols, rows = safe_get_all(conn, """
    MATCH (f:Fact)
    OPTIONAL MATCH (person:Person)-[:HasFact]->(f)
    WITH f, person WHERE person.id IS NULL
    RETURN f.id, f.predicate, f.value
""")
```

### 5. Data Cleanup Operations

#### Clear Corrupted Fields
```python
def execute(conn, query, params=None):
    r = conn.execute(query, params or {})
    try:
        while True:
            r.get_next()
    except Exception:
        pass
    r.close()

# Clear truncated occupations
for pid, field in [(JARED_UUID, "occupation")]:
    execute(conn, f"MATCH (p:Person {{id: $id}}) SET p.{field} = ''", {"id": pid})

# Clear fragment orgs
for pid in FRAGMENT_IDS:
    execute(conn, f"MATCH (p:Person {{id: $id}}) SET p.org = ''", {"id": pid})

# Clear garbage city field
for pid in GARBAGE_CITY_IDS:
    execute(conn, f"MATCH (p:Person {{id: $id}}) SET p.location_city = ''", {"id": pid})
```

#### Delete Null-Name Isolated Records
```python
# First check they have no relationships
for pid in null_name_ids:
    cols, rows = safe_get_all(conn, f"""
        OPTIONAL MATCH (p:Person {{id: '{pid}'}})-[r:Knows]->()
        OPTIONAL MATCH (p)-[r2:HasPreference]->()
        OPTIONAL MATCH (p)-[r3:HasFact]->()
        RETURN count(r) + count(r2) + count(r3)
    """)
    if rows and rows[0][0] == 0:
        execute(conn, f"MATCH (p:Person {{id: '{pid}'}}) DETACH DELETE p")
```

#### Delete Orphan Preferences/Facts
```python
# Delete orphan preferences
r = conn.execute("""
    MATCH (pref:Preference)
    OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
    WITH pref, person WHERE person.id IS NULL
    RETURN pref.id
""")
orphan_ids = []
while True:
    try:
        row = r.get_next()
        orphan_ids.append(row[0])
    except Exception as e:
        if "No more tuples" in str(e):
            break
        raise
r.close()

for pid in orphan_ids:
    execute(conn, f"MATCH (p:Preference {{id: '{pid}'}}) DELETE p")

# Same pattern for orphan Facts
```

### 6. Validation After Cleanup

```python
# Null names: should be 0 after cleanup
cols, rows = safe_get_all(conn, "MATCH (p:Person) WHERE p.name IS NULL RETURN count(p)")
print(f"Null names: {rows[0][0] if rows else 0}")

# Orphan preferences: should be 0
cols, rows = safe_get_all(conn, """
    MATCH (pref:Preference)
    OPTIONAL MATCH (person:Person)-[:HasPreference]->(pref)
    WITH pref, person WHERE person.id IS NULL
    RETURN count(pref)
""")
print(f"Orphan pref: {rows[0][0] if rows else 0}")

# Orphan facts: should be 0
cols, rows = safe_get_all(conn, """
    MATCH (f:Fact)
    OPTIONAL MATCH (person:Person)-[:HasFact]->(f)
    WITH f, person WHERE person.id IS NULL
    RETURN count(f)
""")
print(f"Orphan facts: {rows[0][0] if rows else 0}")
```

### 7. Snapshot Before Cleanup (optional)
```bash
cp /root/.hermes/commons/db/ocas-weave/weave.lbug \
   /root/.hermes/commons/db/ocas-weave/snapshots/weave-$(date +%Y%m%d-%H%M%S).lbug
```

## Key Considerations

### Safe Deletion Criteria
Only delete Person nodes when:
- Name is null AND
- No relationships of any type exist (Knows, HasPreference, HasFact)
- Verified via check above

Only delete orphan Preferences/Facts when confirmed disconnected from Person.

### Relationship Preservation
- **Knows edges**: never delete these — they link two real people
- **HasPreference/HasFact**: check person_id before deleting nodes, not edges
- Never `DETACH DELETE` a person with Knows edges unless you've confirmed it's a true duplicate

### What to Clear vs What to Delete
- **Corrupted occupation/org/city**: SET to empty string `''`
- **Garbage notes field**: SET to empty string
- **Null-name isolated Person**: DETACH DELETE
- **Orphan Preference/Fact with no Person edge**: DELETE
- **Duplicate Person with Knows edges**: MERGE properties into survivor, transfer edges, DELETE duplicate

## Known Data Corruption Patterns (from enrichment bug, discovered Apr 2026)

| Pattern | Example | Fix |
|---------|---------|-----|
| Occupation missing first chars | `'r Vice President Product and Exper'` | Clear field |
| Occupation mid-string | `'ing ManagerDesign'` | Clear field |
| LinkedIn text stored as occupation | `'LinkedIn Excited to start'` / `'LinkedIn sr vp'` | Clear field |
| Bio instead of occupation | `'Exciting progress as the foundation is being poured'` | Clear field |
| Person's own name in occupation | `'Benjamin Brown'` / `'Heather Scoville'` | Clear field |
| Single-word fragment org | `'Greater'`, `'Atlantic'`, `'Serving'`, `'Senior'` | Clear org |
| Job title in org field | `org='Senior'`, `org='CMO'`, `org='Director'` | Clear org |
| Person's own name in org | `'Jeffrey Hutchison & Associates'` | Clear org |
| Surname in org field | `'Georgeson'` (not a company) | Clear org |
| Name+location in city field | `'Heather Scoville Ladora, IA'` | Clear city |
| Occupation-as-org | org='Senior'/'Product'/'Finance'/'General' | Clear org |

## Fact Node Creation (LadybugDB-Specific)

LadybugDB **does not support MERGE** for creating nodes with unknown properties. The `CREATE (f:Fact {...})` pattern requires ALL node properties to be specified at creation time, including `id`. MERGE with `ON CREATE SET` fails with "expects primary key id as input".

### Correct Pattern for Creating Facts

```python
import uuid
from real_ladybug import Database, Connection

db = Database(DB_PATH)  # read_only=False
conn = Connection(db)

# 1. CREATE the Fact node with ALL properties including id
fact_id = str(uuid.uuid4())
conn.execute(f"""
    CREATE (f:Fact {{id: '{fact_id}', predicate: 'predicate_name', value: 'some value',
        source_type: 'system', source_ref: 'reference_tag',
        confidence: 0.9, record_time: '{now}'}})
""")

# 2. CREATE the relationship separately (HasFact has NO properties)
conn.execute(f"""
    MATCH (p:Person {{id: '{person_id}'}}), (f:Fact {{id: '{fact_id}'}})
    CREATE (p)-[:HasFact]->(f)
""")

# 3. For updating existing facts, use SET:
conn.execute(f"""
    MATCH (p:Person {{id: '{person_id}'}})-[:HasFact]->(f:Fact {{predicate: 'predicate_name'}})
    SET f.value = 'new value', f.record_time = '{now}'
""")

# 4. To check if a fact exists before creating:
r = conn.execute("""
    MATCH (p:Person {id: $person_id})-[:HasFact]->(f:Fact {predicate: 'predicate_name'})
    RETURN count(f)
""", {'person_id': person_id})
existing = r.get_next()[0] if r.get_next() else 0
```

**Key rules:**
- `value` must be a string (even for numbers: `'7.7'` not `7.7`)
- `confidence` must be a float (0.0-1.0)
- Relationship `HasFact` has **no properties** (no `fact_key`)
- Always use parameterized queries to prevent SQL injection

## System Fact Nodes (Quality & Enrichment Tracking)

Two system Fact predicates track data quality and enrichment status (created Apr 2026):

### data_quality_score (0-10 scale)
```python
# Score calculation considers:
# - Full name: 0.75 points
# - Contact methods: email (0.25), phone (0.5), custom email domain (0.25 bonus)
# - Multiple contact methods: 0.5 bonus
# - Work data: org (0.5), occupation (0.5)
# - Location: city (0.5), country (0.25)
# - Socials from Facts: up to 2.0 points
# - Family/relationships from Facts: up to 1.5 points
# - Career history from Facts: up to 1.0 points
# - Interests from Facts: up to 1.0 points
# - Education from Facts: up to 0.5 points
# - Content/publications from Facts: up to 0.5 points
# - Enrichment source quality: up to 1.5 points
# Total capped at 10.0
```

### enrichment_status
- `enriched` — properly researched via Scout methodology (44 contacts)
- `enriched_corrupt` — old web_enrichment pipeline (broken data) (534 contacts)
- `not_enriched` — untouched since Google import (453 contacts)

These Facts are internal to Weave and **do not sync** to Google Contacts (sync only exports Person-level fields).

## Real-World Scale (Apr 2026 pass)
- 1,036 Person nodes → 1,031 after cleanup (5 null-name deleted)
- 52 orphan Preferences deleted, 18 orphan Facts deleted
- 14 corrupted occupation fields cleared
- 12 fragment org fields cleared
- 2 garbage city fields cleared
- 46 email-based duplicate pairs identified (not auto-merged)
- 73 people missing occupations, 22 missing orgs, 251 missing emails, 464 missing phones

## References
- LadybugDB documentation for Cypher syntax
- Weave sync scripts in `/root/.hermes/scripts/`
- Weave database at `/root/.hermes/commons/db/ocas-weave/weave.lbug`
- Schema reference: `skill_view('ocas-weave', 'references/schemas.md')`