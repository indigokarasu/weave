#!/usr/bin/env python3
"""
Contact Snapshot System for Weave ↔ Google Contacts Sync

Safeguard: Before any outbound sync modifies Google Contacts, a snapshot
of each contact's current state is stored locally. This allows:
1. Rollback if bad data is pushed
2. Audit trail of all changes
3. Diff analysis between snapshots

Snapshots are stored as JSONL files in:
  ~/.hermes/commons/db/ocas-weave/snapshots/YYYY-MM-DD_HH-MM-SS.jsonl

Each snapshot entry:
{
  "timestamp": "2026-04-21T12:00:00+00:00",
  "sync_id": "outbound_20260421_120000",
  "resource_name": "people/c123",
  "person_id": "uuid",
  "person": {
    "names": [...],
    "emailAddresses": [...],
    "phoneNumbers": [...],
    "organizations": [...],
    "addresses": [...],
    "biographies": [...]
  },
  "etag": "..."
}
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Paths
AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
SNAPSHOTS_DIR = AGENT_ROOT / 'commons/db/ocas-weave/snapshots'
TOKEN_PATH='[Google OAuth credentials]google-workspace-user.json'
PEOPLE_API_BASE = 'https://people.googleapis.com/v1'

# Fields to snapshot (all mutable contact fields)
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,addresses,biographies"


def get_access_token():
    """Get valid Google OAuth token, refreshing if needed."""
    with open(TOKEN_PATH) as f:
        token_data = json.load(f)
    
    token = token_data.get('token') or token_data.get('access_token')
    expiry = token_data.get('expiry')
    
    if expiry:
        if isinstance(expiry, (int, float)):
            exp_dt = datetime.fromtimestamp(expiry, tz=timezone.utc)
        else:
            exp_dt = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) >= exp_dt:
            refresh_token = token_data.get('refresh_token')
            client_id = token_data.get('client_id') or '<GOOGLE_OAUTH_CLIENT_ID>.apps.googleusercontent.com'
            client_secret=<GOOGLE_OAUTH_CLIENT_SECRET>('client_secret', '')
            
            data = urllib.parse.urlencode({
                'client_id': client_id,
                'client_secret=<GOOGLE_OAUTH_CLIENT_SECRET>,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token'
            }).encode()
            
            req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data,
                                        headers={'Content-Type': 'application/x-www-form-urlencoded'})
            resp = urllib.request.urlopen(req, timeout=30)
            new_token = json.loads(resp.read())
            token = new_token['access_token']
            
            token_data['token'] = token
            if 'expires_in' in new_token:
                from datetime import timedelta
                token_data['expiry'] = (datetime.now(timezone.utc) + timedelta(seconds=new_token['expires_in'])).isoformat()
            
            with open(TOKEN_PATH, 'w') as f:
                json.dump(token_data, f, indent=2)
    
    return token


def _api_get(url, token, timeout=30):
    """GET request to People API."""
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _api_post(url, token, body, timeout=30):
    """POST request to People API."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def create_snapshot(resource_names, sync_id=None, person_ids=None):
    """
    Snapshot current state of Google Contacts before modification.
    
    Args:
        resource_names: List of Google resource names (e.g., ['people/c123', ...])
        sync_id: Identifier for this sync operation (auto-generated if None)
        person_ids: Dict mapping resource_name -> Weave person_id (optional)
    
    Returns:
        Path to the snapshot file
    """
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    
    if sync_id is None:
        sync_id = f"outbound_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    timestamp = datetime.now(timezone.utc).isoformat()
    snapshot_file = SNAPSHOTS_DIR / f"{sync_id}.jsonl"
    
    token = get_access_token()
    
    # Fetch contacts in batches of 50 (people:batchGet limit)
    snapshot_count = 0
    error_count = 0
    
    print(f"Creating snapshot: {sync_id}")
    print(f"  Contacts to snapshot: {len(resource_names)}")
    
    with open(snapshot_file, 'w') as f:
        for i in range(0, len(resource_names), 50):
            batch_rns = resource_names[i:i+50]
            rn_param = '&resourceNames='.join(urllib.parse.quote(rn) for rn in batch_rns)
            url = f'{PEOPLE_API_BASE}/people:batchGet?resourceNames={rn_param}&personFields={PERSON_FIELDS}'
            
            attempt = 0
            backoff = 5.0
            while attempt < 3:
                attempt += 1
                try:
                    resp = _api_get(url, token, timeout=30)
                    responses = resp.get('responses', [])
                    
                    for item in responses:
                        person = item.get('person', {})
                        http_status = item.get('httpStatusCode', 0)
                        rn = person.get('resourceName', '')
                        
                        if http_status != 200 or not rn:
                            error_count += 1
                            continue
                        
                        # Extract only the fields we care about
                        snap = {
                            'timestamp': timestamp,
                            'sync_id': sync_id,
                            'resource_name': rn,
                            'person_id': person_ids.get(rn) if person_ids else None,
                            'etag': person.get('etag', ''),
                            'person': {}
                        }
                        
                        for field in ['names', 'emailAddresses', 'phoneNumbers', 
                                      'organizations', 'addresses', 'biographies']:
                            values = person.get(field, [])
                            if values:
                                snap['person'][field] = values
                        
                        f.write(json.dumps(snap) + '\n')
                        snapshot_count += 1
                    
                    break
                    
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        print(f"    Rate limited, backing off {backoff}s")
                        time.sleep(backoff)
                        backoff *= 2
                    else:
                        print(f"    HTTP {e.code} on batch {i//50 + 1}")
                        error_count += len(batch_rns)
                        break
                except Exception as e:
                    print(f"    Error on batch {i//50 + 1}: {e}")
                    error_count += len(batch_rns)
                    break
            
            time.sleep(0.3)  # Stay under rate limit
            
            if (i + 50) % 200 == 0:
                print(f"    Snapshotted: {min(i + 50, len(resource_names))}/{len(resource_names)}")
    
    print(f"  Snapshot complete: {snapshot_count} contacts, {error_count} errors")
    print(f"  File: {snapshot_file}")
    
    return snapshot_file


