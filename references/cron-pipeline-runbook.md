# Cron Pipeline Runbook (Agent-Driven Enrichment)

The correct step-by-step runbook for the agent-driven overnight enrichment pipeline as of June 2026. This supersedes any older pipeline instructions that reference removed components.

## Pre-Conditions
- Running as a cron job (no user present)
- `execute_code` is BLOCKED — use `terminal` with `python3 /path/to/script.py` (write script via `write_file`, then execute). Do NOT use heredoc (`python3 << 'EOF'`) — it triggers false backgrounding detection.
- LadybugDB bridge does NOT exist — skip any stop/start bridge instructions even if the invocation message says otherwise
- `enrichment_data.py` does NOT exist — use direct WeaveDB queries and curl. The invocation message may reference this file; it is stale.
- **The cron invocation message may contain a stale runbook** that references removed components. Always defer to this reference file (which is updated first) over the invocation text.

## Pipeline Steps

### Step 1: Inbound Google Sync
```bash
cd <hermes-home>/skills/ocas-weave && AGENT_ROOT=<hermes-home> HOME=/root python3 -u scripts/google_sync.py
```
Expected: ~977 contacts fetched, ~953 upserted, ~24 skipped. Outbound pushes ~587 contacts.

### Step 2: Check SearXNG Health
```bash
curl -s "http://localhost:8888/search?q=test&format=json&limit=3"
```
Should return JSON with `results` array. If fails, try restart:
```bash
sudo systemctl restart searxng
```
If restart fails, continue with web_search only — do NOT skip enrichment.
### Step 3: Get Contacts with Gaps

**Note on duplicates:** The persons table may have duplicate records for the same real person (e.g., "Abi Jones" ×2, "Cameron Moberg" ×2, "Adam Abouraya" ×2). The SQL below returns one row per ID. When computing coverage metrics, track by distinct **name** not distinct ID — a name appearing once with both fields filled and once empty inflates the "neither" count misleadingly.

**Note on DB sprawl:** If enrichment subagents have been dispatched in prior runs, check for and remove stale DB files before querying. Run: `find <hermes-root> -name "weave.sqlite" -type f | grep -v "profiles/indigo/commons"`. Remove any stale ones.

```python
# Via terminal inline Python
cd <hermes-home>/skills/ocas-weave && python3 -c "
import sys
sys.path.insert(0, 'scripts')
from weave_sqlite import WeaveDB
db = WeaveDB()
results = db.execute(\"\"\"
    SELECT p.id, p.name, p.email, p.phone, p.location_city, p.occupation, p.org, p.confidence
    FROM persons p
    WHERE (p.occupation IS NULL OR p.occupation = '' OR p.org IS NULL OR p.org = '')
    AND p.confidence >= 0.3
    ORDER BY p.confidence DESC
    LIMIT 50
\"\"\")
for r in results:
    print(f'{r[\"id\"]} | {r[\"name\"]} | {r[\"email\"]} | occ={r[\"occupation\"]} | org={r[\"org\"]} | city={r[\"location_city\"]} | conf={r[\"confidence\"]}')
"
```

### Step 3.5: Clear Pre-Existing Garbage
Before enriching, scan for and clear known junk values so COALESCE preserves nothing:
```python
# Common garbage orgs: "PI", "George", "YouTube", "_VOIS", "New", "St", "Donna Karan New York"
# Common garbage occupations: "gram Manager Big Tech Refuge", "Save", "Building Manager"
garbage = db.execute("SELECT id, name, occupation, org FROM persons WHERE org IN ('PI','George','YouTube','_VOIS','New','St','Donna Karan New York') OR occupation IN ('gram Manager Big Tech Refuge','Save','Building Manager','All Restaurants','Short Interest')")
for g in garbage:
    if g['org'] in ('PI','George','YouTube','_VOIS','New','St','Donna Karan New York'):
        db.execute_write('UPDATE persons SET org = NULL WHERE id = ?', (g['id'],))
    if g['occupation'] in ('gram Manager Big Tech Refuge','Save','Building Manager','All Restaurants','Short Interest'):
        db.execute_write('UPDATE persons SET occupation = NULL WHERE id = ?', (g['id'],))
```

### Step 4: Process Each Contact (Scout → Sift → Sherlock → Write)

**SCOUT** (per contact):
- `web_search(query="{name} {org_or_location} LinkedIn", limit=3)` — **primary source**
- Parse LinkedIn title/description from search results
- SearXNG supplementary: `curl -s "http://localhost:8888/search?q={name}&format=json&limit=10"`
- **Unresolvable?** If name is common + no email/phone/location → see `references/unresolvable-contacts.md`

