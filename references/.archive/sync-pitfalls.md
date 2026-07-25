# Google Sync Pitfalls

Detailed troubleshooting for Google Contacts sync issues. For token/auth issues, see `google-token-diagnostics.md`.

## API Quota

- `Critical read requests`: 90 req/min per user per project
- Both GET (etag fetch) and PATCH (update) count against this same 90/min bucket
- Outbound uses 2 API calls per contact (GET etag + PATCH) — at 90/min ceiling, that allows ~45 contacts/min
- **Use `BatchUpdateContacts` for outbound** — up to 200 contacts per batch request, reducing 2N calls to N/200 calls
- Recommended sleep between batches: 1.5s. Between individual PATCHes (if not batching): 1.3s minimum
- **Rate limit backoff must start at 5s minimum**, not 0.5s
- On 429: exponential backoff starting at 5s, doubling per retry up to 4 attempts
- On 502: retry once after 5s, then mark failed
- On 404: contact was deleted from Google — clear `google_resource_name` in Weave

## Known Pitfalls

- `otherContacts()` API is unreliable — use REST with `contacts.other.readonly` scope instead
- `expiry` field in token may be ISO string or integer; handle both
- **Scope expansion always requires re-auth with `prompt=consent&access_type=offline`** — but the Desktop OAuth client (`628032148246-scqt66f1s2533k7u06gjre7k7jo1sbqt`) rejects many GCloud infra scopes as invalid (e.g. `dns`, `speech`, `language`, `file`, `secretmanager`, `iap`, `vpcaccess`, `artifactregistry`, `containerregistry`, `cloudscheduler`, `accesscontextmanager`, `gan`, `certificatemanager`, `firebase.management`, `networkmanagement`, and their `.readonly` variants). Only ~88 of 100+ requested scopes are valid. Google returns `invalid_scope` error listing valid vs invalid. Use only the valid scopes — see `references/google-sync-ops.md` for the current valid scope list.
<<<<<<< Updated upstream
- **Paste-back OAuth flow**: The listener timeout must be ≥ 15 minutes for user interaction. The `oauth_complete_v2.py` script at `<hermes-home>/scripts/oauth_complete_v2.py` has a 15-minute timeout and writes the code to `/tmp/oauth_code.txt` immediately on receipt. If the process dies, the code is still in that file.
=======
- **Paste-back OAuth flow**: The listener timeout must be ≥ 15 minutes for user interaction. The `oauth_complete_v2.py` script at `~/.hermes/scripts/oauth_complete_v2.py` has a 15-minute timeout and writes the code to `/tmp/oauth_code.txt` immediately on receipt. If the process dies, the code is still in that file.
>>>>>>> Stashed changes
- Bulk imports (>100 rows) should use `COPY FROM` not individual inserts
- Provenance for imported contacts: `source_type='imported'`, `confidence=0.8`
- Outbound PATCH requires current etag from Google — fetch etag before update
- **Top-level etag**: Use the top-level `etag` field from the GET response, NOT `metadata.sources[0].etag`
- **Stale resource names**: If a GET on `people/{resourceName}` returns 404, the resource name in Weave is stale. Re-match via `people:searchContacts` or refresh from inbound sync before pushing.
- **Correct update endpoint**: Use `{resourceName}:updateContact` (not `{resourceName}`) for PATCH updates.
- **Social profiles from notes**: Extract `notes.social_profiles` JSON and push each `{platform, url}` as `urls` entries with `type` set to the platform name.
- **Phone numbers may arrive with malformed leading `1`** (e.g. `+1 (141)...`) — validate before storing
- **Token path**: use the Google OAuth credential file for the target account

## Process Management

