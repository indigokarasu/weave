# Google Token Quick Check

Pre-flight token validation script to run before sync. Catches dead refresh tokens before attempting sync.

## Pre-Sync Check

Run before any `weave.sync.google-contacts` or cron sync invocation:

```python
python3 -c "
import json, urllib.request, urllib.parse, sys
from datetime import datetime, timezone

TOKEN_PATH = 'the Google OAuth credential file at<user-google-email>.json'
REQUIRED_SCOPES = {'contacts', 'https://www.googleapis.com/auth/contacts'}

with open(TOKEN_PATH) as f:
    td = json.load(f)

# Check scopes
scopes = set(td.get('scopes', []))
has_contacts = bool(scopes & REQUIRED_SCOPES)
print(f'Scopes OK: {has_contacts} (found: {scopes})')

# Check refresh token presence
has_refresh = 'refresh_token' in td
print(f'Has refresh_token: {has_refresh}')

if not has_contacts or not has_refresh:
    print('FAIL: Missing scopes or refresh token')
    sys.exit(1)

# Test refresh
try:
    resp = urllib.request.urlopen(urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=urllib.parse.urlencode({
            'client_id': td['client_id'],
            'client_secret': td['client_secret'],
            'refresh_token': td['refresh_token'],
            'grant_type': 'refresh_token'
        }).encode()), timeout=30)
    new = json.loads(resp.read())
    print(f'Refresh OK, expires_in: {new.get(\"expires_in\", \"?\")}s')
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f'FAIL: HTTP {e.code}: {body}')
    sys.exit(1)
"
```

Exit code 0 = token is healthy. Exit code 1 = fix token before syncing.
