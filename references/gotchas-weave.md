# Weave — Gotchas

## Database

- **SQLite WAL mode**: Enabled by default. Multiple readers can access the DB while a single writer operates. No lock contention issues.
- **Foreign keys**: Enabled by default. `edges.source_id` has a FK to `persons(id)`. `edges.target_id` does NOT have a FK — it's a polymorphic reference (can point to persons, facts, or preferences depending on `rel_type`).
- **Concurrent access**: WAL mode handles this. If you get a `database is locked` error, it means a write is in progress. Wait briefly and retry (WeaveDB uses 30s timeout).
- **Backup**: Single file copy of `weave.sqlite` (+ `-wal` and `-shm` files if WAL mode is active). Copy all three files for a consistent backup.
- **Schema migrations**: `CREATE TABLE IF NOT EXISTS` only runs on first DB creation. Schema fixes in `weave_sqlite.py` do NOT apply to existing DBs. Always write an explicit migration script when changing DDL on a live database, and verify row counts before/after.

## Google OAuth and Sync

- **NEVER silently fall back to an alternate account's token**: When the primary user's token is expired, revoked, or empty, halt immediately. Log `auth_failure` in evidence.
- **TOKEN_PATH must match the correct user account**: `google_api.py` uses `<user-google-email>.json`. All Weave scripts import from `google_api.py` — never duplicate auth logic.
- **`contacts.readonly` vs `contacts` scope**: Inbound sync works with readonly, but outbound fails with HTTP 403. Check scopes before running outbound. **Differential diagnosis**: a 403 (Forbidden) on outbound = the token is VALID but lacks the write `scope` (`https://www.googleapis.com/auth/contacts`); a 401 `invalid_grant` = the refresh token is revoked/expired (token INVALID). Different root cause, different fix — a 403 means add the write scope / re-consent with Contacts scope, NOT "<operator> must re-authorize because the token was revoked." Don't conflate them. Both runs of the 2026-07-07 enrichment hit persistent 403 on all 588 outbound contacts while inbound (readonly) succeeded.
- **Skip outbound entirely when scope is insufficient**: Don't fetch etags for 582+ contacts when all batch PATCH calls will 403.
- **Outbound checkpoint file accumulates stale entries after failures**: Clear checkpoint if previous run pushed 0.
- **Cron `HOME` env var breaks `Path.home()`**: Override `HOME=/root` when running sync scripts.
- **Shared auth module**: All Google OAuth + API call logic lives in `scripts/google_api.py`. Import from there — never duplicate `get_access_token`, `api_get`, `api_post`, or `api_patch` in individual scripts.

## Cross-Account Contamination

- **Wrong-Oauth-token sync cross-contamination**: When sync runs with the wrong account's token, contacts can be merged, names overwritten, or stale data injected. After any sync with wrong credentials, immediately check Google Contacts for cross-contamination.
- **Overnight enrichment Google sync must use the correct token**: Before any outbound Google sync in the enrichment pipeline, verify the token file belongs to the correct account.

## Python Environment

- **Import pattern**: `from weave_sqlite import WeaveDB` — no need for `real_ladybug` or `ladybug` packages. SQLite is in stdlib.
- **No liblbug.so needed**: The C API shared library is no longer required.
- **Module-level imports**: Python names imported inside `if __name__ == "__main__"` are NOT visible to module-level functions. Import `timedelta`, `sqlite3`, and all other dependencies at the top of the file.
- **WeaveDB default path calculation**: In `scripts/weave_sqlite.py`, `AGENT_ROOT = Path(__file__).resolve().parents[2]` goes up 2 levels from the script, but the skill lives at `profiles/indigo/skills/ocas-weave/scripts/`. The correct path is `parents[3]` to reach `profiles/indigo/`. Using `parents[2]` points to `skills/` which has a stale/empty `commons/db/ocas-weave/weave.sqlite`. This causes silent write failures — the DB opens but has no data. Verify `DEFAULT_DB_PATH` resolves to the expected location on first import.

## Cron Mode

- **`execute_code` blocked in cron jobs**: Use `write_file` to write JSON journal entries directly, and `terminal()` with `echo >>` to append evidence lines.
- **Cron-safe journal pattern**: Write journal JSON directly via `write_file`. Append evidence via `terminal(command='echo \'{...}\' >> evidence.jsonl')`.
- **`process(action='wait')` timeout clamping in cron**: Use `process(action='poll')` in a loop instead of relying on `wait` blocking for the full duration.

## Contact Management

- **Person merge is never automatic**: Weave never silently collapses two Person nodes. Always confirm identity before merging; match by `google_resource_name`, email, or phone — never by name alone.
- **Outbound sync is on by default**, gated by the config writeback flag. The protection is in *what* is pushed: enrichment-derived field values are withheld, and pseudo-contacts, archived and deceased records are never created in Google. A missing `staging/` directory will fail every checkpoint write and take the batch down with it — ensure it exists.
- **Contact merge reported by user but absent in Weave DB**: If Weave shows separate records, the merge is in Google Contacts, not Weave.

## SQLite Quirks

