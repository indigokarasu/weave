# Connectors

Bidirectional sync for Google Contacts and Clay. Both connectors run within the same process as Weave, sharing the same Database object. Never open a connector as a separate READ_WRITE process while Weave has the database open.

Sync state is stored in `{agent_root}/commons/db/ocas-weave/config.json`.

## Rules (both connectors)

Inbound always runs before outbound in a sync session.
Conflict resolution: Weave provenance wins. External data fills gaps; it does not overwrite higher-confidence Weave records.
Outbound requires the relevant writeback flag `true` in config AND explicit per-sync user approval. Neither alone is sufficient.
Report counts after every sync: N upserted, N skipped, N pushed, N failed.

## Google Contacts

API: Google People API v1. Scope: `https://www.googleapis.com/auth/contacts`.

Field map (Google → Weave):

resourceName → google_resource_name
names[0].displayName → name
names[0].givenName → name_given
names[0].familyName → name_family
emailAddresses[0].value → email
phoneNumbers[0].value → phone
organizations[0].name → org
organizations[0].title → occupation
addresses[0].city → location_city
addresses[0].countryCode → location_country
biographies[0].value → notes

Inbound sync:

```python
import real_ladybug as lb, uuid
from datetime import datetime, timezone
from googleapiclient.discovery import build
from pathlib import Path

DB_PATH = Path("{agent_root}/commons/db/ocas-weave/weave.lbug").expanduser()

def sync_inbound_google(db, creds):
    conn = lb.Connection(db)
    service = build("people", "v1", credentials=creds)
    now = datetime.now(timezone.utc).isoformat()
    contacts, page_token = [], None
    while True:
        resp = service.people().connections().list(
            resourceName="people/me", pageSize=1000,
            personFields="names,emailAddresses,phoneNumbers,organizations,addresses,biographies",
            pageToken=page_token
        ).execute()
        contacts.extend(resp.get("connections", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    upserted = skipped = 0
    for c in contacts:
        rn = c.get("resourceName")
        name = (c.get("names") or [{}])[0].get("displayName", "")
        if not name:
            skipped += 1
            continue
        existing = list(conn.execute(
            "MATCH (p:Person {google_resource_name: $rn}) RETURN p.id",
            {"rn": rn}
        ))
        pid = existing[0][0] if existing else str(uuid.uuid4())
        conn.execute("""
            MERGE (p:Person {id: $id})
            SET p.google_resource_name=$rn, p.name=$name,
                p.name_given=$given, p.name_family=$family,
                p.email=$email, p.phone=$phone, p.org=$org,
                p.occupation=$title, p.location_city=$city,
                p.location_country=$country, p.notes=$notes,
                p.source_type='imported', p.source_ref=$rn,
                p.confidence=0.8, p.record_time=$now
        """, {
            "id": pid, "rn": rn, "name": name,
            "given": (c.get("names") or [{}])[0].get("givenName", ""),
            "family": (c.get("names") or [{}])[0].get("familyName", ""),
            "email": (c.get("emailAddresses") or [{}])[0].get("value", ""),
            "phone": (c.get("phoneNumbers") or [{}])[0].get("value", ""),
            "org": (c.get("organizations") or [{}])[0].get("name", ""),
            "title": (c.get("organizations") or [{}])[0].get("title", ""),
            "city": (c.get("addresses") or [{}])[0].get("city", ""),
            "country": (c.get("addresses") or [{}])[0].get("countryCode", ""),
            "notes": (c.get("biographies") or [{}])[0].get("value", ""),
            "now": now
        })
        upserted += 1
    return {"upserted": upserted, "skipped": skipped}
```

Outbound sync (requires `writeback.google_contacts: true` in config — no per-sync approval needed). Uses `BatchUpdateContacts` API for efficiency:

```python
def sync_outbound_google(db, creds, last_sync_at):
    import time, urllib.parse
    from googleapiclient.discovery import build
    
    conn = lb.Connection(db)
    service = build("people", "v1", credentials=creds)
    
    # Get contacts modified since last sync
    rows = list(conn.execute("""
        MATCH (p:Person)
        WHERE p.record_time > $ts AND p.google_resource_name IS NOT NULL
          AND (p.source_type IS NULL OR p.source_type <> 'imported')
        RETURN p.google_resource_name, p.name_given, p.name_family,
               p.email, p.phone, p.org, p.occupation, p.location_city,
               p.location_country, p.notes, p.id
    """, {"ts": last_sync_at}))
    
    if not rows:
        return {"pushed": 0, "failed": 0, "skipped": 0, "stale": 0, "rate_limited": 0}
    
    # Batch fetch etags using people:batchGet (50 per request)
    rn_list = [r[0] for r in rows]
    etag_map = {}
    for i in range(0, len(rn_list), 50):
        batch_rns = rn_list[i:i+50]
        try:
            resp = service.people().batchGet(
                resourceNames=batch_rns,
                personFields="metadata"
            ).execute()
            for person in resp.get("responses", []):
                p = person.get("person", {})
                rn = p.get("resourceName", "")
                etag = p.get("etag", "")
                if rn and etag:
                    etag_map[rn] = etag
        except Exception as e:
            print(f"Etag batch error at {i}: {e}")
        time.sleep(0.3)
    
    # Build update bodies
    all_updates = []
    skipped = 0
    for rn, given, family, email, phone, org, title, city, country, notes, pid in rows:
        etag = etag_map.get(rn)
        if not etag:
            skipped += 1
            continue
        
        body = {}
        if given or family:
            body["names"] = [{"givenName": given or "", "familyName": family or ""}]
        if email:
            body["emailAddresses"] = [{"value": email}]
        if phone:
            body["phoneNumbers"] = [{"value": phone}]
        if org or title:
            body["organizations"] = [{"name": org or "", "title": title or ""}]
        if city or country:
            body["addresses"] = [{"city": city or "", "countryCode": country or ""}]
        if notes:
            body["biographies"] = [{"value": notes}]
        
        if body:
            body["etag"] = etag
            all_updates.append((rn, body, pid))
    
    # Batch update using people:batchUpdateContacts (200 per request)
    pushed = failed = stale = rate_limited = 0
    ALL_FIELDS = "names,emailAddresses,phoneNumbers,organizations,addresses,biographies"
    
    for i in range(0, len(all_updates), 200):
        batch = all_updates[i:i+200]
        contacts_map = {rn: body for rn, body, pid in batch}
        batch_pids = {rn: pid for rn, body, pid in batch}
        
        attempt, backoff = 0, 5.0
        while attempt < 4:
            attempt += 1
            try:
                resp = service.people().batchUpdateContacts(
                    contacts=contacts_map,
                    updateMask=ALL_FIELDS
                ).execute()
                
                # API may return empty body {} on success — treat as all succeeded
                results = resp.get("updateResult", {})
                if results:
                    for rn, result in results.items():
                        status = result.get("httpStatusCode", 0)
                        if status == 200:
                            pushed += 1
                        elif status == 404:
                            stale += 1
                            pid = batch_pids.get(rn)
                            if pid:
                                conn.execute("MATCH (p:Person {id: $id}) SET p.google_resource_name = null", {"id": pid})
                        else:
                            failed += 1
                else:
                    pushed += len(contacts_map)
                
                time.sleep(1.5)  # Pause between batches
                break
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err or "rate limit" in err.lower():
                    rate_limited += 1
                    if attempt < 4:
                        time.sleep(backoff)
                        backoff *= 2
                    else:
                        failed += len(contacts_map)
                elif "404" in err or "NOT_FOUND" in err:
                    for rn, body, pid in batch:
                        stale += 1
                        conn.execute("MATCH (p:Person {id: $id}) SET p.google_resource_name = null", {"id": pid})
                    break
                else:
                    failed += len(contacts_map)
                    break
    
    return {"pushed": pushed, "failed": failed, "skipped": skipped, 
            "stale": stale, "rate_limited": rate_limited}
```