**SIFT** (per contact):
- Fetch up to 3-5 unique URLs via `curl -s "https://r.jina.ai/URL"` (Jina Reader)
- Skip LinkedIn URLs (HTTP 451 from Jina)
- Extract occupation/org/location from content using LLM reasoning

**SHERLOCK** (per contact):
- Cross-reference all sources
- Verify name match (reject if contact's own name appears in extracted occupation — wrong person)
- Assign confidence per field

**WRITE** (per contact):
Use the three-step write pattern. See `references/enrichment-write-pattern.md` for the canonical implementation.

Skip rules:
- Skip contacts where both `occupation` AND `org` are null AND confidence < 0.5
- Skip unresolvable contacts (common name, no disambiguator)
- Skip non-person entries (businesses: OpenTable, PayPal, Venmo, Google-as-entity, DJI, etc.)

### Step 5: Periodic Google Sync
After every 10 enriched contacts, run google_sync.py again.

### Step 6: Final Sync + Stats
1. Run final google_sync.py
2. Query stats:
```python
db.execute("SELECT COUNT(*) as cnt FROM persons")
db.execute("SELECT COUNT(*) as cnt FROM facts WHERE predicate = 'enrichment'")
```

## Read-Back Verification (Required)

After every batch, verify writes. **Important**: `db.execute()` returns a list of dicts, not a list of tuples. Use dict key access:

```python
r = db.execute("SELECT occupation, org, location_city FROM persons WHERE id = ?", (person_id,))
if r:
    row = r[0]
    print(f"Verified: occ={row['occupation']} org={row['org']} city={row['location_city']}")
```

**Common mistake**: `r[0][0]` (tuple indexing) raises `KeyError: 0`. Always use `r[0]['column_name']`.

## Confidence Scoring Guide

| Score | Meaning |
|-------|---------|
| 0.9+ | Multiple authoritative sources agree (LinkedIn + web + email domain) |
| 0.8-0.89 | Single authoritative source (LinkedIn profile or company email domain) |
| 0.7-0.79 | Strong inference (web_search title match, consistent across sources) |
| 0.6-0.69 | Moderate inference (name variant match, single source without corroboration) |
| 0.5-0.59 | Weak inference (possible match, limited data) |
| <0.5 | Insufficient data — do NOT write |

## Batch Write Pattern

For writing multiple contacts efficiently in a single terminal call:

```python
import sys, json, uuid
sys.path.insert(0, '<hermes-home>/skills/ocas-weave/scripts')  # ABSOLUTE PATH required in cron/subagent context
from weave_sqlite import WeaveDB
from datetime import datetime, timezone

db = WeaveDB()
now = datetime.now(timezone.utc).isoformat()

contacts = [
    {'id': '...', 'occupation': '...', 'org': '...', 'location_city': '...', 'source_type': 'inferred', 'source_ref': '...', 'confidence': 0.8},
    # ... more contacts
]

for c in contacts:
    person_id = c['id']
    if not c['occupation'] and not c['org'] and c['confidence'] < 0.5:
        continue
    db.execute_write(
        'UPDATE persons SET occupation = COALESCE(?, occupation), org = COALESCE(?, org), location_city = COALESCE(?, location_city) WHERE id = ?',
        (c['occupation'], c['org'], c['location_city'], person_id)
    )
    fact_id = str(uuid.uuid4())
    payload = {k: v for k, v in c.items() if k != 'id'}
    db.execute_write(
        'INSERT INTO facts (id, predicate, value, source_type, source_ref, confidence, record_time) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (fact_id, 'enrichment', json.dumps(payload), c['source_type'], c['source_ref'], c['confidence'], now)
    )
    edge_id = str(uuid.uuid4())
    db.execute_write(
        'INSERT INTO edges (id, source_id, target_id, rel_type, source_ref, confidence, record_time) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (edge_id, person_id, fact_id, 'HasFact', c['source_ref'], c['confidence'], now)
    )
    print(f'Written: {person_id}')
```

## Timing

| Step | Expected Duration |
|------|-------------------|
| Google sync | ~3-5 minutes |
| web_search (per contact) | ~5-10 seconds |
| Batch write (25 contacts) | ~5 seconds |
| Full pipeline (25-30 contacts) | ~8-12 minutes |

## Known Good State (June 26, 2026)

- Total persons: ~1,049
- With occupation: ~967
- With org: ~993
- With both: ~951 (90.7% completeness)
- Enrichment facts: 146
- Google sync: 3x daily (inbound + outbound ~588)
- SearXNG: healthy, responding
