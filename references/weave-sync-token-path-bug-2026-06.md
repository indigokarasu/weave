# Weave Sync Token Path Bug — June 2026

## Problem

`google_sync.py` line 27 uses a relative path for `TOKEN_PATH`:

```python
TOKEN_PATH='[Google OAuth credentials]google-workspace-user.json'
```

This resolves relative to `AGENT_ROOT` which is `<hermes-root>/commons/data/ocas-weave/`, giving:

```
<hermes-root>/commons/data/ocas-weave/[Google OAuth credentials]google-workspace-user.json
```

This file does NOT exist. The actual token file is at:

```
/root/.google_workspace_mcp/credentials/google-workspace-user.json
```

## Symptom

Sync fails with `FileNotFoundError` on the token file, OR silently uses a wrong/old token file if one exists at the wrong path.

## Fix

Either:
1. **Fix TOKEN_PATH in `google_sync.py`** to use the absolute path `/root/.google_workspace_mcp/credentials/google-workspace-user.json`
2. **Or create a symlink**: `ln -s /root/.google_workspace_mcp/credentials/google-workspace-user.json "<hermes-root>/commons/data/ocas-weave/[Google OAuth credentials]google-workspace-user.json"`

## Verification

```bash
# Check if the token file exists at the expected path
ls -la "<hermes-root>/commons/data/ocas-weave/[Google OAuth credentials]google-workspace-user.json"

# Check the actual token location
ls -la /root/.google_workspace_mcp/credentials/google-workspace-user.json
```

## Related

- `google-token-quick-check.md` — pre-sync token validation
- `token-troubleshooting.md` — general token issues
- `google-workspace-auth` skill — full OAuth lifecycle
