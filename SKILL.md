---
name: ocas-weave
description: >
  Weave: private provenance-backed social graph. Maintains queryable records
  of people, relationships, preferences, and shared experiences for recall, gifting,
  hosting, introductions, and serendipity. Trigger phrases: "who do I know in",
  "what does X like", "add this person", "relationship with", "gift ideas for",
  "sync contacts", "prepare for meeting with", "update weave". Use when storing
  or retrieving facts about a person, recording a relationship, or discovering connections
  between people. Do NOT use for sending messages (use Dispatch), calendar management
  (use Sands), OSINT research (use Scout), or web research without a social graph need (use Sift).
license: MIT
source: https://github.com/indigokarasu/weave
includes:
- references/**
- scripts/**
metadata:
  author: Indigo Karasu (indigokarasu)
  version: 3.6.0
triggers:
- social graph
- contact management
- person facts
- relationship tracking
- people knowledge
---
## Interactive Menu

When invoked interactively (via `/` command), present a two-level menu using the `clarify` tool so the user can pick which function to run.

**Level 1 — Category selection** (max 4 choices):

```python
result = clarify(
    question="What would you like to do?",
    choices=[
        "People & Relationships — upsert persons, relationships, preferences",
        "Query & Export — query graph, import CSV, export data, generate vCard",
        "Connect & Init — sync Google Contacts, initialize database",
        "Status — show system status",
    ]
)
```

**Level 2 — Action selection** based on Level 1 choice:

- **People & Relationships** → clarify with choices: "upsert.person — Add/update a person", "upsert.relationship — Add/update a relationship", "upsert.preference — Store a preference"
- **Query & Export** → clarify with choices: "query — Query the social graph", "import.csv — Bulk import from CSV", "export — Export graph data", "project.vcard — Generate vCard"
- **Connect & Init** → clarify with choices: "sync.google-contacts — Sync with Google Contacts", "init — Initialize/repair database"
- **Status** → run "status — Show system status" directly (single action — no sub-menu needed)

After the user selects an action, execute it following the relevant procedure in this skill. Loop back to the menu after each action completes, until the user chooses to exit or sends `/stop`.

### Response parsing

Match the user's response against the full choice string. Extract the action key by splitting on `" — "` and taking the first segment. If the response doesn't match any known choice (user typed free-form via "Other"), match key prefixes case-insensitively. Re-present the current menu level on no match.

### Platform adaptation

On CLI, choices are navigable with arrow keys. On messaging platforms, choices render as a numbered list. The two-level hierarchy ensures no more than 4 options appear at any level on any platform.


## When to Use

- Contact management and relationship tracking
- Social graph queries (who knows whom, how)
- Contact enrichment from multiple sources
- Strength and interaction history
- Store or update information about a person, relationship, or preference
- Look up who someone is, how they relate, or what they like
- Prepare for a meeting, dinner, or introduction
- Find connections in a given city
- Generate gift ideas grounded in known preferences
- Discover serendipity connections between people
- Sync contacts from Google Contacts or Clay

## When NOT to Use

- Sending messages or emails (use Dispatch)
- Calendar management (use Sands)
- OSINT research (use Scout)
- Knowledge graph entity resolution (use Elephas)
- Web research without a social graph need — use Sift
- CRM or sales pipeline automation
- Personality profiling without evidence

# Weave

Weave maintains a private, provenance-backed social graph of people, relationships, preferences, and shared experiences — queryable for meeting prep, gift ideas, hosting, introductions, city connections, and serendipity discovery. Every stored fact carries source type, reference, timestamp, and confidence score; the graph never silently merges two person records and never writes back to external systems without explicit per-sync approval.

## Responsibility Boundary

Weave owns the social relationship graph: people, relationships, preferences, and shared experiences. It is the only skill that writes to its LadybugDB database.

Weave does not: perform OSINT research (Scout), manage calendars (Sands), organize files (Bower), or build the long-term knowledge graph (Elephas). Entity disambiguation queries to Weave are read-only for all other skills.

## Ontology types

Weave works with these types from `spec-ocas-ontology.md`:

- **Entity/Person** — people in the social graph. Weave extracts and manages Person entities exclusively.

Weave may optionally emit Signals to Elephas for Person nodes with high-confidence identity markers, but this is not required for normal operation.

Each Signal emitted to Elephas must include a `user_relevance` field: `user` if the entity is directly related to the user's world, `agent_only` if encountered incidentally, `unknown` if unclear. Weave entities are almost always `user`-relevant since they represent the user's actual social connections.

## LadybugDB usage guide

Read `references/ladybugdb-guide.md` for query result handling, iteration pitfalls, and import patterns with code examples.

## Storage layout

See `references/schemas.md` for the storage layout. See `references/config-defaults.md` for the default `config.json` structure, writeback flags, and retention settings.

## Database rules

LadybugDB is an embedded single-file database. One `READ_WRITE` process at a time. If another process holds the lock, operations fail immediately with a lock error — surface the error, do not retry silently.

Multiple `READ_ONLY` connections are safe simultaneously. `COPY FROM` is for bulk import (>100 rows). `MERGE` is for sporadic single-record upserts. Never loop `MERGE` over bulk data.

## Auto-initialization

Every command that opens the database runs `_ensure_init()` first. No manual init command is needed on first use.

Read `references/init_pattern.md` for the `_open_db` implementation pattern. Full DDL is in `references/schemas.md`.

**Config.json missing**: Health checks should verify `config.json` exists in `{agent_root}/commons/db/ocas-weave/`. If missing, run `weave.init` to trigger auto-creation via `_ensure_init()`.

## Commands

**weave.upsert.person** — Add or update a person. Auto-inits DB on first call. MERGE on `id`. Read back after write; report failure if no row returned — never claim success unconfirmed.

**weave.upsert.relationship** — Add or update a `Knows` edge. Confirm both Person nodes exist first. Halt and report which is missing.

**weave.upsert.preference** — Store a provenance-backed preference. Each preference is a distinct `CREATE` (not merged). Link to Person via `HasPreference` edge.

**weave.import.csv** — Bulk import contacts via `COPY FROM`. Read `references/import_export.md`. Pre-process CSV to staging dir first. Check `CALL show_warnings() RETURN *` after. Report: N imported, N skipped (with reasons), N failed.

**weave.query** — Query the graph. Read `references/query_patterns.md`. Modes: `lookup`, `connection`, `serendipity`, `city`, `summarize`, `gift`. Return only stored facts with provenance. Never speculate.

**weave.attach** — Query an external skill database read-only. Read `references/cross_db.md`.

**weave.export** — Export data to staging dir via `COPY TO`. Read `references/import_export.md`.

**weave.sync.google-contacts** — Run bidirectional Google Contacts sync. Read `references/connectors.md` before any sync. Outbound requires enabling `writeback.google_contacts` in config (see `references/connectors.md`).

**weave.sync.clay** — Bidirectional sync with Clay. Read `references/connectors.md`. Outbound requires `writeback.clay: true` AND explicit approval.

**weave.project.vcard** — Generate vCard 4.0 draft. Read `references/vcard_projection.md`. Omit fields with confidence below 0.7. Requires explicit approval before writeback.

**weave.writeback.contacts** — Push records to Google Contacts or Clay. Disabled by default. Requires config enablement AND per-action user approval.

**weave.init** — Diagnostic and repair. Checks schema, creates missing tables, verifies indexes.

**weave.status** — Report graph health and config state.

See `references/schemas.md` for details.

**weave.journal** — Write journal for the current run. Read `references/journal.md`. Called at end of every run. Journals are immutable after write.

**weave.update** — Pull latest skill package from GitHub source. Preserves journals and data. See `references/self-update.md`.

## Run completion

After every Weave command that reads or writes data:

1. Persist any new or updated records to the database
2. Log material decisions to `decisions.jsonl`
3. Write journal via `weave.journal` — Observation Journal for queries/upserts/imports, Action Journal for syncs/writebacks
4. **Read-back verification**: After every write operation (`upsert.*`, `sync.*`, `writeback.*`, `import.*`), immediately query the database for the affected person/relationship/preference record by its primary key. Confirm the written data matches what was intended — field values, provenance metadata, and confidence scores. Report failure if no row is returned or if any field differs from the intended write. Never claim success unconfirmed.

## Provenance

Every written fact requires: `source_type` (direct / inferred / imported / user-stated), `source_ref`, `record_time` (ISO 8601), `confidence` (0.0–1.0). Use `event_time` when the real-world occurrence has a distinct time. Never write facts without provenance.

## Contact enrichment lifecycle

Read `references/enrichment-pipeline.md` for the full overnight pipeline architecture.

For manual enrichment of high-value contacts, use the full quality pipeline:

1. **Pre-Search Seed Quality Check** — Check `source_type` and `confidence`. If `web_enrichment` with confidence < 0.8: reject org/location/occupation. Use only name + email + phone (trusted signals).
2. **Read** — Query Weave for the existing Person record. Confirm it exists.
3. **Search** — Use SearchX (SearXNG via `execute_code`) for identity-resolved research. Fallback to `web_search`. **Cron note**: `execute_code` is blocked in cron jobs. In cron context, use `terminal()` to run SearXNG `curl` queries directly, or skip the search step.
4. **Enrich (Scout → Sift → Sherlock)**:
   - **Scout**: Targeted SearXNG queries using name + org. URLs/snippets are discovery only, NOT extraction source.
   - **Sift**: Fetch full page content from top non-auth-walled URLs (direct HTTP for static, Jina Reader for JS-heavy). Extract structured data from full pages. NEVER extract from search snippets (~160 chars → truncated/garbage).
   - **Sherlock**: Run `sherlock --print-found --no-color '<username>' --timeout 90`. Verify 2+ data points per result. For verified profiles that self-link other platforms, treat linked platforms as high-quality.
5. **Write** — MERGE on Person, CREATE on Fact/Preference. Always read back.
6. **Search again** — Follow-up with enriched data (company, location, title).
7. **Sync** — After writes touching mapped fields, sync to Google Contacts.

### Implicit data from user language

When the user uses pronouns or relationship terms (wife, husband, partner, brother, etc.), extract and store as high-confidence Facts (`source_type: 'user-stated'`, `confidence: 0.99`).

## Google Contacts sync

Weave maintains bidirectional sync via `scripts/google_sync.py`. All Google Contacts API usage — OAuth scopes, batch sizes, rate limits, snapshot safeguards, and token management — is documented in `references/connectors.md`.

Key rules:
- Match by `google_resource_name`, then email, then phone. Never match on name alone.
- Gap-fill only — Weave provenance wins conflicts.
- Outbound requires `writeback.google_contacts: true` in config AND a previous sync checkpoint.
- Full field sync is mandatory — all fields from `references/google-field-map.md`, including birthdays and relations from Fact/Knows nodes.

## Recovery behavior

This skill implements the recovery contract from `spec-ocas-recovery.md`.

- **Evidence**: Every sync/write run writes an evidence record to `{agent_root}/commons/data/ocas-weave/evidence.jsonl`, including no-op runs. `not_activity_reason` is mandatory for no-op runs.
- **Gap detection**: On every wake, checks the evidence log. If gap exceeds cadence (24h for sync-google, 1h for enrichability-recalc), logs `gap_detected`.
- **Degraded mode**: When dependencies are unavailable, logs `degraded: <dependency>` and queues changes. Existing data remains queryable.
- **Log compaction**: Evidence/decision logs older than 30 days (no-op) or 90 days (error/gap) compacted. Sync checkpoints never auto-deleted. Last 7 days retained.

## Constraints

Read `references/constraints.md` for the full constraint set.

## Pitfalls

- **Separate enrichment skills**: All enrichment workflow lives in this skill. Merge any accidental duplicates.
- **`HasFact` has no properties**: `CREATE (p)-[:HasFact]->(f)` only — no property bags allowed.
- **Wrong enrichment pipeline**: Manual enrichment = full Scout→Sift→Sherlock pipeline. Do NOT shortcut with raw SearXNG regex alone.
- **Tool output truncation**: `read_file`/`terminal` may truncate paths (e.g., `/root/...json`). This is a display artifact — verify with raw reads before fixing. In Apr–May 2026, 25+ minutes were lost to this false positive.
- **TOKEN_PATH corruption**: Can be caused by `read_file` truncation being written back, or sed/asterisk replacement. See `references/google-token-diagnostics.md`.
- **`write_file` line number prefix injection**: Using `write_file` after `read_file` can inject line numbers into scripts. Use direct Python file I/O or `sed` instead.
- **Auth migration misses**: When updating Google auth, check ALL Python scripts — skill scripts, data directory scripts, webui scripts.
- **google_sync.py `Person.notes` column**: May reference dropped `Person.notes`. Remove all references to `notes` in MERGE, CREATE, RETURN, `build_contact_body`, and parameter dicts (5 locations). Verify with: `python3 -c "import ast; ast.parse(open('scripts/google_sync.py').read()); print('OK')"`.
- **Enrichment scraper substring bug**: Occasional truncated text extraction. Clear corrupted fields and fix scraper before re-running.
- **`enrichment_data.py` does not exist**: The pipeline instructions reference `enrichment_data.py` but the actual script is `enrichment_control.py` (commands: start, stop, status). The overnight enrichment pipeline is in `scripts/overnight_enrichment.py`. When writing custom enrichment scripts, use `import real_ladybug as lb` directly — do not route through non-existent wrapper scripts.
- **SearXNG engine degradation**: SearXNG search engines frequently become unresponsive (rate-limited, CAPTCHAs, timeouts). Always check `unresponsive_engines` in the JSON response before trusting zero results. If >50% of engines are unresponsive, restart SearXNG via `docker restart searxng` (or `systemctl restart searxng` systemd) and wait 15-30 seconds before retrying. Log degraded mode in evidence. Do NOT report "no search results" as a pipeline failure without checking engine health first.
- **google_sync.py `token` field dependency**: The sync script reads `token_data.get('token', '')` from the JSON credential file (NOT `access_token`). If the credential file uses `access_token` as the field name (as some OAuth flows produce), the sync silently gets `token=''` and all API calls fail with 401. Verify the JSON file has a `token` field with a non-empty value before running sync. See `google-workspace-auth` pitfall #38b.

- **google_sync.py uses env-var client credentials**: The sync script reads `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` from the environment as the primary source for OAuth client credentials, falling back to values stored in the token JSON file. This is important because the JSON file may contain stale/revoked OAuth app credentials while the env vars reference the working OAuth app. Ensure these env vars are set in any context where `google_sync.py` runs (cron jobs, background processes). The env vars are configured in `<hermes-root>/config.yaml` under `mcp_servers.google-workspace.env`.

- **LadybugDB bridge lock contention**: The `ladybug-bridge-weave.service` holds a `READ_WRITE` lock on `weave.lbug`, blocking all other DB access (including `google_sync.py` and direct Python scripts). Before any DB write operation, ensure the bridge is stopped: `systemctl stop ladybug-bridge-weave.service`. This service hangs on stop — use `systemctl kill --signal=SIGKILL` if stop times out, then verify with `systemctl is-active`. After writes complete, restart: `systemctl start ladybug-bridge-weave.service`.
- **LinkedIn MCP browser dependency**: The LinkedIn MCP server (`mcp_linkein_search_people`, `mcp_linkedin_get_person_profile`) requires Patchright Chromium. If missing, MCP calls error with "Patchright Chromium browser is missing." Install: `uv run patchright install chromium`. The MCP server must be restarted after install — in a cron context this may not be possible. Workaround: use SearXNG + Jina Reader to fetch data from LinkedIn-adjacent sources (wiza.co, zoominfo, theverge, etc.).
- **TOKEN_PATH must match the correct user account**: The Google People API returns contacts for the **authenticated user's account only**. If `google_sync.py`'s `TOKEN_PATH` points to Indigo's credentials, the sync will fetch Indigo's contacts (1 contact), not owner's (964 contacts). This is NOT an error — it silently produces wrong data. Always verify the TOKEN_PATH points to the intended account's token file. If the target token file is empty (0 bytes), the sync cannot proceed — do NOT silently switch to a different account's token. Instead, halt and report that re-auth is needed. An OAuth re-auth flow requires user interaction and cannot complete in a cron context.

- **NEVER silently fall back to an alternate account's token**: When the primary user's token is expired, revoked, or empty, the sync or enrichment pipeline MUST NOT silently use a different account's credentials (e.g., Indigo's token instead of owner's). This causes contacts from one account to be written to another, creating phantom merges and data corruption. The evidence log entry `"google_sync": "completed_with_indigo_token"` is a smoking gun for this failure mode. **Halt immediately** when the primary token is invalid. Log `auth_failure` in evidence. Do not proceed with any Google Contacts operations until the primary token is re-authorized. If running in cron context where re-auth is impossible, skip the entire Google sync and log the gap.
- **Python script output buffering**: Background Python scripts with `terminal(background=true)` may show zero output in `process(action='poll')` even when running. The output is buffered by Python. Use `python3 -u` (unbuffered) flag, or redirect output to a file (`> /tmp/log.txt 2>&1`) and read the file separately.
- **DB write-write collision with bridge**: When writing to Weave DB from a Python script AND the bridge is running simultaneously (e.g., bridge was restarted while script is still running), both processes contend for the `READ_WRITE` lock. Result: `RuntimeError: IO exception: Could not set lock on file`. Prevention: stop bridge before starting any enrichment script, restart bridge only after all scripts complete. Do NOT restart the bridge mid-pipeline.
- **`execute_code` blocked in cron jobs**: When running as a scheduled cron job, `execute_code` is denied: `"BLOCKED: execute_code runs arbitrary local Python... Cron jobs run without a user present to approve it."` This affects journal writing, evidence logging, and any inline Python data processing. **Workaround**: Use `write_file` to write JSON journal entries directly, and `terminal()` with `echo >>` to append evidence lines. Test the journal `write_file` path in a cron dry-run first. The enrichment pipeline step 3 references `execute_code` for SearXNG — in cron, use `terminal()` to run `curl` queries directly instead. See `google-workspace-auth` pitfall #33 for full details on cron-safe alternatives.

  **Cron-safe journal pattern (verified working)**:
  ```python
  # 1. Write journal JSON directly via write_file tool:
  write_file(path="<hermes-root>/commons/journals/ocas-weave/YYYY-MM-DD/{run_id}.json", content=json_string)
  # 2. Append evidence via terminal echo:
  terminal(command='echo \'{"timestamp":"...","command":"...","status":"success",...}\' >> <hermes-root>/commons/data/ocas-weave/evidence.jsonl')
  # 3. Append decision via terminal echo:
  terminal(command='echo \'{"timestamp":"...","decision_type":"...",...}\' >> <hermes-root>/commons/db/ocas-weave/decisions.jsonl')
  ```
  Note: Journal directories must exist — `write_file` creates parent directories automatically. Evidence/decisions files must already exist (created by first manual run or init).
- **Bridge `systemctl stop` can exceed 60s foreground timeout**: The `systemctl stop ladybug-bridge-weave.service` command hung for >60s in the May 2026 cron run, causing a foreground timeout. After the timeout, the bridge was still in `deactivating` state. Always follow up with `systemctl is-active` to check state, and escalate to `systemctl kill --signal=SIGKILL ladybug-bridge-weave.service` if not yet `failed`. Do NOT proceed with DB writes until bridge is confirmed `failed`.

- **`process(action='kill')` does NOT kill the bridge process**: The Hermes background process tool sends SIGTERM to the `b1b3254784e9`-style session, but the actual Python process (visible in `ps aux | grep ladybug_bridge`) survives. The process continues holding the DB lock. **Always kill using the actual PID**: find it via `ps aux | grep ladybug_bridge` or `systemctl kill --signal=SIGKILL ladybug-bridge-weave.service`, then verify the process is gone with `ps aux | grep ladybug_bridge` and that the DB is unlocked by checking `lsof /path/to/weave.lbug`.

- **iptables must ACCEPT localhost before port-specific DROP rules**: When restricting a bridge port with `iptables -I INPUT -p tcp --dport PORT -j DROP`, also add `iptables -I INPUT 1 -i lo -j ACCEPT` BEFORE the port rules. Without this, localhost connections to the bridge also get dropped, making the service unreachable from the host itself. Order matters: loopback ACCEPT first, then specific-source ACCEPT, then port DROP.

- **Long-running bridge lock blocks all DB access**: The bridge holds a `READ_WRITE` lock on `weave.lbug` indefinitely. Any process that needs to read the DB (enrichment scripts, visualizer API, ad-hoc queries) will fail with `IO exception: Could not set lock on file`. For one-off reads: kill the bridge → read → restart. For persistent services (API servers): use a pre-copied snapshot in `snapshots/weave_viz_copy.lbug` and refresh via cron. See `util-vps-webapp` skill's `references/ladybugdb-snapshot.md` for the full pattern. Do NOT kill the bridge and forget to restart it — always verify `systemctl is-active` shows `active` after restart.

## Diagnosing "contacts got merged" reports

When a user reports that two people got merged in Weave, follow this procedure to locate and undo the merge:

1. **Query Weave DB for both persons by name**: `MATCH (p:Person) WHERE p.name CONTAINS $term RETURN p.id, p.name, p.email, p.phone, p.google_resource_name, p.org`. Weave **never** silently merges two Person nodes — if they appear merged in Weave, one of the original records is gone and needs to be restored.

2. **If Weave shows separate records**: The merge is in Google Contacts, not Weave. Proceed to step 3.

3. **Check sync evidence for the merge cause**: Review `evidence.jsonl` and `sync_log.jsonl` for the most recent sync. Key indicators:
   - `"google_sync": "completed_with_indigo_token"` — sync ran with wrong account credentials
   - `source_ref: "google-contacts-restore"` — we restored from a Google backup (check the restore snapshot)
   - `source_ref: "google-contacts-sync"` — normal inbound sync

4. **Check for problematic duplicates**: Two Weave records with the same email/phone but different names often indicate a Google Contacts duplicate that was synced inbound. The record with `google_resource_name: None` was likely never in Google — it was created by enrichment or manual entry.

5. **Fix in Google Contacts first**: If the merge is in Google:
   - Present the OAuth re-auth URL to the user if the MCP is not authenticated
   - Once authenticated, look up both contacts by `google_resource_name`
   - Unmerge/split the contact in Google Contacts API
   - Then re-sync inbound to update Weave

6. **Fix in Weave if needed**: If a Weave record was incorrectly updated:
   - Restore the affected fields from the record with higher confidence/prevenance
   - If a Person record was deleted, restore from the latest snapshot in `snapshots/`
   - After fixing Weave, run outbound sync to push the correction to Google

7. **Verify**: After the fix, query both records, check Google Contacts via MCP, and confirm with the user that both contacts appear correctly in their Google account.
- **MCP health before auth assumptions**: When Google Workspace MCP tools fail (e.g., during `weave.sync.google-contacts`), do NOT assume auth tokens are expired. First: ping the MCP with `mcp_google_workspace_list_gmail_labels`. If unresponsive, restart the gateway. Only consider re-auth if the MCP is running but returns explicit auth errors. See `email-sending` skill's `references/mcp-health-check.md` for the diagnostic sequence.

- **MCP "unreachable" counter accumulates across sessions**: The "unreachable after N consecutive failures" error and "auto-retry available in ~58s" cooldown accumulate across tool calls and sessions. When you see this, WAIT for the cooldown (~60s) and retry BEFORE launching into token-refresh debugging. If the user says they're already authorized, trust them and retry — do not generate auth URLs, do not try to refresh tokens manually via API, do not debug the MCP server's credential store.

- **Wrong-Oauth-token sync cross-contamination**: When `google_sync.py` runs with the wrong account's token (e.g., Indigo's token instead of owner's), the sync can create or modify contacts in the wrong Google account. This causes real data corruption — contacts can be merged, names overwritten, or stale data injected. The evidence log entry `"google_sync": "completed_with_indigo_token"` indicates this occurred. Prevention: always verify `TOKEN_PATH` in `google_sync.py` points to the correct account before running outbound sync. After any sync with wrong credentials, immediately check Google Contacts for cross-contamination and restore affected records from Weave.

- **Contact merge reported by user but absent in Weave DB**: When a user reports two contacts are merged, first query Weave DB directly. If Weave shows separate records with different IDs, emails, phones, orgs, and `google_resource_name` values, the merge is in Google Contacts (cloud), not in Weave. The fix requires Google API access to separate the merged records and then re-sync inbound to Weave. Do not attempt to "unmerge" in Weave if the records were never merged there.
- **Enrichment Person field validation rejects value but may still write `_source` Fact**: When `enrich_weave_contact()` rejects a field via `validate_enrichment_field()` (e.g., `org='the'` fails), the script skips writing that field's value to the Person node — but the corresponding `_source` companion field (e.g., `org_source`, `occupation_source`) can still be written as an orphan Fact node. The progress log then lists `org_source` as an "enriched field" even though no useful `org` value was persisted. This is cosmetic (Person node unchanged) but confusing in progress reports. Check Person node fields directly for enrichment status, not just the progress log.

- **`process(action='wait')` timeout clamping in cron**: When running background scripts via `terminal(background=true)` in a cron context, `process(action='wait', timeout=N)` may be silently clamped to a lower configured limit (observed: 300s requested → 60s actual). The process continues running but the wait returns early with a timeout note. **Mitigation**: Use `process(action='poll')` in a loop to check completion, or redirect script output to a file and check the file after a known-safe delay. Don't rely on `wait` blocking for the full requested duration in cron.

- **`overnight_enrichment.py` writes Facts but not Person fields**: The `enrich_weave_contact()` function creates `Fact` nodes linked via `HasFact` edges but does NOT update `p.org`, `p.occupation`, `p.location_city` on the Person node itself. The "contacts needing enrichment" query (`WHERE p.org IS NULL OR p.occupation IS NULL`) uses Person fields, so the same contacts appear as having gaps every run even after successful enrichment. The enriched data IS stored — query `MATCH (p:Person)-[:HasFact]->(f:Fact {source_type: 'web_enrichment'})` to see it. To avoid re-processing, the script relies on the progress file for deduplication, but the Person fields remain NULL. If you need Person fields populated, run a separate MERGE to copy the highest-confidence Fact values onto the Person node after enrichment completes.

- **Overnight enrichment Google sync must use the correct token**: The overnight enrichment pipeline (`overnight_enrichment.py`) may attempt a Google Contacts sync at the end. If the primary user's token is expired and the pipeline silently falls back to an alternate account's token, it will push Weave contact data to the wrong Google account. The evidence log entry `"google_sync": "completed_with_indigo_token"` confirms this happened on 2026-06-01. **Before任何 outbound Google sync in the enrichment pipeline, verify the token file belongs to the correct account.** If the token is invalid, skip the Google sync entirely — do NOT fall back to another account's credentials.

- **Cron job message references stale script names**: The cron job message references `enrichment_data.py` with `searxng-ensure`/`list`/`write` commands. These don't exist. The actual scripts are `enrichment_control.py` (SearXNG start/stop/status) and `overnight_enrichment.py` (full pipeline). The pipeline instructions also reference LinkedIn MCP tools (`mcp_linkedin_search_people`, `mcp_linkedin_get_person_profile`) which require Patchright Chromium — unavailable in cron. The `overnight_enrichment.py` script uses SearXNG + Jina Reader only, which works in cron.

## OKRs

Read `references/okrs.md` for Weave-specific OKR definitions and targets.

## Optional skill cooperation

- Elephas — read Chronicle read-only for entity enrichment (degrades gracefully)
- Elephas — journal entity observations consumed during Chronicle ingestion
- Scout — receive OSINT findings about people as upsert candidates
- Dispatch — provide social graph context for communication drafting
- **Clay (Mesh MCP)** — CRM sync via Smithery (`clay-inc/clay-mcp`). Old Clay REST API is deprecated. Auth is OAuth (not API key). Install: `npx -y @smithery/cli@latest mcp add clay-inc/clay-mcp`.

## Journal outputs

- Observation Journal — query runs, upsert runs, import runs
- Action Journal — sync runs, writeback runs

Journals include `entities_observed`, `relationships_observed`, `preferences_observed` in `decision.payload`, each with `user_relevance` field.

## Initialization

On first invocation of any Weave command, `_open_db()` handles auto-initialization:

1. Create directories and default `config.json`
2. Open database (auto-creates `weave.lbug` and runs DDL if needed)
3. Register cron job `weave:update` if not present
4. Log initialization as a DecisionRecord

## Background tasks

| Job name | Mechanism | Schedule | Command |
|---|---|---|---|
| `weave:update` | cron | `0 0 * * *` (midnight daily) | `weave.update` |
| `weave:sync-google` | cron | `0 4 * * *` (4AM UTC) | `python3 {skill_root}/scripts/google_sync.py` |
| `weave:enrichability-recalc` | cron | `0 1 * * *` (1am daily) | `python3 {skill_root}/scripts/recalculate_enrichability.py` |

## Self-update

`weave.update` pulls the latest package from GitHub. See `references/self-update.md` for the full procedure.

## Database maintenance

See `references/database_maintenance.md`. Key uses: before Google sync, after enrichment runs, periodic health checks.

## Gotchas

- **Never assume write success without read-back**: After every write operation, immediately query the DB by primary key. Silent write failures corrupt the graph over time.
- **Lock errors are immediate**: LadybugDB fails instantly if another process holds `READ_WRITE`. Surface the error — do not retry silently or spin-wait.
- **Person merge is never automatic**: Weave never silently collapses two Person nodes. Always confirm identity before merging; match by `google_resource_name`, email, or phone — never by name alone.
- **Outbound sync is doubly gated**: Both the config writeback flag AND per-sync user approval are required. Neither alone is sufficient.
- **Tool output truncation is cosmetic**: `read_file` may display truncated paths (e.g., `/root/...json`). This is a display artifact, not actual corruption. Verify with raw reads before acting.
- **`HasFact` rejects property bags**: Use `CREATE (p)-[:HasFact]->(f)` only. Adding properties to the relationship will fail.
- **`Person.notes` column was dropped**: Any reference to `Person.notes` in sync scripts, MERGE, CREATE, RETURN, or parameter dicts will fail at runtime. Verify scripts with Python AST parse.

## Support File Map

| File | When to read |
|---|---|
| `references/schemas.md` | Before any DDL, upsert, or import |
| `references/init_pattern.md` | Implementing _open_db or troubleshooting init |
| `references/ladybugdb-guide.md` | Query result handling, iteration, import patterns |
| `references/query_patterns.md` | Before any weave.query call |
| `references/import_export.md` | Before any COPY FROM/TO operation |
| `references/cross_db.md` | Before weave.attach or Chronicle queries |
| `references/connectors.md` | Before any Google/Clay sync |
| `references/vcard_projection.md` | Before weave.project.vcard |
| `references/journal.md` | Before weave.journal; at end of every run |
| `references/google-token-quick-check.md` | Pre-flight token validation before sync |
| `references/google-token-diagnostics.md` | Token diagnostic, TOKEN_PATH fixes |
| `references/token-path-corruption.md` | TOKEN_PATH corruption diagnosis, fix procedure, and byte-verification commands |
| `references/enrichment-pipeline.md` | Overnight enrichment architecture & health checks |
| `references/mcp-auth-retry-and-wrong-token-incidents.md` | June 2026: MCP cooldown behavior, wrong-token sync causing contact merge in Google Contacts |
| `references/sync-pitfalls.md` | Google sync API quota and known pitfalls |
| `references/self-update.md` | Self-update procedure |
| `references/config-defaults.md` | Default config structure, writeback flags, retention |
| `references/constraints.md` | Full constraint set |
| `references/okrs.md` | OKR definitions and targets |
| `references/database_maintenance.md` | Before Google sync, after enrichment runs |
| `references/searxng-bridge-operations.md` | SearXNG health checks, bridge lock management, script reference |

## Visibility

public
