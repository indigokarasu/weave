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
- Scope expansion always requires re-auth with `prompt=consent&access_type=offline`
- Bulk imports (>100 rows) should use `COPY FROM` not individual inserts
- Provenance for imported contacts: `source_type='imported'`, `confidence=0.8`
- Outbound PATCH requires current etag from Google — fetch etag before update
- **Top-level etag**: Use the top-level `etag` field from the GET response, NOT `metadata.sources[0].etag`
- **Stale resource names**: If a GET on `people/{resourceName}` returns 404, the resource name in Weave is stale. Re-match via `people:searchContacts` or refresh from inbound sync before pushing.
- **Correct update endpoint**: Use `{resourceName}:updateContact` (not `{resourceName}`) for PATCH updates.
- **Social profiles from notes**: Extract `notes.social_profiles` JSON and push each `{platform, url}` as `urls` entries with `type` set to the platform name.
- **Phone numbers may arrive with malformed leading `1`** (e.g. `+1 (141)...`) — validate before storing
- **Token path**: use `/root/.google_workspace_mcp/credentials/google-workspace-user.json` (managed by Google Workspace MCP server)

## Process Management

- **execute_code timeout**: The full sync script times out in `execute_code` (300s limit). Manual sync workaround: run as background process via `terminal(background=true)` with `notify_on_complete=true`.
- **Manual sync via background process**: Always use `terminal(background=true, notify_on_complete=true, timeout=600)`. The script takes ~280s for ~900 contacts.
- **Multi-run resilience**: The checkpoint system (`staging/outbound_ckpt.txt`) survives process kills and restarts.
- **Process spawning**: The Python script spawns a child process (real_ladybug C extension). Two PIDs is normal.
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

## Stale Etags After Multi-Run Resume

When the sync process is killed and restarted, etags loaded at script start may become stale. **Symptom**: HTTP 400 with `FAILED_PRECONDITION`. **Fix**: Re-run the sync — the next invocation re-fetches fresh etags. **Better fix**: Always use `people:batchGet` to fetch fresh etags right before batch update.

## Batch Operations

- **BatchUpdateContacts empty response**: HTTP 200 with empty body `{}` = all contacts updated successfully
- **Batch etag fetching**: Fetch 50 etags per request via `people:batchGet`. For 580 contacts: 12 batch GETs + 3 batch updates = 15 API calls vs 1162 individual calls.
