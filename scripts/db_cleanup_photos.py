#!/usr/bin/env python3
"""Remove googleusercontent photo URL fields from Google Contacts."""
import sys, json, os, time
from pathlib import Path

AGENT_ROOT = Path(os.environ.get("HERMES_HOME") or os.environ.get("OCAS_AGENT_ROOT") or Path.home() / ".hermes")
sys.path.insert(0, str(AGENT_ROOT / "hermes-agent/venv/lib/python3.11/site-packages"))
os.environ.setdefault('HERMES_HOME', str(AGENT_ROOT))

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

with open(AGENT_ROOT / "owner_google_credentials.json") as f:
    token_data = json.load(f)

creds = Credentials(
    token=token_data['token'],
    refresh_token=token_data['refresh_token'],
    token_uri='https://oauth2.googleapis.com/token',
    client_id=token_data['client_id'],
    client_secret=<GOOGLE_OAUTH_CLIENT_SECRET>['client_secret'],
    scopes=token_data.get('scopes', []),
)
if not creds.valid and creds.refresh_token:
    creds.refresh(Request())

service = build('people', 'v1', credentials=creds)

# Scan for contacts with photo fields
page_token = None
photo_field_contacts = []

print("Scanning contacts...")
while True:
    kwargs = dict(
        resourceName='people/me',
        pageSize=100,
        personFields='names,userDefined'
    )
    if page_token:
        kwargs['pageToken'] = page_token
    
    results = service.people().connections().list(**kwargs).execute()
    connections = results.get('connections', [])
    
    for c in connections:
        user_defined = c.get('userDefined', [])
        keep = [ud for ud in user_defined if 'lh3.googleusercontent.com' not in ud.get('value', '')]
        remove = [ud for ud in user_defined if 'lh3.googleusercontent.com' in ud.get('value', '')]
        
        if remove:
            name = 'Unknown'
            if c.get('names'):
                name = c['names'][0].get('displayName', 'Unknown')
            photo_field_contacts.append({
                'resourceName': c['resourceName'],
                'etag': c['etag'],
                'name': name,
                'keep': keep,
            })
    
    page_token = results.get('nextPageToken')
    if not page_token:
        break

print(f"Found {len(photo_field_contacts)} contacts with photo fields to clean")

# Process with throttling - ~0.8s between requests, handle rate limits
success = 0
errors = 0

for i, contact in enumerate(photo_field_contacts):
    for attempt in range(3):
        try:
            # On retry, re-fetch etag
            if attempt > 0:
                person = service.people().get(
                    resourceName=contact['resourceName'],
                    personFields='userDefined'
                ).execute()
                keep = [ud for ud in person.get('userDefined', []) if 'lh3.googleusercontent.com' not in ud.get('value', '')]
                etag = person['etag']
            else:
                keep = contact['keep']
                etag = contact['etag']
            
            body = {'etag': etag, 'userDefined': keep}
            service.people().updateContact(
                resourceName=contact['resourceName'],
                updatePersonFields='userDefined',
                body=body
            ).execute()
            success += 1
            break
        except HttpError as e:
            if e.resp.status == 429:
                print(f"  Rate limited at {i+1}, waiting 65s...")
                time.sleep(65)
                continue
            elif e.resp.status == 400 and 'etag' in str(e):
                if attempt < 2:
                    time.sleep(1)
                    continue
                errors += 1
                print(f"  Etag error on {contact['name']} after retries")
            else:
                errors += 1
                if errors <= 5:
                    print(f"  ERROR on {contact['name']}: {e}")
                break
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  ERROR on {contact['name']}: {e}")
            break
    
    time.sleep(0.8)
    
    if (i + 1) % 25 == 0:
        print(f"  Progress: {i+1}/{len(photo_field_contacts)} ({success} ok, {errors} err)")

print(f"\nDone: {success} updated, {errors} errors out of {len(photo_field_contacts)} total")
