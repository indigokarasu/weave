#!/usr/bin/env python3
"""
Bidirectional Google Contacts sync for Weave.
1. Inbound: Google Contacts → Weave
2. Outbound: Weave → Google Contacts (records modified since last sync)

Uses urllib.request REST calls (not googleapiclient SDK) to avoid silent hangs
in execute_code and background process environments.
"""

import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Paths
AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
DB_PATH = AGENT_ROOT / 'commons/db/ocas-weave/weave.lbug'
CONFIG_PATH = AGENT_ROOT / 'commons/data/ocas-weave/config.json'
# Google Workspace MCP credentials directory
TOKEN_PATH='/root/.google_workspace_mcp/credentials/google-workspace-user.json'
PEOPLE_API_BASE = 'https://people.googleapis.com/v1'

def _log(msg):
    """Flush-aware print for long-running sync output."""
    print(msg, flush=True)

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(config):
    config['updated_at'] = datetime.now(timezone.utc).isoformat()
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

def get_access_token():
    """Refresh OAuth token if needed, return access token string.
    
    Uses urllib.request directly (not google.oauth2 SDK) to avoid silent hangs
    in execute_code and background process environments. The google.auth and
    google.oauth2 packages cause indefinite blocking when imported via
    execute_code — discovered Apr 2026.
    """
    with open(TOKEN_PATH) as f:
        token_data = json.load(f)

    access_token = token_data.get('token', '')
    expiry_str = token_data.get('expiry', '')

    # Check if token is expired
    expired = False
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
            # Ensure expiry is timezone-aware for comparison with timezone-aware now
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            expired = datetime.now(timezone.utc) >= expiry
        except (ValueError, TypeError):
            pass

    if expired and token_data.get('refresh_token'):
        _log('  Token expired, refreshing via urllib...')
        # Use client credentials from env (the working OAuth app) — fall back to file
        client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', token_data.get('client_id', ''))
        client_secret=<GOOGLE_OAUTH_CLIENT_SECRET>('GOOGLE_OAUTH_CLIENT_SECRET', token_data.get('client_secret', ''))
        refresh_data = urllib.parse.urlencode({
            'client_id': client_id,
            'client_secret=<GOOGLE_OAUTH_CLIENT_SECRET>,
            'refresh_token': token_data['refresh_token'],
            'grant_type': 'refresh_token',
        }).encode()

        req = urllib.request.Request(
            'https://oauth2.googleapis.com/token',
            data=refresh_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            new_token = json.loads(resp.read())
            access_token = new_token['access_token']
            token_data['token'] = access_token
            if 'expires_in' in new_token:
                from datetime import timedelta
                token_data['expiry'] = (
                    datetime.now(timezone.utc) + timedelta(seconds=new_token['expires_in'])
                ).isoformat()
            with open(TOKEN_PATH, 'w') as f:
                json.dump(token_data, f, indent=2)
            _log('  Token refreshed successfully')
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            _log(f'  Token refresh failed: {e}')
            # Fall through — return existing token, API calls will fail with 401 if truly invalid

    return access_token

def _api_get(url, token, timeout=30, max_retries=4):
    """GET request to People API, returns parsed JSON. Retries on 429."""
    backoff = 5.0
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries:
                _log(f'  429 rate limited (attempt {attempt}/{max_retries}), backoff {backoff}s')
                time.sleep(backoff)
                backoff *= 2
                continue
            raise

def _api_patch(url, token, body, timeout=30):
    """PATCH request to People API, returns parsed JSON."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='PATCH'
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def _validate_phone(phone):
    """Validate and clean phone numbers. Returns cleaned phone or None if invalid."""
    if not phone:
        return None
    import re
    cleaned = re.sub(r'[^\d+\\(\\)\\-\\. ]', '', phone.strip())
    digits = re.sub(r'\D', '', cleaned)
    if len(digits) < 7 or len(digits) > 15:
        return None
    return cleaned

def sync_inbound(db, token):
    """Pull contacts FROM Google INTO Weave using REST API."""
    import real_ladybug as lb

    conn = lb.Connection(db)
    now = datetime.now(timezone.utc).isoformat()

    # Fetch all Google contacts via REST
    person_fields = 'names,emailAddresses,phoneNumbers,organizations,addresses,urls,biographies,birthdays,relations'
    contacts = []
    page_token = None
    page = 0
    while True:
        page += 1
        url = f'{PEOPLE_API_BASE}/people/me/connections?personFields={person_fields}&pageSize=100&sources=READ_SOURCE_TYPE_CONTACT'
        if page_token:
            url += f'&pageToken={page_token}'
        data = _api_get(url, token)
        connections = data.get('connections', [])
        contacts.extend(connections)
        _log(f'  Inbound: fetched page {page}: {len(connections)} contacts (total: {len(contacts)})')
        page_token = data.get('nextPageToken')
        if not page_token:
            break
        time.sleep(1.0)

    _log(f'  Inbound: total Google contacts fetched: {len(contacts)}')

    # Build lookup maps (read-only pass)
    all_people = list(conn.execute(
        "MATCH (p:Person) RETURN p.id, p.google_resource_name, p.name, p.email, p.phone"
    ))
    rn_map = {}
    email_map = {}
    phone_map = {}
    for row in all_people:
        pid, grn, name, email, phone = row[0], row[1], row[2], row[3], row[4]
        if grn:
            rn_map[grn] = pid
        if email:
            email_map[email.lower()] = pid
        if phone:
            phone_map[phone] = pid

    upserted = enriched = created = skipped = 0
    for c in contacts:
        rn = c.get('resourceName', '')
        name_data = (c.get('names') or [{}])[0] if c.get('names') else {}
        name = name_data.get('displayName', '')
        if not name:
            skipped += 1
            continue

        given = name_data.get('givenName', '')
        family = name_data.get('familyName', '')
        email = (c.get('emailAddresses') or [{}])[0].get('value', '') if c.get('emailAddresses') else ''
        phone = (c.get('phoneNumbers') or [{}])[0].get('value', '') if c.get('phoneNumbers') else ''
        org = (c.get('organizations') or [{}])[0].get('name', '') if c.get('organizations') else ''
        title_val = (c.get('organizations') or [{}])[0].get('title', '') if c.get('organizations') else ''
        city = (c.get('addresses') or [{}])[0].get('city', '') if c.get('addresses') else ''
        country = (c.get('addresses') or [{}])[0].get('countryCode', '') if c.get('addresses') else ''
        notes = (c.get('biographies') or [{}])[0].get('value', '') if c.get('biographies') else ''

        phone = _validate_phone(phone) if phone else ''

        # Match: resource_name → email → phone → new
        pid = rn_map.get(rn) or (email_map.get(email.lower()) if email else None) or (phone_map.get(phone) if phone else None)

        if pid:
            # Gap-fill existing record
            conn.execute("""
                MATCH (p:Person {id: $id})
                SET p.name = CASE WHEN p.name IS NULL OR p.name = '' THEN $name ELSE p.name END,
                    p.name_given = CASE WHEN p.name_given IS NULL OR p.name_given = '' THEN $given ELSE p.name_given END,
                    p.name_family = CASE WHEN p.name_family IS NULL OR p.name_family = '' THEN $family ELSE p.name_family END,
                    p.email = CASE WHEN p.email IS NULL OR p.email = '' THEN $email ELSE p.email END,
                    p.phone = CASE WHEN p.phone IS NULL OR p.phone = '' THEN $phone ELSE p.phone END,
                    p.org = CASE WHEN p.org IS NULL OR p.org = '' THEN $org ELSE p.org END,
                    p.occupation = CASE WHEN p.occupation IS NULL OR p.occupation = '' THEN $title ELSE p.occupation END,
                    p.location_city = CASE WHEN p.location_city IS NULL OR p.location_city = '' THEN $city ELSE p.location_city END,
                    p.location_country = CASE WHEN p.location_country IS NULL OR p.location_country = '' THEN $country ELSE p.location_country END,
                    p.google_resource_name = CASE WHEN p.google_resource_name IS NULL THEN $rn ELSE p.google_resource_name END,
                    p.record_time = $now
            """, {
                "id": pid, "name": name, "given": given, "family": family,
                "email": email, "phone": phone, "org": org, "title": title_val,
                "city": city, "country": country, "rn": rn, "now": now
            })
            enriched += 1
        else:
            pid = str(uuid.uuid4())
            conn.execute("""
                CREATE (p:Person {
                    id: $id, name: $name, name_given: $given, name_family: $family,
                    email: $email, phone: $phone, location_city: $city, location_country: $country,
                    occupation: $title, org: $org,
                    google_resource_name: $rn, source_type: 'imported', source_ref: $rn,
                    confidence: 0.8, record_time: $now
                })
            """, {
                "id": pid, "name": name, "given": given, "family": family,
                "email": email, "phone": phone, "org": org, "title": title_val,
                "city": city, "country": country, "rn": rn, "now": now
            })
            created += 1
        upserted += 1
        if upserted % 100 == 0:
            _log(f"  Inbound progress: {upserted}/{len(contacts)} processed")

    return {"inbound_upserted": upserted, "inbound_enriched": enriched, "inbound_created": created, "inbound_skipped": skipped}

def _api_post(url, token, body, timeout=30):
    """POST request to People API, returns parsed JSON."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def sync_outbound(db, token, last_sync_at):
    """Push Weave changes TO Google Contacts via individual PATCH calls.
    Each contact: GET etag + PATCH with data (2 API calls per contact).
    On 429: exponential backoff starting at 5s. On 404: clears stale google_resource_name.
    On 400 FAILED_PRECONDITION: re-fetch etag and retry once.
    """
    import real_ladybug as lb

    conn = lb.Connection(db)

    # Checkpoint file for resumable outbound sync
    ckpt_path = AGENT_ROOT / 'commons/db/ocas-weave/staging/outbound_ckpt.txt'
    pushed_set = set()
    if ckpt_path.exists():
        pushed_set = set(l for l in ckpt_path.read_text().strip().split('\n') if l)
        _log(f'  Outbound: resuming from checkpoint ({len(pushed_set)} already pushed)')

    # Find contacts with Fact-sourced URLs (LinkedIn, etc.)
    linkedin_rows = list(conn.execute("""
        MATCH (p:Person)-[:HasFact]->(f:Fact)
        WHERE p.record_time > $ts
          AND f.predicate = 'scout_verification_note'
          AND f.value CONTAINS 'linkedin.com/'
        RETURN p.google_resource_name, p.id, f.value
    """, {"ts": last_sync_at}))
    linkedin_map = {}
    for rn, pid, note in linkedin_rows:
        import re
        url_match = re.search(r'(https?://[^\s]*linkedin\.com/in/[^\s]+)', note)
        if url_match:
            linkedin_map[rn or pid] = url_match.group(1)

    # Find records modified since last sync.
    # Two categories:
    # 1. Has google_resource_name → PATCH (update existing)
    # 2. No google_resource_name but has data → POST (create new in Google)
    # Exclude source_type='imported' to avoid echo-looping inbound-enriched records.
    rows = list(conn.execute("""
        MATCH (p:Person)
        WHERE p.record_time > $ts
          AND (p.source_type IS NULL OR p.source_type <> 'imported')
        RETURN p.google_resource_name, p.name_given, p.name_family,
               p.email, p.phone, p.org, p.occupation, p.location_city, p.location_country,
               p.id
    """, {"ts": last_sync_at}))

    to_update = [r for r in rows if r[0]]  # Has google_resource_name → PATCH
    to_create = [r for r in rows if not r[0]]  # No google_resource_name → POST
    _log(f'  Outbound: {len(to_update)} contacts to update, {len(to_create)} contacts to create')

    if not to_update and not to_create:
        return {"outbound_pushed": 0, "outbound_failed": 0, "outbound_skipped": 0, "outbound_stale": 0, "outbound_rate_limited": 0, "outbound_created": 0}

    # Build contact updates list (existing contacts to PATCH)
    all_updates = []  # List of (rn, body, update_fields, pid)
    # Build contact creates list (new contacts to POST)
    all_creates = []  # List of (body, pid)
    skipped = 0

    def build_contact_body(rn, given, family, email, phone, org, title, city, country, pid):
        """Build a Google Contacts API body dict from Weave person fields."""
        phone_clean = _validate_phone(phone)

        body = {}
        if given or family:
            body["names"] = [{"givenName": given or "", "familyName": family or ""}]
        if email:
            email_entry = {"value": email}
            if email.lower().endswith('@gmail.com'):
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
        # Add LinkedIn URL if available (from Fact nodes)
        linkedin_url = linkedin_map.get(rn, linkedin_map.get(pid))
        if linkedin_url:
            body["urls"] = [
                {
                    "value": linkedin_url,
                    "formattedType": "LinkedIn"
                }
            ]
        # Skip notes - do NOT write to Google Contacts biographies field
        return body

    for rn, given, family, email, phone, org, title, city, country, pid in to_update:
        body = build_contact_body(rn, given, family, email, phone, org, title, city, country, pid)
        if not body:
            skipped += 1
            continue
        all_updates.append((rn, body, [], pid))

    for rn, given, family, email, phone, org, title, city, country, pid in to_create:
        body = build_contact_body(rn, given, family, email, phone, org, title, city, country, pid)
        if not body:
            skipped += 1
            continue
        all_creates.append((body, pid))

    _log(f'  Outbound: {len(all_updates)} contacts with data to push, {skipped} skipped')

    # === SAFEGUARD: Snapshot current Google state before any modifications ===
    try:
        from contact_snapshots import create_snapshot
        rn_list_for_snapshot = [rn for rn, *_ in all_updates]
        person_id_map = {rn: pid for rn, _, _, pid in all_updates}
        sync_id = f"outbound_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        snapshot_file = create_snapshot(rn_list_for_snapshot, sync_id=sync_id, person_ids=person_id_map)
        _log(f'  Outbound: snapshot saved to {snapshot_file}')
    except Exception as e:
        _log(f'  Outbound: WARNING - snapshot failed: {e}. Proceeding anyway.')
        snapshot_file = None

    # Batch etag fetching using people:batchGet (50 per request)
    _log(f'  Outbound: fetching etags for {len(all_updates)} contacts...')
    rn_list = [rn for rn, *_ in all_updates]
    etag_map = {}
    for i in range(0, len(rn_list), 50):
        batch_rns = rn_list[i:i+50]
        rn_param = '&resourceNames='.join(urllib.parse.quote(rn) for rn in batch_rns)
        url = f'{PEOPLE_API_BASE}/people:batchGet?resourceNames={rn_param}&personFields=metadata'
        try:
            resp = _api_get(url, token, timeout=30)
            for person in resp.get('responses', []):
                p = person.get('person', {})
                rn = p.get('resourceName', '')
                etag = p.get('etag', '')
                if rn and etag:
                    etag_map[rn] = etag
        except Exception as e:
            _log(f'    Etag batch error at {i}: {e}')
        time.sleep(0.3)
    _log(f'  Outbound: fetched {len(etag_map)}/{len(rn_list)} etags')

    # Batch update using people:batchUpdateContacts (200 per request)
    pushed = failed = stale = rate_limited = 0
    batch_url = f'{PEOPLE_API_BASE}/people:batchUpdateContacts'
    ALL_FIELDS = "names,emailAddresses,phoneNumbers,organizations,addresses,biographies"

    for i in range(0, len(all_updates), 200):
        batch = all_updates[i:i+200]
        batch_num = i//200 + 1
        total_batches = (len(all_updates) + 199)//200

        # Build contacts map with etags
        contacts_map = {}
        batch_pids = {}
        no_etag = 0
        for rn, body, update_fields, pid in batch:
            etag = etag_map.get(rn)
            if not etag:
                no_etag += 1
                continue
            body['etag'] = etag
            contacts_map[rn] = body
            batch_pids[rn] = pid

        if not contacts_map:
            _log(f'  Batch {batch_num}: no valid contacts (all missing etags)')
            continue

        req_body = {"contacts": contacts_map, "updateMask": ALL_FIELDS}
        attempt = 0
        backoff = 5.0
        while attempt < 4:
            attempt += 1
            try:
                _log(f'  Batch {batch_num}/{total_batches}: {len(contacts_map)} contacts...')
                resp = _api_post(batch_url, token, req_body, timeout=120)

                # Process results (API may return empty body on success)
                results = resp.get("updateResult", {})
                if results:
                    for rn, result in results.items():
                        status = result.get("httpStatusCode", 0)
                        if status == 200:
                            pushed += 1
                            with open(ckpt_path, 'a') as f:
                                f.write(rn + '\n')
                        elif status == 404:
                            stale += 1
                            pid = batch_pids.get(rn)
                            if pid:
                                conn.execute("MATCH (p:Person {id: $id}) SET p.google_resource_name = null", {"id": pid})
                        else:
                            failed += 1
                else:
                    # Empty response = success for all
                    for rn in contacts_map:
                        pushed += 1
                        with open(ckpt_path, 'a') as f:
                            f.write(rn + '\n')

                _log(f'  Batch {batch_num} done: {len(contacts_map)} processed')
                time.sleep(1.5)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    rate_limited += 1
                    _log(f'  Batch {batch_num} rate limited, backoff {backoff}s ({attempt}/4)')
                    time.sleep(backoff)
                    backoff *= 2
                    if attempt >= 4:
                        failed += len(contacts_map)
                elif e.code == 400:
                    try:
                        err = e.read().decode()[:300]
                    except:
                        err = str(e)
                    _log(f'  Batch {batch_num} HTTP 400: {err[:200]}')
                    failed += len(contacts_map)
                    break
                else:
                    try:
                        err = e.read().decode()[:300]
                    except:
                        err = str(e)
                    _log(f'  Batch {batch_num} HTTP {e.code}: {err[:200]}')
                    failed += len(contacts_map)
                    break
            except Exception as e:
                _log(f'  Batch {batch_num} error: {e}')
                failed += len(contacts_map)
                break

    if ckpt_path.exists() and len(pushed_set) == 0 and pushed > 0:
        pass  # Keep checkpoint for resume-ability but don't delete here

    return {
        "outbound_pushed": pushed,
        "outbound_failed": failed,
        "outbound_skipped": skipped,
        "outbound_stale": stale,
        "outbound_rate_limited": rate_limited,
        "outbound_created": len(all_creates)
    }


def main():
    _log("=" * 60)
    _log("Weave Google Contacts Sync")
    _log(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    _log("=" * 60)

    token = get_access_token()
    if not token:
        _log("ERROR: Failed to get access token")
        sys.exit(1)

    import real_ladybug as lb
    _log("Opening Weave database...")
    db = lb.Database(str(DB_PATH))

    config = load_config()
    last_sync_at = config.get("last_sync", {}).get("google_contacts")

    _log("\n[Inbound] Google Contacts → Weave...")
    try:
        result_in = sync_inbound(db, token)
        _log(f"Inbound: upserted={result_in.get('inbound_upserted', '?')} enriched={result_in.get('inbound_enriched', '?')} created={result_in.get('inbound_created', '?')} skipped={result_in.get('inbound_skipped', '?')}")
    except Exception as e:
        _log(f"ERROR inbound: {e}")
        import traceback
        _log(traceback.format_exc())

    _log("\n[Outbound] Weave → Google Contacts...")
    writeback_enabled = config.get('writeback', {}).get('google_contacts', False)
    if not writeback_enabled:
        _log("Outbound SKIPPED: writeback.google_contacts is false in config (set true to enable outbound)")
        result_out = {
            'outbound_pushed': 0,
            'outbound_failed': 0,
            'outbound_skipped': 0,
            'outbound_stale': 0,
            'outbound_rate_limited': 0,
            'outbound_created': 0
        }
    else:
        try:
            result_out = sync_outbound(db, token, last_sync_at)
            _log(f"Outbound: pushed={result_out.get('outbound_pushed', '?')} failed={result_out.get('outbound_failed', '?')} skipped={result_out.get('outbound_skipped', '?')} stale={result_out.get('outbound_stale', '?')} rate_limited={result_out.get('outbound_rate_limited', '?')} created={result_out.get('outbound_created', '?')}")
        except Exception as e:
            _log(f"ERROR outbound: {e}")
            import traceback
            _log(traceback.format_exc())
            result_out = {
                'outbound_pushed': 0,
                'outbound_failed': 0,
                'outbound_skipped': 0,
                'outbound_stale': 0,
                'outbound_rate_limited': 0,
                'outbound_created': 0
            }

    now = datetime.now(timezone.utc).isoformat()
    if "last_sync" not in config:
        config["last_sync"] = {}
    config["last_sync"]["google_contacts"] = now
    config["updated_at"] = now
    save_config(config)

    conn = lb.Connection(db)
    r = conn.execute("MATCH (p:Person) RETURN count(p)")
    people_count = r.get_all()[0][0]
    r2 = conn.execute("MATCH (p:Person) WHERE p.google_resource_name IS NOT NULL RETURN count(p)")
    google_count = r2.get_all()[0][0]
    _log(f"\nDatabase: {people_count} people ({google_count} with Google resource names)")
    _log(f"Sync completed. Last sync: {now}")
    _log("=" * 60)


if __name__ == "__main__":
    main()