def load_snapshot(snapshot_file):
    """Load a snapshot file and return list of snapshot entries."""
    entries = []
    with open(snapshot_file) as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line.strip()))
    return entries


def list_snapshots(limit=10):
    """List available snapshots, newest first."""
    if not SNAPSHOTS_DIR.exists():
        return []
    
    files = sorted(SNAPSHOTS_DIR.glob('*.jsonl'), reverse=True)[:limit]
    result = []
    for f in files:
        # Read first line to get sync_id and timestamp
        with open(f) as fh:
            first = fh.readline().strip()
            if first:
                entry = json.loads(first)
                # Count lines
                fh.seek(0)
                count = sum(1 for _ in fh)
                result.append({
                    'file': str(f),
                    'sync_id': entry.get('sync_id', ''),
                    'timestamp': entry.get('timestamp', ''),
                    'contacts': count
                })
    return result


def restore_from_snapshot(snapshot_file, dry_run=True):
    """
    Restore Google Contacts from a snapshot.
    
    Pushes the snapshot data back to Google Contacts, restoring
    each contact to its snapshotted state.
    
    Args:
        snapshot_file: Path to snapshot JSONL file
        dry_run: If True, only show what would be restored
    
    Returns:
        Dict with restore stats
    """
    entries = load_snapshot(snapshot_file)
    
    if not entries:
        print(f"No entries in snapshot: {snapshot_file}")
        return {"restored": 0, "failed": 0, "skipped": 0}
    
    print(f"Restore {'(DRY RUN) ' if dry_run else ''}from: {snapshot_file}")
    print(f"  Entries: {len(entries)}")
    
    if dry_run:
        # Show what would be restored
        for entry in entries[:5]:
            rn = entry['resource_name']
            fields = list(entry['person'].keys())
            print(f"    {rn}: {fields}")
        if len(entries) > 5:
            print(f"    ... and {len(entries) - 5} more")
        return {"would_restore": len(entries)}
    
    token = get_access_token()
    
    # Use BatchUpdateContacts to restore
    batch_url = f'{PEOPLE_API_BASE}/people:batchUpdateContacts'
    ALL_FIELDS = PERSON_FIELDS
    
    restored = failed = 0
    
    for i in range(0, len(entries), 200):
        batch = entries[i:i+200]
        batch_num = i//200 + 1
        total_batches = (len(entries) + 199)//200
        
        # Build contacts map with etags
        contacts_map = {}
        for entry in batch:
            rn = entry['resource_name']
            person = entry['person']
            etag = entry.get('etag', '')
            
            if not person:
                continue
            
            if etag:
                person['etag'] = etag
            contacts_map[rn] = person
        
        if not contacts_map:
            continue
        
        req_body = {"contacts": contacts_map, "updateMask": ALL_FIELDS}
        
        attempt = 0
        backoff = 5.0
        while attempt < 4:
            attempt += 1
            try:
                print(f"  Batch {batch_num}/{total_batches}: restoring {len(contacts_map)} contacts...")
                resp = _api_post(batch_url, token, req_body, timeout=120)
                
                results = resp.get("updateResult", {})
                if results:
                    for rn, result in results.items():
                        status = result.get("httpStatusCode", 0)
                        if status == 200:
                            restored += 1
                        else:
                            failed += 1
                else:
                    # Empty response = success
                    restored += len(contacts_map)
                
                print(f"    Done: {len(contacts_map)} processed")
                time.sleep(1.5)
                break
                
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print(f"    Rate limited, backoff {backoff}s ({attempt}/4)")
                    time.sleep(backoff)
                    backoff *= 2
                    if attempt >= 4:
                        failed += len(contacts_map)
                else:
                    try:
                        err = e.read().decode()[:200]
                    except:
                        err = str(e)
                    print(f"    HTTP {e.code}: {err}")
                    failed += len(contacts_map)
                    break
            except Exception as e:
                print(f"    Error: {e}")
                failed += len(contacts_map)
                break
    
    print(f"\n  Restored: {restored}")
    print(f"  Failed: {failed}")
    
    return {"restored": restored, "failed": failed}


