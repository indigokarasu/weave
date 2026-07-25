# Google Token Diagnostics

Complete diagnostic workflow for Google OAuth token issues: scope verification, refresh token validity testing, and TOKEN_PATH verification.

## Quick Checks

### 1. Verify token file scopes

```bash
python3 -c "import json; td=json.load(open('the Google OAuth credential file at<user-google-email>.json')); print(td.get('scopes', []))"
```

Required scopes for Weave sync:
- `https://www.googleapis.com/auth/contacts` (or short `contacts`)
- `contacts.readonly`
- `contacts.other.readonly`

The full URI scope `https://www.googleapis.com/auth/contacts` is equivalent to the short `contacts` scope. Accept either form.

### 2. Test refresh token validity

```python
import json, urllib.request, urllib.parse
with open('the Google OAuth credential file at<user-google-email>.json') as f:
    td = json.load(f)
req = urllib.request.Request(
    'https://oauth2.googleapis.com/token',
    data=urllib.parse.urlencode({
        'client_id': td['client_id'],
        'client_secret': td['client_secret'],
        'refresh_token': td['refresh_token'],
        'grant_type': 'refresh_token'
    }).encode(),
    headers={'Content-Type': 'application/x-www-form-urlencoded'}
)
try:
    resp = urllib.request.urlopen(req, timeout=30)
    print('Refresh OK')
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f'HTTP {e.code}: {body}')  # Look for invalid_grant
```

### 3. Check which token file the script reads

```bash
grep TOKEN_PATH {skill_root}/scripts/google_sync.py
```

Correct path: `TOKEN_PATH = 'the Google OAuth credential file at<user-google-email>.json'`

### 4. Verify the script parses correctly

```bash
python3 -c "import ast; ast.parse(open('{skill_root}/scripts/google_sync.py').read()); print('OK')"
```

## Full Diagnostic Workflow (Wrong Token File or Dead Refresh Token)

Two distinct failure modes:

1. **Wrong file path**: Script points to a token file lacking `contacts` scope
2. **Dead refresh token**: Token file has correct scopes but the refresh token itself is expired/revoked (HTTP 400 `invalid_grant` — permanently dead)

**Symptom**: Script fails with "Token refresh failed: HTTP Error 400: Bad Request" then 401 Unauthorized on the People API call.

### Diagnosis Steps

1. Check which file the script reads: `grep TOKEN_PATH {skill_root}/scripts/google_sync.py`
2. Verify the token file's scopes (see Quick Check #1 above)
3. Test refresh token validity (see Quick Check #2 above)
4. Check alternate token file if exists

### Fixes

- **Wrong file**: Patch `TOKEN_PATH` to `the Google OAuth credential file at<user-google-email>.json`
- **Dead refresh token**: Full re-auth required with `access_type=offline&prompt=consent`
- **Both problematic**: Full re-auth required regardless

## Token Refresh Mid-Run (Silent Refresh Failure)

The script's `get_access_token()` can fail silently — the refresh call throws an exception that gets caught and logged to stdout (buffered 90-120s), causing the script to fall through and return the expired token. Inbound succeeds (token was still valid) but outbound fails with HTTP 401 on most contacts.

**Symptom**: Pushed ~118, Failed ~463, all 401s.

**Fix**: Manually refresh the token before retrying:

```python
python3 -c "
import json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
with open('the Google OAuth credential file at<user-google-email>.json') as f:
    td = json.load(f)
resp = urllib.request.urlopen(urllib.request.Request(
    'https://oauth2.googleapis.com/token',
    data=urllib.parse.urlencode({
        'client_id': td['client_id'],
        'client_secret': td['client_secret'],
        'refresh_token': td['refresh_token'],
        'grant_type': 'refresh_token'
    }).encode()))
new = json.loads(resp.read())
td['token'] = new['access_token']
td['expiry'] = (datetime.now(timezone.utc) + timedelta(seconds=new['expires_in'])).isoformat()
with open('the Google OAuth credential file at<user-google-email>.json', 'w') as f:
    json.dump(td, f, indent=2)
print('Token refreshed, expires:', td['expiry'])
"
```

Then re-run the sync script. The checkpoint system (`staging/outbound_ckpt.txt`) ensures the retry picks up where it left off — no duplicate pushes.

## Fixing TOKEN_PATH Corruption

When TOKEN_PATH is truly corrupted (e.g., `***`, `<fs-root>/...json` truncated path, or invalid syntax), `patch` tool and `sed` may fail due to special characters. Use Python with regex:

```python
import re
with open('{skill_root}/scripts/google_sync.py', 'rb') as f:
    content = f.read()
new_content = re.sub(
    rb'TOKEN_PATH\s*=\s*"[^"]*"',
    rb'TOKEN_PATH="the Google OAuth credential file at<user-google-email>.json"',
    content
)
with open('{skill_root}/scripts/google_sync.py', 'wb') as f:
    f.write(new_content)
```

Verify the fix using byte-level checks (tool output may truncate long paths):
- Hexdump check: `hexdump -C {skill_root}/scripts/google_sync.py | grep -A1 TOKEN_PATH`
- Python byte check: `python3 -c "with open('script.py', 'rb') as f: c=f.read(); idx=c.find(b'TOKEN_PATH'); print(c[idx:idx+60])"`

## Tool Output Truncation Warning

`read_file`, `terminal`, and `execute_code` tools may truncate long paths in their output (e.g., `the Google OAuth credential file at<user-google-email>.json` → `<fs-root>/...json`). This is a **display artifact only** — the actual file content is usually correct. Verify with raw file reads before attempting fixes. **Never use truncated tool output to write files**, as this can persist corruption.