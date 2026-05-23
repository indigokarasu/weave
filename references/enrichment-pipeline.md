# Overnight Enrichment Pipeline

Script: `{skill_root}/scripts/overnight_enrichment.py`
Logs: `{agent_root}/data/weave-enrichment/run.log`
Progress: `{agent_root}/data/weave-enrichment/progress.jsonl`
Recalculation: `{skill_root}/scripts/recalculate_enrichability.py` (run nightly at 1am ET via cron)

## Architecture — 3-phase Scout → Sift → Write pipeline

1. **Scout Phase** (`searxng_search` + `build_scout_queries`): Identity-resolved SearXNG search using name + org. Builds targeted queries like `"First Last" LinkedIn` and `"Name" Company`. Returns URLs and snippets.

2. **Sift Phase** (`sift_extract_from_pages`): Fetches full page content from the top 3 non-auth-walled URLs using direct HTTP fetch (fast path) with Jina Reader fallback for JS-heavy sites. Extracts structured data (occupation, org, location, email) from the **full page content** — NOT from search snippets. This is the critical fix: the old code used regex on 160-character search snippets, which produced truncated/garbage data.

3. **Write Phase** (`enrich_weave_contact`): Validates extracted fields, writes as Fact nodes with full provenance (source_url, source_type, confidence, record_time), recalculates enrichability_score.

## Key fixes from the old pipeline

- **No more snippet regex**: The old `extract_info_from_search()` applied regex to search result snippets (title + ~160 char content), producing truncated fields like `"r Vice President"` and `"St"` instead of `"Stanford"`. The new `sift_extract_from_pages()` fetches full pages.
- **No `fact_key` on HasFact**: The old code used `CREATE (p)-[:HasFact {fact_key: $key}]->(f)` but `HasFact` has no properties in the schema. Fixed to `CREATE (p)-[:HasFact]->(f)`.
- **Source URL tracking**: Each extracted field now stores its source URL in `source_ref` for provenance.
- **Auth-walled domain skipping**: LinkedIn, Twitter/X, Facebook, Instagram are skipped during page fetch (they return login walls).

## Re-processing pitfall

The progress file tracks all contacted person IDs, but ~65% of searches return "no extractable data." If the filter excludes ALL progress-file IDs permanently, contacts that failed enrichment are never retried.

**Do NOT filter by progress file at all.** The enrichment logic (`enrich_weave_contact`) only fills fields that are currently NULL/empty in the database — writing the same value twice is harmless. Filtering by progress entries caused a bug (Apr 2026): contacts with partial enrichment (e.g., `location_city` found, but `org` and `occupation` still missing) were permanently excluded because they had a non-empty `fields` entry in progress.jsonl. The simplest correct approach: query contacts with gaps directly from the database, skip no one, and let the SET clause only fill what's missing. The progress file should be used for logging/monitoring only, not for filtering candidates.

## Progress file duplicate monitoring

Health checks must verify progress.jsonl duplicate rate (unique contact IDs / total entries) stays below 10%. Higher rates indicate the script is incorrectly filtering by progress file. If duplicates exceed 10%, truncate progress.jsonl and patch the script to remove any progress-file-based filtering.

Note: progress.jsonl uses the `id` field (not `contact_id`) for contact identifiers. When counting unique contact IDs, parse the `id` key from each JSON line in the file.

Recurring errors in progress.jsonl (e.g., `Connection.execute() got unexpected keyword argument 'occupation'`) indicate script bugs; truncate the file to clear stale entries and patch the script.

## Enrichment Pipeline Health Check

Run these checks periodically (e.g., via cron) to verify pipeline health:

1. Check if enrichment process is running: `ps aux | grep overnight_weave_enrichment | grep -v grep`
2. If not running and before 6am PDT, restart: `python3 {agent_root}/scripts/overnight_weave_enrichment.py`
3. Check progress.jsonl duplicates: Count unique `id` values vs total entries; truncate if duplicate rate >10%
4. Check enrichment stats: `cat {agent_root}/data/weave-enrichment/stats.json`
5. Check last sync time: `cat {agent_root}/commons/db/ocas-weave/config.json | grep last_sync`
6. Check recent sync activity: `tail -5 {agent_root}/commons/data/ocas-weave/sync_log.jsonl`
7. Verify Google token scopes: Ensure `contacts` (or full URI `https://www.googleapis.com/auth/contacts`) is present in token scopes.
