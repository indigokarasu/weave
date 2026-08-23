#!/usr/bin/env python3
"""
Shared Google API helpers for Weave scripts.
Import from here instead of duplicating auth + API call logic.
"""
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import os

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 google_api.py")
    sys.exit(0)



CREDS_DIR = os.environ.get("WORKSPACE_MCP_CREDENTIALS_DIR", os.path.join(os.path.expanduser("~"), ".google_workspace_mcp", "credentials"))
OPERATOR_EMAIL = os.environ.get("OCAS_OPERATOR_EMAIL", "operator_email")
TOKEN_PATH = Path(CREDS_DIR) / f"{OPERATOR_EMAIL}.json"
PEOPLE_API_BASE = 'https://people.googleapis.com/v1'


def get_access_token():
    """Get valid Google OAuth token, refreshing if needed."""
    with open(TOKEN_PATH) as f:
        token_data = json.load(f)
    token = token_data.get('token', '')
    expiry = token_data.get('expiry', '')
    if expiry:
        try:
            if isinstance(expiry, (int, float)):
                exp_dt = datetime.fromtimestamp(expiry, tz=timezone.utc)
            else:
                exp_dt = datetime.fromisoformat(str(expiry).replace('Z', '+00:00'))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp_dt and token_data.get('refresh_token'):
                data = urllib.parse.urlencode({
                    'client_id': token_data.get('client_id', ''),
                    'client_secret': token_data.get('client_secret', ''),
                    'refresh_token': token_data['refresh_token'],
                    'grant_type': 'refresh_token',
                }).encode()
                req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data,
                                            headers={'Content-Type': 'application/x-www-form-urlencoded'})
                resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
                token = resp['access_token']
                token_data['token'] = token
                if 'expires_in' in resp:
                    token_data['expiry'] = (datetime.now(timezone.utc) + timedelta(seconds=resp['expires_in'])).isoformat()
                with open(TOKEN_PATH, 'w') as f:
                    json.dump(token_data, f, indent=2)
        except Exception as e:
            import sys
            print(f"WARNING: token refresh failed: {e}", file=sys.stderr)
            if hasattr(e, 'read'):
                try:
                    body = e.read().decode()
                    print(f"  Response body: {body}", file=sys.stderr)
                    # Re-raise on invalid_grant — the refresh token is revoked
                    if 'invalid_grant' in body:
                        raise RuntimeError(f"Google OAuth refresh token revoked: {body}") from e
                except RuntimeError:
                    raise
                except Exception:
                    pass
    return token


def api_get(url, token, timeout=30, max_retries=4):
    """GET request to People API with retry on 429."""
    backoff = 5.0
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries:
                import time
                time.sleep(backoff)
                backoff *= 2
                continue
            raise


def api_post(url, token, body, timeout=30):
    """POST request to People API."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                                 method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def api_patch(url, token, body, timeout=30):
    """PATCH request to People API."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                                 method='PATCH')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())