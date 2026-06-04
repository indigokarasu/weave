# MCP Auth Retry & Wrong-Token Sync Incidents

## MCP "Unreachable" Retry Pattern (June 2026)

**Symptom**: Every MCP tool call returns either:
- `"MCP server 'google-workspace' is unreachable after N consecutive failures. Auto-retry available in ~58s."`
- `"ACTION REQUIRED: Google Authentication Needed for Google People for 'google-workspace-user'"` with a fresh auth URL each call

**Root cause**: The "unreachable" counter accumulates across tool calls and sessions. Each failed call increments N and starts a ~60s cooldown. The auth URL has a fresh `state` parameter each time, confirming the MCP server spawns fresh per request and never reaches stored credentials.

**What NOT to do** (learned the hard way):
- Do NOT try to manually refresh tokens via the People API (`invalid_grant` means the refresh token itself is dead)
- Do NOT generate auth URLs via `google_reauth_url.py` — that uses a different OAuth client than the MCP
- Do NOT debug the MCP server's credential store or token files
- Do NOT assume the user is wrong when they say "you are already authorized"

**Correct response**:
1. If user says they're authorized, simply WAIT 60s for the cooldown and retry the MCP call
2. If still failing after cooldown, ask user to re-authorize via the MCP's own auth URL (the one returned in the error)
3. The MCP uses client ID `550801240087-vmc47b1gflj2biblqdr6bkekl7qqm8ls` and credentials dir `/root/.google_workspace_mcp/credentials/`

## Wrong-Token Sync Cross-Contamination (June 1 2026)

**Incident**: The overnight enrichment pipeline's Google sync ran with Indigo's OAuth token instead of owner's (owner's token was expired since May 7). Evidence log entry: `"google_sync": "completed_with_indigo_token"`.

**Symptom reported by user**: "Zhenshuo Fang got merged with Karl Lindekugel" — visible in Google Contacts UI but NOT in Weave DB.

**Root cause**: When `google_sync.py` runs with wrong account credentials, it can:
- Create contacts in the wrong Google account
- Match existing contacts by email/phone and overwrite fields
- Merge distinct contacts if the matching logic finds a false positive

**Weave DB state**: All three records were separate:
- Karl Lindekugel IV — Merrill Lynch, `people/c3547945627458424429`
- Karl Lindekugel (CFO) — Eccotemp Systems, no Google resource name
- Zhenshuo Fang — Xiaomi, `people/c7279332960451507149`

**Fix required**:
1. Get valid Google OAuth for owner (re-authorize via MCP auth URL)
2. Query Google Contacts API to see the merged state
3. Separate the merged contact in Google Contacts
4. Re-sync inbound to Weave to restore correct data
5. Verify `google_sync.py` `TOKEN_PATH` points to correct account before future runs

**Prevention**: The `google_sync.py` script should verify the token file is non-empty AND the token can actually fetch contacts before proceeding. If the token returns `invalid_grant` on refresh, halt and report — do not silently switch to a different account's token.
