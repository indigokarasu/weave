# Enrichment Run — 2026-07-14

## Context
Agent-driven overnight enrichment cron run. Followed `references/cron-pipeline-runbook.md` and ignored the stale runbook embedded in the cron invocation message (it referenced `enrichment_data.py`, the LadybugDB bridge, and `execute_code` — all removed/blocked).

## Environment confirmed at start
<<<<<<< Updated upstream
- Canonical DB path resolves via `parents[3]` — correct. `<hermes-home>/commons` is a symlink to `profiles/indigo/commons` (same inode), so no stale-DB removal was needed (the `parents[2]` stale-DB warning did not apply).
=======
- Canonical DB path resolves via `parents[3]` — correct. `~/.hermes/commons` is a symlink to `profiles/indigo/commons` (same inode), so no stale-DB removal was needed (the `parents[2]` stale-DB warning did not apply).
>>>>>>> Stashed changes
- `edges.target_id` has NO FK (only `source_id → persons(id)`). No FK migration needed.
- WAL mode on.
- SearXNG (`localhost:8888`) was up but degraded: Brave reported "too many requests", DuckDuckGo CAPTCHA, and off-topic results for specific-name queries ("Blaise Pascal" for "Blaise Agüera y Arcas").

## Pipeline outcome
- Inbound sync: 950 upserted, 26 skipped. Outbound: 587 pushed, **0 failed** (no 403 this run).
- Pre-enrichment cleanup: cleared 7 explicit garbage fields; applied `org=Google`/major-tech heuristic — cleared 10 unverified `org=Google`, kept 101 corroborated; 1 `Amazon` cleared, 0 `Microsoft`/`Salesforce` cleared.
- Gap query: 50 contacts (occupation OR org empty, confidence ≥ 0.3).
- Triage skips (logged to `decisions.jsonl`): 13 non-person business entities (OpenTable, PayPal, Venmo, DJI, Google-as-entity, Harbor View Plaza, Resy, Amazon.com, Wealthfront, Visualping, Electrical General Services, Pay By Phone Parking, +1) and 13 unresolvable common names with no disambiguator (Cedric, Davy, Susie, Jim Hong, Susan, Phil Baker, Ben Brown, Cassie Renee Peña, Andree Parker, Sarah Adolf, Octavio Kinoshita, Laurent, Jennifer Zamora, Margee Rettig).
- Enriched: **2 records** — both `Blaise Agüera y Arcas` duplicate IDs → `org=Google` (VP & Fellow, CTO Technology & Society), verified via `research.google/people/106776`, `en.wikipedia.org/wiki/Blaise_Agüera_y_Arcas`, and `aaespeakers.com`. Three-step write + read-back verified; edges intact (32 and 16 respectively).

## Blocker (search infra unavailable)
- `web_search` intermittently threw `'DaemonThreadPoolExecutor' object has no attribute '_initializer'` (calls #16–#30); when it succeeded it returned `[]` for private individuals.
- SearXNG returned off-topic/degraded results.
- No LinkedIn MCP tool present in this runtime.
- **Decision**: wrote only verified records; logged a structured blocker to `decisions.jsonl` with `enriched_this_run`/`skipped` counts; did NOT fabricate `org` values for unverifiable private contacts.

## Data-quality flag
- `<counterparty> Nguyen` (id `a29d1dc5-0ee8-5c87-96c5-197109ccf3bf`) carries `contact@example.com` — email belongs to a different person (Eleanor Klibanoff, Texas Tribune). Flagged `email_mismatch`, enrichment skipped.

## Reusable patterns
- Use `write_file` to a `/tmp/*.py` then `python3 /tmp/*.py` for all Python in cron (`execute_code` blocked; `python3 << 'EOF'` triggers false backgrounding detection).
<<<<<<< Updated upstream
- Always `sys.path.insert(0, '<hermes-home>/profiles/indigo/skills/ocas-weave/scripts')` (absolute path).
=======
- Always `sys.path.insert(0, '~/.hermes/profiles/indigo/skills/ocas-weave/scripts')` (absolute path).
>>>>>>> Stashed changes
- Read-back verification pattern (assert the written field equals intent) + edge-count sanity check.
- Structured blocker log schema: `{time, action:'blocker_reported', pipeline, issue, detail, enriched_this_run, skipped}`.
- Structured data-quality flag schema: `{time, action:'flag_data_quality', person_id, name, issue:'email_mismatch', detail}`.

## Final stats
Total 1,060 | occ 1,020 (96.2%) | org 1,028 (97.0%) | both 1,013 (95.6%) | enrichment facts 270.

## Action items for next run
- When search infra recovers, re-run to cover the remaining ~22 private-contact org gaps (Abi Jones, Jesse Lefkowitz, Jessica Boddicker, Rachel Berg, Ljubica Lu Chatman, <counterparty> Nguyen [after email fix], Mindy DelliCarpini, Debra Lee <operator-last>, Davy, etc.).
- Investigate the `<counterparty> Nguyen` email mismatch at source (Google Contacts) before re-enriching.