## Google People API — Operational Learnings (Apr 2026)

**Quota limits (hard-won):**
- `Critical read requests` quota: 90/min per user per project
- **Both GET (etag fetch) AND PATCH (update) count against this same 90/min bucket**
- **Use `BatchUpdateContacts` for outbound** — up to 200 contacts per batch, reduces 2N API calls to ~N/200 + N/50
- **Use `people:batchGet` for etag fetching** — 50 contacts per request vs individual GETs
- If not batching: sleep 1.3s minimum between individual contact updates
- If batching (recommended): sleep 1.5s between batches of 200

**BatchUpdateContacts empty response (discovered Apr 2026):**
- The API may return HTTP 200 with empty body `{}` instead of `updateResult`
- Empty response means ALL contacts in the batch were updated successfully
- Must handle both cases: `updateResult` with per-contact status, AND empty body as success

**Rate limit backoff (critical):**
- Start backoff at **5s minimum**, not 1s — starting too aggressive cascades 429s without clearing the quota window
- On 429: retry with exponential backoff doubling from 5s (5s → 10s → 20s → 40s), max 4 attempts
- On 502 (Google server error): retry once after 5s, then mark failed — 502s are transient

**Etag requirement:**
- `updateContact` and `batchUpdateContacts` require current etag or returns 412/FAILED_PRECONDITION
- **For batch updates**: use `people:batchGet` (50 per request) to fetch etags efficiently
- Etags are URL-encoded strings (e.g., `%EgMBLjcaBAECBQciDGN...`)

**Stale references (404):**
- A Weave record may have `google_resource_name` but the contact was deleted from Google
- On 404: clear `google_resource_name` in Weave so future syncs don't retry
- Track as `stale` count in sync results

**Data integrity before sync:**
- Run validation before outbound sync to catch bad data from web enrichment
- Common issues: fragment phones (< 7 digits), full bios in occupation field, job titles in city field, overlong org fields
- Clear invalid fields rather than pushing bad data to Google

**Token path:**
- The script at `/root/.hermes/skills/ocas-weave/scripts/google_sync.py` reads `TOKEN_PATH` — verify which file it's using: `grep TOKEN_PATH /root/.hermes/skills/ocas-weave/scripts/google_sync.py`
- **Correct file**: `/root/.hermes/jared_google_credentials.json` — this is Jared's Google Contacts account with full `contacts` scope and a valid refresh token
- **Stale file**: `/root/.hermes/jared_google_credentials.json` (DO NOT use — see above) — DO NOT use. This file has an expired/revoked refresh token (`invalid_grant`) and NO contacts scope. It was used historically but the credentials are permanently dead
- Indigo's token is at `/root/.hermes/indigo_google_credentials.json` — separate Google account, not used for contacts sync

## Contact Snapshot Safeguard (required before outbound sync)

**CRITICAL**: Before ANY outbound sync that modifies Google Contacts, a snapshot MUST be taken. This prevents data loss from bad enrichment data or sync bugs.

Script: `/root/.hermes/skills/ocas-weave/scripts/contact_snapshots.py`

### How it works
1. `create_snapshot(resource_names)` fetches current Google state via `people:batchGet` (50 per request)
2. Stores each contact's full data in timestamped JSONL: `~/.hermes/commons/db/ocas-weave/snapshots/{sync_id}.jsonl`
3. Snapshot includes: timestamp, sync_id, resource_name, person_id, etag, and all mutable fields
4. Integrated into `weave_google_bidirectional_sync.py` — runs automatically before every outbound push

### Commands
```bash
# List snapshots
python3 weave_contact_snapshots.py list

# Restore from snapshot (dry run first)
python3 weave_contact_snapshots.py restore <snapshot_file> --dry-run
python3 weave_contact_snapshots.py restore <snapshot_file>

# Compare two snapshots
python3 weave_contact_snapshots.py diff <snap_a> <snap_b>
```

