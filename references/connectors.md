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

Outbound sync (requires `writeback.google_contacts: true` in config — no per-sync approval needed):

```python
def sync_outbound_google(db, creds, last_sync_at):
    import time
    conn = lb.Connection(db)
    service = build("people", "v1", credentials=creds)
    rows = list(conn.execute("""
        MATCH (p:Person)
        WHERE p.record_time > $ts AND p.google_resource_name IS NOT NULL
        RETURN p.google_resource_name, p.name_given, p.name_family,
               p.email, p.phone, p.org, p.occupation, p.location_city,
               p.location_country, p.notes, p.id
    """, {"ts": last_sync_at}))
    pushed = failed = skipped = stale = rate_limited = 0
    for rn, given, family, email, phone, org, title, city, country, notes, pid in rows:
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
        if not body:
            skipped += 1
            continue
        update_fields = [k for k in ["names","emailAddresses","phoneNumbers",
                                      "organizations","addresses","biographies"] if k in body]
        attempt, backoff = 0, 5.0
        while attempt < 4:
            attempt += 1
            try:
                # Fetch etag FIRST (required for PATCH)
                current = service.people().get(
                    resourceName=rn, personFields="metadata"
                ).execute()
                etag = current.get("etag")
                if etag:
                    body["etag"] = etag
                service.people().updateContact(
                    resourceName=rn,
                    updatePersonFields=",".join(update_fields),
                    body=body
                ).execute()
                pushed += 1
                time.sleep(1.3)  # ~45 contacts/min at 2 calls each — stay under 90/min quota
                break
            except Exception as e:
                err = str(e)
                if "404" in err or "NOT_FOUND" in err:
                    # Stale: contact deleted from Google — clear Weave reference
                    conn.execute("""
                        MATCH (p:Person {id: $id})
                        SET p.google_resource_name = null
                    """, {"id": pid})
                    stale += 1
                    break
                elif "429" in err or "RESOURCE_EXHAUSTED" in err or "rate limit" in err.lower():
                    rate_limited += 1
                    if attempt < 4:
                        time.sleep(backoff)
                        backoff *= 2  # 5s → 10s → 20s → 40s
                    else:
                        failed += 1
                elif "502" in err:
                    # Transient Google server error — retry once after 5s
                    if attempt < 2:
                        time.sleep(5.0)
                    else:
                        failed += 1
                else:
                    failed += 1
                    break
    return {"pushed": pushed, "failed": failed, "skipped": skipped,
            "stale": stale, "rate_limited": rate_limited}
```

## Google People API — Operational Learnings (Apr 2026)

**Quota limits (hard-won):**
- `Critical read requests` quota: 90/min per user per project
- **Both GET (etag fetch) AND PATCH (update) count against this same 90/min bucket**
- At 1.3s sleep between contacts: ~45 contacts/min — within safe margin
- **Use `BatchUpdateContacts` for outbound** — up to 200 contacts per batch, reduces 2N API calls to N/200
- If not batching: sleep 1.3s minimum between individual contact updates
- If batching (recommended): sleep 1.5s between batches of 200

**Rate limit backoff (critical):**
- Start backoff at **5s minimum**, not 1s — starting too aggressive cascades 429s without clearing the quota window
- On 429: retry with exponential backoff doubling from 5s (5s → 10s → 20s → 40s), max 4 attempts
- On 502 (Google server error): retry once after 5s, then mark failed — 502s are transient

**Etag requirement:**
- `updateContact` requires current etag in the request body or returns 412
- Must GET the contact first to obtain the etag (counts toward read quota)
- **With BatchUpdateContacts**: provide etag per contact in the batch body

**Stale references (404):**
- A Weave record may have `google_resource_name` but the contact was deleted from Google
- On 404: clear `google_resource_name` in Weave so future syncs don't retry
- Track as `stale` count in sync results

**Token path:**
- Use `/root/.hermes/google_token.json` — Jared is the Google Contacts account owner
- Indigo's token is at `/root/.hermes-indigo/google_token.json` and is a separate account


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
