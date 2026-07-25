# Weave — Google Sync Operational Reference

## Sync procedure

After any Weave data changes, run the bidirectional Google Contacts sync:

```bash
HOME=/root AGENT_ROOT=<hermes-home> python3 <hermes-home>/skills/ocas-weave/scripts/google_sync.py
```

## Auth

- **Token path:** `<gworkspace-creds>/credentials/<account-identity>.json` (symlinked from the path the script expects)
- **TOKEN_PATH:** `<user-google-email>.json` ONLY — never silently fall back to the agent's token
- On auth failure: halt and report `auth_failure` — do not retry with a different token

## DB access

- **Must stop bridge first:** `systemctl stop ladybug-bridge-weave.service` — the bridge holds an exclusive lock on `weave.lbug`
- **Use `ladybug` package** (not `real_ladybug`) for DB access — real_ladybug causes segfaults and version mismatch errors
- **Extension path:** `LADYBUG_EXTENSION_PATH=<hermes-home>/profiles/indigo/home/.lbdb/extension/0.17.0/linux_amd64`
- Restart bridge after sync: `systemctl start ladybug-bridge-weave.service`

## Location format

`location_city` must be `"City, State"` or `"City, Country"` — always include the comma.

## Merge diagnostics

"Contacts got merged" reports: check Weave DB first. If records are separate in Weave, the merge happened in Google, not Weave.

## Token refresh

The sync script uses direct Google People API calls (not MCP). Token refresh uses the credential file's own `client_id`/`client_secret`, NOT env vars. If the refresh token is dead (`invalid_grant`), re-authorization is required — run the OAuth consent flow with `prompt=consent&access_type=offline`.
