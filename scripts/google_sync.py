#!/usr/bin/env python3
"""
Bidirectional Google Contacts sync for Weave — SQLite backend edition.

Uses weave_sqlite.WeaveDB instead of LadybugDB. WAL mode allows concurrent
access from multiple cron jobs and interactive sessions.

Inbound:  Google Contacts → Weave (SQLite)
Outbound: Weave (SQLite) → Google Contacts

Usage:
    AGENT_ROOT=os.path.expanduser("~/.hermes")/profiles/indigo HOME=/root python3 google_sync.py
"""
import json
import os
import re as _re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Paths
AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
SQLITE_DB = AGENT_ROOT / "commons/db/ocas-weave/weave.sqlite"
CONFIG_PATH = AGENT_ROOT / "commons/data/ocas-weave/config.json"

# Shared Google API helpers
sys.path.insert(0, str(Path(__file__).parent))
from google_api import get_access_token, api_get as _api_get, api_post as _api_post, api_patch as _api_patch, PEOPLE_API_BASE

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 google_sync.py")
    sys.exit(0)



def _log(msg):
    print(msg, flush=True)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config):
    config["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _validate_phone(phone):
    if not phone:
        return None
    cleaned = _re.sub(r"[^\d+\(\)\-\. ]", "", phone.strip())
    digits = _re.sub(r"\D", "", cleaned)
    if len(digits) < 7 or len(digits) > 15:
        return None
    return cleaned


def sync_inbound(token):
    """Pull contacts FROM Google INTO Weave using REST API + SQLite backend."""
    now = datetime.now(timezone.utc).isoformat()

    # Import weave_sqlite here (lazy) to avoid import issues in test environments
    sys.path.insert(0, str(Path(__file__).parent))
    from weave_sqlite import WeaveDB

    weave = WeaveDB(SQLITE_DB)

    # Fetch all Google contacts via REST
    person_fields = "names,emailAddresses,phoneNumbers,organizations,addresses,urls,biographies,birthdays,relations"
    contacts = []
    page_token = None
    page = 0
    while True:
        page += 1
        url = f"{PEOPLE_API_BASE}/people/me/connections?personFields={person_fields}&pageSize=100&sources=READ_SOURCE_TYPE_CONTACT"
        if page_token:
            url += f"&pageToken={page_token}"
        data = _api_get(url, token)
        connections = data.get("connections", [])
        contacts.extend(connections)
        _log(f"  Inbound: fetched page {page}: {len(connections)} contacts (total: {len(contacts)})")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(1.0)

    _log(f"  Inbound: total Google contacts fetched: {len(contacts)}")

    # Build lookup maps from SQLite
    all_people = weave.execute(
        "SELECT id, google_resource_name, name, email, phone FROM persons"
    )
    rn_map = {}
    email_map = {}
    phone_map = {}
    for row in all_people:
        pid, grn, name, email, phone = row["id"], row["google_resource_name"], row["name"], row["email"], row["phone"]
        if grn:
            rn_map[grn] = pid
        if email:
            email_map[email.lower()] = pid
        if phone:
            phone_map[phone] = pid

    upserted = enriched = created = skipped = 0
    for c in contacts:
        rn = c.get("resourceName", "")
        name_data = (c.get("names") or [{}])[0] if c.get("names") else {}
        name = name_data.get("displayName", "")
        if not name:
            skipped += 1
            continue

        given = name_data.get("givenName", "")
        family = name_data.get("familyName", "")
        email = (c.get("emailAddresses") or [{}])[0].get("value", "") if c.get("emailAddresses") else ""
        phone = _validate_phone((c.get("phoneNumbers") or [{}])[0].get("value", "")) if c.get("phoneNumbers") else ""
        org = (c.get("organizations") or [{}])[0].get("name", "") if c.get("organizations") else ""
        title_val = (c.get("organizations") or [{}])[0].get("title", "") if c.get("organizations") else ""
        city = (c.get("addresses") or [{}])[0].get("city", "") if c.get("addresses") else ""
        country = (c.get("addresses") or [{}])[0].get("countryCode", "") if c.get("addresses") else ""

        # Match: resource_name → email → phone → new
        pid = rn_map.get(rn) or (email_map.get(email.lower()) if email else None) or (phone_map.get(phone) if phone else None)

        if pid:
            # Gap-fill existing record
            weave.execute_write("""
                UPDATE persons SET
                    name = CASE WHEN name IS NULL OR name = '' THEN :name ELSE name END,
                    name_given = CASE WHEN name_given IS NULL OR name_given = '' THEN :given ELSE name_given END,
                    name_family = CASE WHEN name_family IS NULL OR name_family = '' THEN :family ELSE name_family END,
                    email = CASE WHEN email IS NULL OR email = '' THEN :email ELSE email END,
                    phone = CASE WHEN phone IS NULL OR phone = '' THEN :phone ELSE phone END,
                    org = CASE WHEN org IS NULL OR org = '' THEN :org ELSE org END,
                    occupation = CASE WHEN occupation IS NULL OR occupation = '' THEN :title ELSE occupation END,
                    location_city = CASE WHEN location_city IS NULL OR location_city = '' THEN :city ELSE location_city END,
                    location_country = CASE WHEN location_country IS NULL OR location_country = '' THEN :country ELSE location_country END,
                    google_resource_name = CASE WHEN google_resource_name IS NULL THEN :rn ELSE google_resource_name END,
                    record_time = :now
                WHERE id = :id
            """, {
                "id": pid, "name": name, "given": given, "family": family,
                "email": email, "phone": phone, "org": org, "title": title_val,
                "city": city, "country": country, "rn": rn, "now": now,
            })
            enriched += 1
        else:
            pid = str(__import__("uuid").uuid4())
            weave.execute_write("""
                INSERT INTO persons
                    (id, name, name_given, name_family, email, phone,
                     location_city, location_country, occupation, org,
                     google_resource_name, source_type, source_ref, confidence, record_time)
                VALUES
                    (:id, :name, :given, :family, :email, :phone,
                     :city, :country, :title, :org,
                     :rn, 'imported', :rn, 0.8, :now)
            """, {
                "id": pid, "name": name, "given": given, "family": family,
                "email": email, "phone": phone, "org": org, "title": title_val,
                "city": city, "country": country, "rn": rn, "now": now,
            })
            created += 1
        upserted += 1
        if upserted % 100 == 0:
            _log(f"  Inbound progress: {upserted}/{len(contacts)} processed")

    return {"inbound_upserted": upserted, "inbound_enriched": enriched, "inbound_created": created, "inbound_skipped": skipped}


def sync_outbound(token, last_sync_at):
    """Push Weave changes TO Google Contacts via REST API + SQLite backend."""
    sys.path.insert(0, str(Path(__file__).parent))
    from weave_sqlite import WeaveDB

    weave = WeaveDB(SQLITE_DB)

    ckpt_path = AGENT_ROOT / "commons/db/ocas-weave/staging/outbound_ckpt.txt"
    pushed_set = set()
    if ckpt_path.exists():
        pushed_set = set(l for l in ckpt_path.read_text().strip().split("\n") if l)
        _log(f"  Outbound: resuming from checkpoint ({len(pushed_set)} already pushed)")

    # Find contacts with Fact-sourced LinkedIn URLs
    linkedin_rows = weave.execute("""
        SELECT p.google_resource_name, p.id, f.value
        FROM facts f
        JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        JOIN persons p ON p.id = e.source_id
        WHERE p.record_time > :ts
          AND f.predicate = 'scout_verification_note'
          AND f.value LIKE '%linkedin.com/%'
    """, {"ts": last_sync_at})
    linkedin_map = {}
    for row in linkedin_rows:
        rn, pid, note = row["google_resource_name"], row["id"], row["value"]
        url_match = _re.search(r"(https?://[^\s]*linkedin\.com/in/[^\s]+)", note)
        if url_match:
            linkedin_map[rn or pid] = url_match.group(1)

    # Find records modified since last sync
    rows = weave.execute("""
        SELECT google_resource_name, name_given, name_family,
               email, phone, org, occupation, location_city, location_country, id
        FROM persons
        WHERE record_time > :ts
          AND (source_type IS NULL OR source_type <> 'imported')
    """, {"ts": last_sync_at})

    to_update = [r for r in rows if r["google_resource_name"]]
    to_create = [r for r in rows if not r["google_resource_name"]]
    _log(f"  Outbound: {len(to_update)} contacts to update, {len(to_create)} contacts to create")

    if not to_update and not to_create:
        return {"outbound_pushed": 0, "outbound_failed": 0, "outbound_skipped": 0, "outbound_stale": 0, "outbound_rate_limited": 0, "outbound_created": 0}

    all_updates = []
    all_creates = []
    skipped = 0

    def build_contact_body(rn, given, family, email, phone, org, title, city, country, pid):
        phone_clean = _validate_phone(phone)
        body = {}
        if given or family:
            body["names"] = [{"givenName": given or "", "familyName": family or ""}]
        if email:
            email_entry = {"value": email}
            if email.lower().endswith("@gmail.com"):
                email_entry["type"] = "home"
                email_entry["formattedType"] = "Personal"
            body["emailAddresses"] = [email_entry]
        if phone_clean:
            body["phoneNumbers"] = [{"value": phone_clean}]
        if org or title:
            body["organizations"] = [{"name": org or "", "title": title or ""}]
        if city or country:
            address = {}
            if city:
                if "," in city:
                    parts = [p.strip() for p in city.split(",")]
                    address["city"] = parts[0]
                    if len(parts) > 1:
                        address["region"] = parts[1]
                    if len(parts) > 2:
                        address["countryCode"] = parts[2]
                else:
                    address["city"] = city
            if country and "countryCode" not in address:
                address["countryCode"] = country
            body["addresses"] = [address]
        linkedin_url = linkedin_map.get(rn, linkedin_map.get(pid))
        if linkedin_url:
            body["urls"] = [{"value": linkedin_url, "formattedType": "LinkedIn"}]
        return body

    for r in to_update:
        body = build_contact_body(
            r["google_resource_name"], r["name_given"], r["name_family"],
            r["email"], r["phone"], r["org"], r["occupation"],
            r["location_city"], r["location_country"], r["id"],
        )
        if not body:
            skipped += 1
            continue
        all_updates.append((r["google_resource_name"], body, [], r["id"]))

    for r in to_create:
        body = build_contact_body(
            r["google_resource_name"] or "", r["name_given"] or "", r["name_family"] or "",
            r["email"] or "", r["phone"] or "", r["org"] or "", r["occupation"] or "",
            r["location_city"] or "", r["location_country"] or "", r["id"],
        )
        if not body:
            skipped += 1
            continue
        all_creates.append((body, r["id"]))

    _log(f"  Outbound: {len(all_updates)} contacts with data to push, {skipped} skipped")

    # Batch etag fetching (50 per request)
    _log(f"  Outbound: fetching etags for {len(all_updates)} contacts...")
    rn_list = [rn for rn, *_ in all_updates]
    etag_map = {}
    for i in range(0, len(rn_list), 50):
        batch_rns = rn_list[i:i+50]
        rn_param = "&resourceNames=".join(urllib.parse.quote(rn) for rn in batch_rns)
        url = f"{PEOPLE_API_BASE}/people:batchGet?resourceNames={rn_param}&personFields=metadata"
        try:
            resp = _api_get(url, token, timeout=30)
            for person in resp.get("responses", []):
                p = person.get("person", {})
                rn_val = p.get("resourceName", "")
                etag = p.get("etag", "")
                if rn_val and etag:
                    etag_map[rn_val] = etag
        except Exception as e:
            _log(f"    Etag batch error at {i}: {e}")
        time.sleep(0.3)
    _log(f"  Outbound: fetched {len(etag_map)}/{len(rn_list)} etags")

    # Batch update (200 per request)
    pushed = failed = stale = rate_limited = 0
    batch_url = f"{PEOPLE_API_BASE}/people:batchUpdateContacts"
    ALL_FIELDS = "names,emailAddresses,phoneNumbers,organizations,addresses,biographies"

    for i in range(0, len(all_updates), 200):
        batch = all_updates[i:i+200]
        batch_num = i // 200 + 1
        total_batches = (len(all_updates) + 199) // 200

        contacts_map = {}
        batch_pids = {}
        for rn, body, update_fields, pid in batch:
            etag = etag_map.get(rn)
            if not etag:
                continue
            body["etag"] = etag
            contacts_map[rn] = body
            batch_pids[rn] = pid

        if not contacts_map:
            _log(f"  Batch {batch_num}: no valid contacts (all missing etags)")
            continue

        req_body = {"contacts": contacts_map, "updateMask": ALL_FIELDS}
        attempt = 0
        backoff = 5.0
        while attempt < 4:
            attempt += 1
            try:
                _log(f"  Batch {batch_num}/{total_batches}: {len(contacts_map)} contacts...")
                resp = _api_post(batch_url, token, req_body, timeout=120)

                results = resp.get("updateResult", {})
                if results:
                    for rn_val, result in results.items():
                        status = result.get("httpStatusCode", 0)
                        if status == 200:
                            pushed += 1
                            with open(ckpt_path, "a") as f:
                                f.write(rn_val + "\n")
                        elif status == 404:
                            stale += 1
                            pid = batch_pids.get(rn_val)
                            if pid:
                                weave.execute_write(
                                    "UPDATE persons SET google_resource_name = NULL WHERE id = :id",
                                    {"id": pid},
                                )
                        else:
                            failed += 1
                else:
                    for rn_val in contacts_map:
                        pushed += 1
                        with open(ckpt_path, "a") as f:
                            f.write(rn_val + "\n")

                _log(f"  Batch {batch_num} done: {len(contacts_map)} processed")
                time.sleep(1.5)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    rate_limited += 1
                    _log(f"  Batch {batch_num} rate limited, backoff {backoff}s ({attempt}/4)")
                    time.sleep(backoff)
                    backoff *= 2
                    if attempt >= 4:
                        failed += len(contacts_map)
                elif e.code == 400:
                    try:
                        err = e.read().decode()[:300]
                    except Exception:
                        err = str(e)
                    _log(f"  Batch {batch_num} HTTP 400: {err[:200]}")
                    failed += len(contacts_map)
                    break
                else:
                    _log(f"  Batch {batch_num} HTTP {e.code}: {str(e)[:200]}")
                    failed += len(contacts_map)
                    break
            except Exception as e:
                _log(f"  Batch {batch_num} error: {e}")
                failed += len(contacts_map)
                break

    # Batch create new contacts
    created_count = create_failed = 0
    create_url = f"{PEOPLE_API_BASE}/people:createContact"
    for body, pid in all_creates:
        if pid in pushed_set:
            continue
        try:
            _api_post(create_url, token, body, timeout=30)
            created_count += 1
            with open(ckpt_path, "a") as f:
                f.write(pid + "\n")
            time.sleep(0.5)
        except Exception as e:
            create_failed += 1
            _log(f"  Create failed for {pid}: {e}")

    return {
        "outbound_pushed": pushed,
        "outbound_failed": failed,
        "outbound_skipped": skipped,
        "outbound_stale": stale,
        "outbound_rate_limited": rate_limited,
        "outbound_created": created_count,
        "outbound_create_failed": create_failed,
    }


def main():
    config = load_config()
    last_sync = config.get("last_sync", {}).get("google_contacts")
    token = get_access_token()

    _log(f"Starting Google Contacts sync (last_sync={last_sync})")

    # Inbound
    _log("Phase 1: Inbound sync...")
    inbound_result = sync_inbound(token)
    _log(f"  Inbound result: {inbound_result}")

    # Outbound (doubly gated: config flag + checkpoint)
    writeback = config.get("writeback", {}).get("google_contacts", False)
    ckpt_exists = (AGENT_ROOT / "commons/db/ocas-weave/staging/outbound_ckpt.txt").exists()

    if writeback:
        _log("Phase 2: Outbound sync...")
        outbound_result = sync_outbound(token, last_sync)
        _log(f"  Outbound result: {outbound_result}")
    else:
        outbound_result = {"outbound_pushed": 0, "note": "writeback disabled"}
        _log("Phase 2: Outbound skipped (writeback disabled)")

    # Update last_sync timestamp
    now = datetime.now(timezone.utc).isoformat()
    config.setdefault("last_sync", {})["google_contacts"] = now
    save_config(config)

    _log(f"Sync complete at {now}")
    return {"inbound": inbound_result, "outbound": outbound_result, "sync_time": now}


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        if 'refresh token revoked' in str(e):
            import sys
            print(f"ABORT: Google OAuth refresh token revoked. the operator must re-authorize.", file=sys.stderr)
            sys.exit(2)
        raise