- **execute_code timeout**: The full sync script times out in `execute_code` (300s limit). Manual sync workaround: run as background process via `terminal(background=true)` with `notify_on_complete=true`.
- **Manual sync via background process**: Always use `terminal(background=true, notify_on_complete=true, timeout=600)`. The script takes ~280s for ~900 contacts.
- **Multi-run resilience**: The checkpoint system (`staging/outbound_ckpt.txt`) survives process kills and restarts.
- **Process spawning**: The Python script spawns a child process (ladybug C extension). Two PIDs is normal.
<<<<<<< Updated upstream
- **Package: use `ladybug`, NOT `real_ladybug`** (June 2026): The `real_ladybug` package causes segfaults (exit 139) and DB version mismatch errors. All direct-DB scripts must use `import ladybug as lb`. Before importing, set the extension path: `os.environ['LADYBUG_EXTENSION_PATH'] = str(Path('<hermes-home>/profiles/indigo/home/.lbdb/extension/0.17.0/linux_amd64'))`. The `real_ladybug` package is incompatible with the current DB file version.
=======
- **Package: use `ladybug`, NOT `real_ladybug`** (June 2026): The `real_ladybug` package causes segfaults (exit 139) and DB version mismatch errors. All direct-DB scripts must use `import ladybug as lb`. Before importing, set the extension path: `os.environ['LADYBUG_EXTENSION_PATH'] = str(Path('~/.hermes/profiles/indigo/home/.lbdb/extension/0.17.0/linux_amd64'))`. The `real_ladybug` package is incompatible with the current DB file version.
>>>>>>> Stashed changes
- **Output buffering**: stdout appears empty for 90-120+ seconds despite the script actively working. Do NOT kill the process thinking it's hung.
- **Do NOT use SIGUSR1**: Causes `RuntimeError`. Use `/proc/<pid>/wchan`, `ss -tnp`, and checkpoint file inspection instead.

## LadybugDB Lock After Process Kill

When the sync process is killed (SIGTERM/SIGKILL, exit codes 143/137), the LadybugDB file lock can remain held. **Diagnosis**: `fuser -v {agent_root}/commons/db/ocas-weave/weave.lbug`. **Fix**:

```bash
fuser -v {agent_root}/commons/db/ocas-weave/weave.lbug 2>&1
kill -9 <PID> 2>/dev/null
rm -f {agent_root}/commons/db/ocas-weave/weave.lbug.wal
fuser {agent_root}/commons/db/ocas-weave/weave.lbug
```

Kill, re-check, repeat until `fuser` returns empty. The `.wal` file from a killed process is always stale; removing it is safe.

## LadybugDB Bridge Server Lock (June 2026)

<<<<<<< Updated upstream
The `ladybug_bridge.py` server (`<hermes-home>/scripts/ladybug_bridge.py`) runs as a persistent service (typically on port 9191) and holds an **exclusive write lock** on `weave.lbug` for its entire lifetime. Any script that opens the DB directly via `ladybug.Database(path)` — including `google_sync.py`, `weave_health_check.py`, and all enrichment scripts — will fail with:
=======
The `ladybug_bridge.py` server (`~/.hermes/scripts/ladybug_bridge.py`) runs as a persistent service (typically on port 9191) and holds an **exclusive write lock** on `weave.lbug` for its entire lifetime. Any script that opens the DB directly via `ladybug.Database(path)` — including `google_sync.py`, `weave_health_check.py`, and all enrichment scripts — will fail with:
>>>>>>> Stashed changes

```
RuntimeError: IO exception: Could not set lock on file : .../weave.lbug
```

**Diagnosis**:
```bash
<<<<<<< Updated upstream
fuser <hermes-home>/commons/db/ocas-weave/weave.lbug
=======
fuser ~/.hermes/commons/db/ocas-weave/weave.lbug
>>>>>>> Stashed changes
# If PID belongs to ladybug_bridge.py:
ps aux | grep ladybug_bridge
```

**The bridge and direct-DB access are mutually exclusive.** To run any direct-DB script:

1. Stop the bridge (see systemd procedure below)
2. Wait 2s for lock release
3. Run the sync/script
4. Restart the bridge afterwards

**Do NOT kill the bridge lightly** — other services may depend on it. Check what's connected to port 9191 first:
```bash
ss -tnp | grep 9191
```