- **Parameterized queries**: Use `:name` syntax (sqlite3 named parameters), not `$name`.
- **Boolean values**: SQLite has no boolean type. Use 0/1 integers.
- **GROUP_CONCAT**: Use for aggregating multiple rows into a string.
- **Recursive CTEs**: Supported natively. Use for graph traversal (shortest path, connections).
- **UPSERT**: Use `INSERT ... ON CONFLICT(id) DO UPDATE SET ...` pattern.
- **Inequality**: Use `!=` or `<>` (both work in SQLite).

## Tooling

- **`enrichment_data.py` does NOT exist**: There is no `enrichment_data.py` script. The pipeline instructions that reference it (`searxng-ensure`, `list`, `write`, `stats` subcommands) are stale. Use direct Python/WeaveDB queries and `curl` for SearXNG health checks instead.
- **`web_extract` + SearXNG backend**: The `web_extract` tool fails with "SearXNG is a search-only backend and cannot extract URL content." Use `curl -s "https://r.jina.ai/URL"` for page content fetching instead. This applies to all URLs, not just LinkedIn.
- **LadybugDB bridge service removed**: The `ladybug-bridge-weave.service` no longer exists after the SQLite migration (June 2026). Do not attempt `systemctl stop/start` on it.
- **Google OAuth `invalid_grant`**: When `google_sync.py` fails with HTTP 401 and the token endpoint returns `invalid_grant: Token has been expired or revoked`, the refresh token is permanently invalid. <operator> must re-authorize the OAuth consent flow. Log the failure and continue enrichment using MCP tools — do not halt the pipeline.

## LinkedIn Profile Fetching (June 2026)

LinkedIn profiles are auth-walled. Fetching strategy that works:

1. **Direct HTTP** with browser User-Agent — returns full HTML with `<title>` containing `"Name - Title at Org | LinkedIn"` and `<meta name="description">` with location.
2. **Parse the title**: Split on ` | LinkedIn` then on ` - `. If ` at ` exists in the last part, it's `"Occupation at Org"`. Otherwise it's just the org.
3. **Parse the description**: Contains `Location: City, ST` and `Experience: OrgName` segments.
4. **Jina Reader blocked**: `r.jina.ai/linkedin.com/in/...` returns `SecurityCompromiseError` due to LinkedIn DDoS protection.
5. **web_extract cannot fetch**: SearXNG-based `web_extract` is search-only and cannot fetch URLs.
6. **LinkedIn MCP**: `LINKEDIN_GET_PERSON` requires a `person_id` (not username). There is no name-search tool. Use web search to find the username, then direct HTTP to fetch.

## Enrichment

- **Enrichment Person field validation rejects value but may still write `_source` Fact**: Check Person node fields directly for enrichment status, not just the progress log.
- **`overnight_enrichment.py` writes Facts but not Person fields**: The "contacts needing enrichment" query uses Person fields, so the same contacts appear as having gaps every run even after successful enrichment.
- **SearXNG engine degradation**: Always check `unresponsive_engines` in the JSON response before trusting zero results. If >50% of engines are unresponsive, restart SearXNG.
- **Enrichment write pattern**: When writing enrichment data to Weave, always use `weave.execute_write()` / `weave.execute()` (the WeaveDB abstraction layer) rather than raw `sqlite3` connections. Raw connections bypass FK enforcement and skip WAL mode.
- **Shared enrichment extraction**: All web scraping, content extraction, and field validation logic lives in `scripts/weave_enrich.py`. Both `quick_enrich.py` and `overnight_enrichment.py` import from it. When modifying extraction patterns, update `weave_enrich.py` — never edit duplicated copies.
- **Function signature drift in pipeline scripts**: `overnight_enrichment.py` called `sift_extract_from_pages(name, org, all_results, max_pages=3)` but the function signature is `sift_extract_from_pages(name, search_results, max_pages=3)`. The extra `org` argument shifted `all_results` into `max_pages` and `max_pages=3` was ignored. Always verify function signatures match when calling shared functions from `weave_enrich.py`.
- **Enrichment field validation too permissive (PATCHED 2026-06-18)**: `validate_field()` in `weave_enrich.py` previously allowed garbage through: sentence fragments as occupations ("As the Editorial Director"), city names as org ("Chicago", "Los Angeles"), single-word generic orgs ("Professional", "Accidents", "per", "newsletter"), partial org names ("was", "Updates", "Product", "San"), invalid locations ("Teague, CP"), junk emails ("leaflet@1.9.4"), and duplicate fact inserts. **Fix applied**: org validation now rejects known STATIC_CITIES, generic non-company words, sentence fragments (was/were/been/have/has), values without uppercase letters, and single-character values. Occupation validation requires title-case tokens. Always deduplicate facts before insert. See `references/enrichment-data-quality.md` for the full pattern catalog.
- **SearXNG connection resets under load**: During overnight enrichment, SearXNG can return `Connection reset by peer` or `Remote end closed connection without response` errors when hit with rapid sequential searches. Add retry logic with exponential backoff (3 attempts, 2s/4s/8s delays) to `searxng_search()` in `weave_enrich.py`.
- **overnight_enrichment.py duplicate processing**: The script's progress tracking does not prevent re-processing contacts that were already enriched in a previous run. If interrupted and restarted, contacts appear in the progress file but may already have facts written. The script also writes duplicate facts (same predicate/value for the same person) when `enrich_weave_contact()` is called multiple times for the same contact. Always deduplicate after enrichment runs.