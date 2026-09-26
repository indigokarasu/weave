# Cron-mode operating notes

Read this **only when running Weave as a scheduled cron job**, agent-driven
(not script-driven). Everything here is a cron-specific constraint; interactive
sessions ignore it.

## The invocation runbook is STALE — defer to the skill

The cron job's user message often contains a hardcoded pipeline runbook. The
skill is updated first; the cron message template lags by weeks or months.
**ALWAYS defer to the skill's own documentation** over the runbook in the
invocation message.

| Runbook instruction | Correct action |
|---|---|
| "Stop the LadybugDB bridge" | **NOOP** — bridge removed June 2026 |
| "Run enrichment_data.py" | **DOES NOT EXIST** — query WeaveDB directly |
| "Run google_sync.py without AGENT_ROOT" | **WILL FAIL** — set `AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root` |
| "Restart the bridge after" | **NOOP** — bridge removed |
| Any step using `execute_code` | **BLOCKED IN CRON** — use `terminal` with an inline `python3 -c "..."` or a script path |
| `systemctl stop ladybug-bridge-weave.service` | **NOOP** — unit no longer exists |

## Agent-driven enrichment checklist

- [ ] Probe discovery sources first (`scripts/discovery_probe.py`) — all-down means defer, not fabricate
- [ ] Query WeaveDB directly for gaps and stats; `enrichment_data.py` does not exist
- [ ] `curl` for SearXNG health checks
- [ ] Use the three-step write pattern in `enrichment-write-pattern.md` (persons UPDATE → facts INSERT → edges INSERT)
- [ ] Read back every write by primary key before claiming success
- [ ] Skip contacts where both `occupation` AND `org` are null AND `confidence` < 0.5
- [ ] Log material decisions to `decisions.jsonl`

## Schema reminder

`facts` has **NO** `person_id` — linkage is via the `edges` table.
`persons` columns: id, name, email, phone, location_city, location_country,
occupation, org, google_resource_name, clay_id, source_type, source_ref,
confidence, record_time, valid_from, valid_until.

## Tooling constraints

- `execute_code` is blocked in cron — it runs arbitrary local Python that
  bypasses approval checks. Use `terminal` with inline `python3 -c "..."`, or
  write a temp script under the scratch dir and run it by path.
- `web_extract` fails when the SearXNG backend is down. Fall back to
  `curl -s "https://r.jina.ai/URL"`. Jina Reader blocks LinkedIn — treat that as
  an access wall, not a failure to work around.
- `google_sync.py` exits 2 with a clean ABORT on a revoked token. Log and
  continue with MCP tools. There is no programmatic workaround, and falling
  back to the agent's own Google account is a hard violation of the auth rule.
- Foreground `terminal` calls time out at 600s. For long syncs use
  `background=true` with `notify_on_complete=true`.

## Longer reference

Step-by-step runbook, confidence scoring guide, and the read-back verification
pattern: `cron-pipeline-runbook.md`.
