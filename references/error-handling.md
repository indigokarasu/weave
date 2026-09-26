# Error handling

Failure → symptom → handling. Read the row that matches what you are seeing;
do not improvise a workaround for a failure listed here.

| Failure | Symptom | Handling |
|---|---|---|
| Failure | Symptom | Handling |
|---|---|---|
| `google_sync.py` exits 2 | Revoked/expired OAuth token; clean ABORT printed | Re-auth the operator's Google account. **No programmatic workaround** — log, continue with MCP tools, and do not fall back to the agent's own account (auth rule above). |
| `google_sync.py` aborts on paths | `AGENT_ROOT` unset or wrong | Re-run with `AGENT_ROOT=<hermes-home>/profiles/indigo HOME=/root`. Sibling skill paths resolve against it; without it the sync reads an empty tree and reports 0 contacts. |
| Enrichment run produced nothing and no error | `discovery_probe.py` shows every source down | **Not** a null result. Defer real people, skip unresolvables, write no facts. An empty search result is never evidence to record. |
| `HasFact` edge insert raises or silently no-ops | `PRAGMA foreign_key_list(edges)` shows `target_id` → `persons(id)` | Drop that FK and recreate `edges`. `target_id` is polymorphic; the constraint is wrong by construction. |
| 0-row UPDATE after a persons write | Wrong `person_id` copied by hand | Copy IDs programmatically, never by hand. Re-query by name, confirm the UUID, retry. See `references/pitfalls-weave.md`. |
| Enrichment "vanished" | No row in the canonical DB | Check for a stale DB copy at a `parents[2]`-derived path (pre-enrichment checklist). A subagent wrote to it instead. |
| Two rows for one person | `persons` has duplicates after a merge attempt | Do not merge by name. Use `merge_persons.py`; a name is not an identity. |
| `foreign key constraint` / `database is locked` | Concurrent write during a sync | WAL allows many readers, one writer. Surface the error and retry the single statement — **never** wrap a write in a silent retry loop. |
| `web_extract` returns empty on a page | SearXNG backend down | Use `curl -s "https://r.jina.ai/URL"`. Jina Reader blocks LinkedIn — that is an access wall, not a bug; mark the contact unresolvable rather than guessing. |
| Script referenced by SKILL.md is missing after a pull | Any `references/*.md` link 404s | Phantom ref. Restore the file from the repo copy or remove the pointer; do not leave a link to a nonexistent subsystem. |
| DB path resolves somewhere unexpected | `sqlite3.connect` opens a file you did not expect | Print the resolved path before opening it. `parents[n]` assumptions have produced three wrong roots. |

## The three failures that cost the most

**An empty search result is not a null result.** It means no data was found,
and the correct action is to write nothing. Enrichment that "completed with
zero results" against a dead search backend is worse than a failed run,
because the record looks clean when it is actually unexamined.

**A 0-row UPDATE is not a no-op.** It means the id you wrote with does not
exist. The person was not updated, and if you also inserted a fact, that fact
is now orphaned. Re-query by name, copy the id programmatically, retry.

**Enrichment that "vanished" was written somewhere else.** The `parents[n]`
path bug produced three non-canonical database roots. Before re-running any
enrichment, confirm the resolved path of the database you are about to open.

## Destructive operations

`restore_google_urls.py` is dry-run by default; `--apply` writes. Before any
repair, purge, or merge, read `database_maintenance.md` — it carries the
rollback for each. The rule across all of them: a destructive script reports
what it would change, and a human-scale read of that list comes before `--apply`.