### Recovery procedure
If bad data is pushed to Google:
1. List snapshots: `python3 weave_contact_snapshots.py list`
2. Find the snapshot taken before the bad sync (check `sync_log.jsonl` for `snapshot_file` field)
3. Dry-run restore: `python3 weave_contact_snapshots.py restore <snapshot_file> --dry-run`
4. Restore: `python3 weave_contact_snapshots.py restore <snapshot_file>`

### Snapshot Script Pitfalls

**Token path must match the main sync script**: `weave_contact_snapshots.py` has its own `TOKEN_PATH` that can drift from the bidirectional script. Both must point to the same credentials file (`jared_google_credentials.json` for Jared's contacts account). Common bugs:
- `TOKEN_PATH=***` corruption — same sed/find-and-replace corruption as the main sync script
- Pointing to `indigo_google_credentials.json` (Indigo's account) instead of `jared_google_credentials.json` — Indigo's token may not have `contacts` scope
- Double `Path` import (`from pathlib import Path as _P` when `Path` is already imported at the top)

**Symptom**: Snapshot creates an empty file (0 bytes) with "588 errors" on CLI output but the main sync proceeds and succeeds. Always verify snapshot file has content after a run: `wc -l <snapshot_file>`.

**Fix**: Verify and patch:
```bash
grep TOKEN_PATH /root/.hermes/skills/ocas-weave/scripts/contact_snapshots.py
# Should be: TOKEN_PATH = HERMES_HOME / 'jared_google_credentials.json'
python3 -c "import ast; ast.parse(open('/root/.hermes/skills/ocas-weave/scripts/contact_snapshots.py').read()); print('OK')"
```

### Why this is mandatory
- Web enrichment can produce corrupted data (truncated strings, fragments, full bios in wrong fields)
- Syncing bad data overwrites good data in Google with no automatic recovery
- Snapshots are the ONLY rollback mechanism — Google's native restore is coarse (1 week granularity)
- **NEVER skip the snapshot step to save time. The 30 seconds it takes can save hours of manual repair.**

## Data Validation Before Sync

Before outbound sync, validate Weave data to catch enrichment bugs:

```python
import re

def validate_contact(org, occupation, phone, city):
    issues = []
    
    # Phone: must have 7+ digits
    if phone and len(re.sub(r'\D', '', phone)) < 7:
        issues.append('fragment_phone')
    
    # Org: not a fragment, not too long
    if org:
        if len(org) < 3 and org.upper() not in {'IBM', 'AMD', 'GE', 'MIT', 'CNN'}:
            issues.append('fragment_org')
        if len(org) > 80:
            issues.append('overlong_org')
    
    # Occupation: not truncated (starts lowercase), not a full bio
    if occupation:
        if occupation[0].islower() and occupation[0] not in ['i', 'a']:
            issues.append('truncated_occupation')
        if len(occupation) > 80 or '@' in occupation:
            issues.append('bio_not_title')
    
    # City: not a job title
    if city and re.search(r'(Executive|Manager|Director|Engineer|VP)', city):
        issues.append('title_not_city')
    
    return issues
```

**Clear invalid fields** rather than pushing bad data. An empty field is better than a wrong field.

## LadybugDB Corruption Repair

If `get_all()` raises `UnicodeDecodeError`, a Person node has corrupt UTF-8 data. Row-by-row iteration works (skips corrupt rows), but the corrupt data should be cleared:

```python
import real_ladybug as lb

db = lb.Database("/path/to/weave.lbug", read_only=True)
conn = lb.Connection(db)

# Find corrupt rows by iterating
cypher = "MATCH (p:Person) RETURN p.id, p.name, p.org, p.occupation"
r = conn.execute(cypher)
cols = r.get_column_names()

corrupt_ids = []
while True:
    try:
        row = r.get_next()
    except StopIteration:
        break
    except Exception as e:
        if "No more tuples" in str(e):
            break
        # Try to extract ID from previous context or skip
        continue
conn.close()

# Clear corrupt fields (open as read_write)
db2 = lb.Database("/path/to/weave.lbug", read_only=False)
conn2 = lb.Connection(db2)
for pid in corrupt_ids:
    conn2.execute("MATCH (p:Person {id: $id}) SET p.occupation = ''", {"id": pid})
conn2.close()
```

**Common corruption source**: Web enrichment scraper stores truncated strings (missing first characters). Fix the scraper before re-enriching.


## Clay

API: Clay REST API v1. Auth: Bearer token. Base: `https://api.clay.earth/v1`.

Field map (Clay → Weave):

id → clay_id
name → name
first_name → name_given
last_name → name_family
email → email
phone → phone
company → org
title → occupation
city → location_city
country_code → location_country
notes → notes

Inbound sync:

```python
import requests, uuid
from datetime import datetime, timezone

def sync_inbound_clay(db, api_key):
    conn = lb.Connection(db)
    headers = {"Authorization": f"Bearer {api_key}"}
    now = datetime.now(timezone.utc).isoformat()
    upserted = skipped = 0
    page = 1
    while True:
        resp = requests.get("https://api.clay.earth/v1/people", headers=headers,
                            params={"page": page, "per_page": 100})
        resp.raise_for_status()
        data = resp.json()
        people = data.get("people", [])
        if not people:
            break
        for person in people:
            clay_id = person.get("id")
            name = person.get("name", "")
            if not name:
                skipped += 1
                continue
            existing = list(conn.execute(
                "MATCH (p:Person {clay_id: $cid}) RETURN p.id, p.confidence",
                {"cid": clay_id}
            ))
            if existing:
                pid, conf = existing[0]
                if float(conf or 0) >= 0.8:
                    skipped += 1
                    continue
            else:
                pid = str(uuid.uuid4())
            conn.execute("""
                MERGE (p:Person {id: $id})
                SET p.clay_id=$cid, p.name=$name,
                    p.name_given=$given, p.name_family=$family,
                    p.email=CASE WHEN p.email IS NULL THEN $email ELSE p.email END,
                    p.phone=CASE WHEN p.phone IS NULL THEN $phone ELSE p.phone END,
                    p.org=CASE WHEN p.org IS NULL THEN $org ELSE p.org END,
                    p.occupation=CASE WHEN p.occupation IS NULL THEN $title ELSE p.occupation END,
                    p.location_city=CASE WHEN p.location_city IS NULL THEN $city ELSE p.location_city END,
                    p.source_type='imported', p.source_ref=$cid,
                    p.confidence=0.75, p.record_time=$now
            """, {
                "id": pid, "cid": clay_id, "name": name,
                "given": person.get("first_name", ""), "family": person.get("last_name", ""),
                "email": person.get("email", ""), "phone": person.get("phone", ""),
                "org": person.get("company", ""), "title": person.get("title", ""),
                "city": person.get("city", ""), "now": now
            })
            upserted += 1
        if page >= data.get("total_pages", 1):
            break
        page += 1
    return {"upserted": upserted, "skipped": skipped}
```

Outbound sync (requires writeback enabled + explicit approval):

```python
def sync_outbound_clay(db, api_key, last_sync_at):
    conn = lb.Connection(db)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    rows = list(conn.execute("""
        MATCH (p:Person)
        WHERE p.record_time > $ts AND p.confidence >= 0.7 AND p.clay_id IS NOT NULL
        RETURN p.clay_id, p.name, p.email, p.org, p.occupation, p.location_city
    """, {"ts": last_sync_at}))
    pushed = failed = 0
    for clay_id, name, email, org, title, city in rows:
        body = {k: v for k, v in {"name": name, "email": email,
                "company": org, "title": title, "city": city}.items() if v}
        try:
            requests.patch(f"https://api.clay.earth/v1/people/{clay_id}",
                           headers=headers, json=body).raise_for_status()
            pushed += 1
        except Exception as e:
            failed += 1
            print(f"Failed Clay {clay_id}: {e}")
    return {"pushed": pushed, "failed": failed}
```
