# Weave — Gotchas

## Database Lock Management

- **LadybugDB lock contention**: The `ladybug-bridge-weave.service` holds a `READ_WRITE` lock on `weave.lbug`, blocking all other DB access. Before any DB write operation, ensure the bridge is stopped: `timeout 10 systemctl stop ladybug-bridge-weave.service`. **`systemctl stop` hangs indefinitely** if the service is in a failed/restart loop — always use `timeout`. After sync, restart with `timeout 10 systemctl start ladybug-bridge-weave.service`.
- **Cron job doesn't stop the bridge**: The `weave:sync-google` cron job runs `google_sync.py` directly without stopping `ladybug-bridge-weave.service` first. Every cron run will fail with a lock contention error unless the bridge is stopped. Either update the cron command to stop/start the bridge, or add bridge stop/start logic to the sync script itself.
- **Lock errors are immediate**: LadybugDB fails instantly if another process holds `READ_WRITE`. Surface the error — do not retry silently.
- **Long-running bridge lock blocks all DB access**: For one-off reads: kill the bridge → read → restart. For persistent services: use a pre-copied snapshot.
- **`process(action='kill')` does NOT kill the bridge process**: Always kill using the actual PID or `systemctl kill --signal=SIGKILL ladybug-bridge-weave.service`.

## Google OAuth and Sync

- **NEVER silently fall back to an alternate account's token**: When the primary user's token is expired, revoked, or empty, halt immediately. Log `auth_failure` in evidence. The evidence log entry `"google_sync": "completed_with_indigo_token"` is a smoking gun for this failure mode.
- **TOKEN_PATH must match the correct user account**: If `google_sync.py`'s `TOKEN_PATH` points to Indigo's credentials, the sync fetches Indigo's contacts (1 contact), not owner's (964 contacts).
- **`contacts.readonly` vs `contacts` scope**: Inbound sync works with readonly, but outbound fails with HTTP 403. Check scopes before running outbound.
- **Skip outbound entirely when scope is insufficient**: Don't fetch etags for 582+ contacts when all batch PATCH calls will 403.
- **Outbound checkpoint file accumulates stale entries after failures**: Clear checkpoint if previous run pushed 0.
- **Cron `HOME` env var breaks `Path.home()`**: Override `HOME=/root` when running sync scripts.
- **google_sync.py `token` field dependency**: The sync script reads `token_data.get('token', '')` from the JSON credential file (NOT `access_token`). Verify the JSON file has a `token` field.
- **google_sync.py uses env-var client credentials**: Ensure `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are set in any context where `google_sync.py` runs.

## Cross-Account Contamination

- **Wrong-Oauth-token sync cross-contamination**: When `google_sync.py` runs with the wrong account's token, contacts can be merged, names overwritten, or stale data injected. After any sync with wrong credentials, immediately check Google Contacts for cross-contamination.
- **Overnight enrichment Google sync must use the correct token**: Before any outbound Google sync in the enrichment pipeline, verify the token file belongs to the correct account.

## Python Environment

- **`real_ladybug` vs `ladybug` package mismatch**: The Weave DB is version 41, so only `ladybug` works. The sync scripts import `real_ladybug as lb`, which fails. Symlink `real_ladybug` → `ladybug` in the Python dist-packages. Use `/usr/bin/python3` (system Python 3.13) to run sync scripts.
- **Python script output buffering**: Background Python scripts with `terminal(background=true)` may show zero output. Use `python3 -u` (unbuffered) flag.

## Cron Mode

- **`execute_code` blocked in cron jobs**: Use `write_file` to write JSON journal entries directly, and `terminal()` with `echo >>` to append evidence lines.
- **Cron-safe journal pattern**: Write journal JSON directly via `write_file`. Append evidence via `terminal(command='echo \'{...}\' >> evidence.jsonl')`.
- **`process(action='wait')` timeout clamping in cron**: Use `process(action='poll')` in a loop instead of relying on `wait` blocking for the full duration.

## Contact Management

- **Person merge is never automatic**: Weave never silently collapses two Person nodes. Always confirm identity before merging; match by `google_resource_name`, email, or phone — never by name alone.
- **Outbound sync is doubly gated**: Both the config writeback flag AND per-sync user approval are required.
- **`HasFact` rejects property bags**: Use `CREATE (p)-[:HasFact]->(f)` only. Adding properties to the relationship will fail.
- **`Person.notes` column was dropped**: Any reference to `Person.notes` in sync scripts will fail at runtime.
- **Contact merge reported by user but absent in Weave DB**: If Weave shows separate records, the merge is in Google Contacts, not Weave.

## Enrichment

- **Enrichment Person field validation rejects value but may still write `_source` Fact**: Check Person node fields directly for enrichment status, not just the progress log.
- **`overnight_enrichment.py` writes Facts but not Person fields**: The "contacts needing enrichment" query uses Person fields, so the same contacts appear as having gaps every run even after successful enrichment.
- **SearXNG engine degradation**: Always check `unresponsive_engines` in the JSON response before trusting zero results. If >50% of engines are unresponsive, restart SearXNG.

## iptables

- **iptables must ACCEPT localhost before port-specific DROP rules**: Order matters: loopback ACCEPT first, then specific-source ACCEPT, then port DROP.

## MCP Health

- **MCP health before auth assumptions**: When Google Workspace MCP tools fail, do NOT assume auth tokens are expired. First ping the MCP. If unresponsive, restart the gateway.
- **MCP "unreachable" counter accumulates across sessions**: Wait for the cooldown (~60s) and retry BEFORE launching into token-refresh debugging.
