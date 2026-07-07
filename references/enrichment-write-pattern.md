# Enrichment Write Pattern (Agent-Driven)

The exact SQLite write sequence for agent-driven enrichment. The `facts` table has **no `person_id` column** — person→fact linkage is always via the `edges` table.

## Three-Step Write Per Contact

```python
import sys, json, uuid
sys.path.insert(0, '<hermes-home>/skills/ocas-weave/scripts')  # ABSOLUTE PATH — relative 'scripts' breaks in subagent/cron context
from weave_sqlite import WeaveDB
from datetime import datetime, timezone

db = WeaveDB()
now = datetime.now(timezone.utc).isoformat()

def write_enrichment(db, person_id, occupation, org, location_city, source_type, source_ref, confidence):
    """Write enrichment data to Weave. Three operations required."""
    
    # Step 1: UPDATE persons (only fills NULL fields via COALESCE)
    update_cols = []
    params = []
    if occupation:
        update_cols.append("occupation = COALESCE(?, occupation)")
        params.append(occupation)
    if org:
        update_cols.append("org = COALESCE(?, org)")
        params.append(org)
    if location_city:
        update_cols.append("location_city = COALESCE(?, location_city)")
        params.append(location_city)
    
    if update_cols:
        sql = f"UPDATE persons SET {', '.join(update_cols)} WHERE id = ?"
        params.append(person_id)
        db.execute_write(sql, tuple(params))
    
    # Step 2: INSERT fact record (the enrichment payload as JSON)
    fact_id = str(uuid.uuid4())
    payload = json.dumps({
        "occupation": occupation,
        "org": org,
        "location_city": location_city,
        "pipeline": "agent_enrichment"
    })
    db.execute_write(
        "INSERT INTO facts (id, predicate, value, source_type, source_ref, confidence, record_time) "
        "VALUES (?, 'enrichment', ?, ?, ?, ?, ?)",
        (fact_id, payload, source_type, source_ref, confidence, now)
    )
    
    # Step 3: INSERT edge linking person → fact
    edge_id = str(uuid.uuid4())
    db.execute_write(
        "INSERT INTO edges (id, source_id, target_id, rel_type, source_ref, confidence, record_time) "
        "VALUES (?, ?, ?, 'HasFact', ?, ?, ?)",
        (edge_id, person_id, fact_id, source_ref, confidence, now)
    )
    
    return fact_id, edge_id
```

## Batch Pattern

For batch processing (e.g., 50 contacts per run), load JSON from stdin:

```python
import sys, json, uuid
sys.path.insert(0, '<hermes-home>/skills/ocas-weave/scripts')  # ABSOLUTE PATH — not 'scripts' which breaks in subagent/cron context
from weave_sqlite import WeaveDB
from datetime import datetime, timezone

db = WeaveDB()
now = datetime.now(timezone.utc).isoformat()

enrichments = json.load(sys.stdin)
results = []
for rec in enrichments:
    # Skip if no meaningful data
    if not rec.get('occupation') and not rec.get('org'):
        continue
    write_enrichment(db, rec['id'], rec.get('occupation'), rec.get('org'),
                     rec.get('location_city'), rec['source_type'], rec['source_ref'], rec['confidence'])
    results.append(rec['name'])
```

Run via: `python3 -c "..." < /tmp/batch.json` or `python3 /tmp/script.py < /tmp/batch.json`

## Cron Mode: Use `terminal`, Not `execute_code`

`execute_code` is **BLOCKED** in cron jobs. Use `terminal` with inline Python:

```bash
cd <hermes-home>/skills/ocas-weave && python3 -c "
import sys, json, uuid
sys.path.insert(0, 'scripts')
from weave_sqlite import WeaveDB
from datetime import datetime, timezone
db = WeaveDB()
now = datetime.now(timezone.utc).isoformat()
with open('/tmp/batch.json') as f:
    data = json.load(f)
# ... write loop ...
"
```

## Read-Back Verification (Required)

After every batch, verify:

```python
for name in verify_names:
    r = db.execute("SELECT occupation, org, location_city FROM persons WHERE name = ?", (name,))[0]
    print(f"{name}: {r['occupation']} @ {r['org']}")
```

## Common Mistakes

1. **Trying to INSERT person_id into facts** — column doesn't exist. Use edges.
2. **Using execute_code in cron** — blocked. Use terminal.
3. **Forgetting COALESCE** — without it, existing data gets overwritten with NULL.
4. **Not generating UUIDs for fact and edge IDs** — they need PKs too.
5. **Writing contacts with no data** — skip if both occupation AND org are null with confidence < 0.5.
6. **Tuple indexing on execute() results** — `db.execute()` returns `list[dict]`, not `list[tuple]`. Use `r[0]['column_name']`, NOT `r[0][0]`. The latter raises `KeyError: 0` and looks like a data error but is actually a code pattern error.
7. **Subagent enrichment to wrong DB** — when using `delegate_task` for enrichment subagents, the subagent may connect to a stale DB at `<hermes-root>/commons/db/ocas-weave/weave.sqlite` (953 persons) instead of the canonical `<hermes-home>/commons/db/ocas-weave/weave.sqlite` (1052 persons). Symptoms: subagent reports writes successful but verification shows empty fields. **Fix**: always include `target_db_path = '<hermes-home>/commons/db/ocas-weave/weave.sqlite'` in subagent context. If using `WeaveDB()` (which uses `parents[3]` auto-resolution), the correct path is used; raw `sqlite3.connect()` calls must hardcode the canonical path.