**Alternative**: For read-only operations, use the bridge's HTTP API on port 9191 instead of opening the DB directly. For write operations (sync, enrichment), the bridge must be stopped first.

**Cron job implication**: Scheduled syncs that run while the bridge is up will always fail with the lock error. Either schedule syncs during a bridge restart window, or add bridge stop/start logic to the cron wrapper.

## Bridge systemd Service Management (June 2026)

The weave bridge is managed by systemd as `ladybug-bridge-weave.service`. **Simply `kill`ing the PID is not enough** — systemd will auto-restart it within seconds, re-acquiring the lock before the sync script can open the DB.

**`systemctl stop` can hang** — do not rely on it. The reliable procedure is:

```bash
# 1. Move the service file to prevent auto-restart
mv /etc/systemd/system/ladybug-bridge-weave.service /etc/systemd/system/ladybug-bridge-weave.service.bak
systemctl daemon-reload

# 2. Kill the running bridge process
kill -9 $(pgrep -f "ladybug_bridge.*weave")

# 3. Verify lock is released
<<<<<<< Updated upstream
fuser <hermes-home>/commons/db/ocas-weave/weave.lbug  # should return empty

# 4. Remove stale WAL
rm -f <hermes-home>/commons/db/ocas-weave/weave.lbug.wal

# 5. Run the sync/script
cd <hermes-home> && HOME=/root AGENT_ROOT=<hermes-home> python3 <script>
=======
fuser ~/.hermes/commons/db/ocas-weave/weave.lbug  # should return empty

# 4. Remove stale WAL
rm -f ~/.hermes/commons/db/ocas-weave/weave.lbug.wal

# 5. Run the sync/script
cd ~/.hermes && HOME=/root AGENT_ROOT=~/.hermes python3 <script>
>>>>>>> Stashed changes

# 6. Restore and restart the bridge
mv /etc/systemd/system/ladybug-bridge-weave.service.bak /etc/systemd/system/ladybug-bridge-weave.service
systemctl daemon-reload
systemctl start ladybug-bridge-weave.service
```

**Important**: Always restore the service file after the sync. A missing bridge blocks all Weave HTTP API consumers.

## Google People API: Outbound 403 Insufficient Scopes (June 2026)

**Symptom**: Inbound sync (read) succeeds, but outbound batch updates fail with HTTP 403:
```
Request had insufficient authentication scopes.
status: PERMISSION_DENIED
```

**Cause**: The OAuth token has read scope (`contacts.readonly`) but lacks write scope (`https://www.googleapis.com/auth/contacts`). The Google People API requires full write scope for `batchUpdateContacts` and `updateContact`.

**How to distinguish from a dead refresh token**:
- Dead refresh token → `invalid_grant` error during token refresh, inbound also fails
- Insufficient scopes → inbound works fine, only outbound write operations fail with 403

**Fix**: Re-authorize with `prompt=consent&access_type=offline` including `https://www.googleapis.com/auth/contacts` in the scope list. See `references/google-token-diagnostics.md` and the `google-workspace-auth` skill.

**Do NOT mark outbound contacts as processed** when auth fails — the checkpoint system will skip them on re-run. The sync script handles this correctly by exiting with pushed=0 failed=N.

## Stale Etags After Multi-Run Resume

When the sync process is killed and restarted, etags loaded at script start may become stale. **Symptom**: HTTP 400 with `FAILED_PRECONDITION`. **Fix**: Re-run the sync — the next invocation re-fetches fresh etags. **Better fix**: Always use `people:batchGet` to fetch fresh etags right before batch update.

## Batch Operations

- **BatchUpdateContacts empty response**: HTTP 200 with empty body `{}` = all contacts updated successfully
- **Batch etag fetching**: Fetch 50 etags per request via `people:batchGet`. For 580 contacts: 12 batch GETs + 3 batch updates = 15 API calls vs 1162 individual calls.