def diff_snapshot(snapshot_file_a, snapshot_file_b):
    """
    Compare two snapshots and show differences.
    
    Useful for seeing what changed between syncs.
    """
    entries_a = {e['resource_name']: e for e in load_snapshot(snapshot_file_a)}
    entries_b = {e['resource_name']: e for e in load_snapshot(snapshot_file_b)}
    
    changes = []
    
    for rn in set(list(entries_a.keys()) + list(entries_b.keys())):
        a = entries_a.get(rn)
        b = entries_b.get(rn)
        
        if a and not b:
            changes.append({'resource_name': rn, 'type': 'removed_in_b'})
        elif b and not a:
            changes.append({'resource_name': rn, 'type': 'added_in_b'})
        elif a and b:
            # Compare fields
            field_diffs = {}
            for field in ['names', 'emailAddresses', 'phoneNumbers', 
                          'organizations', 'addresses', 'biographies']:
                val_a = a['person'].get(field, [])
                val_b = b['person'].get(field, [])
                if val_a != val_b:
                    field_diffs[field] = {'before': val_a, 'after': val_b}
            
            if field_diffs:
                changes.append({
                    'resource_name': rn,
                    'type': 'modified',
                    'fields': field_diffs
                })
    
    return changes


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 weave_contact_snapshots.py list")
        print("  python3 weave_contact_snapshots.py snapshot <resource_name1> [resource_name2] ...")
        print("  python3 weave_contact_snapshots.py restore <snapshot_file> [--dry-run]")
        print("  python3 weave_contact_snapshots.py diff <snapshot_a> <snapshot_b>")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == 'list':
        snapshots = list_snapshots()
        if not snapshots:
            print("No snapshots found")
        for s in snapshots:
            print(f"  {s['sync_id']} | {s['timestamp']} | {s['contacts']} contacts | {s['file']}")
    
    elif cmd == 'snapshot':
        if len(sys.argv) < 3:
            print("Usage: python3 weave_contact_snapshots.py snapshot <rn1> <rn2> ...")
            sys.exit(1)
        rns = sys.argv[2:]
        create_snapshot(rns)
    
    elif cmd == 'restore':
        if len(sys.argv) < 3:
            print("Usage: python3 weave_contact_snapshots.py restore <snapshot_file> [--dry-run]")
            sys.exit(1)
        snapshot_file = sys.argv[2]
        dry_run = '--dry-run' in sys.argv
        restore_from_snapshot(snapshot_file, dry_run=dry_run)
    
    elif cmd == 'diff':
        if len(sys.argv) < 4:
            print("Usage: python3 weave_contact_snapshots.py diff <snapshot_a> <snapshot_b>")
            sys.exit(1)
        changes = diff_snapshot(sys.argv[2], sys.argv[3])
        for c in changes:
            print(f"  {c['resource_name']}: {c['type']}")
            if 'fields' in c:
                for field, vals in c['fields'].items():
                    print(f"    {field}: {vals['before']} → {vals['after']}")
    
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
