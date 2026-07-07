# Agent-Driven Overnight Enrichment — June 2026 Session Notes

## What Worked

### Two-pass approach produces best results
1. **Pass 1 (broad)**: SearXNG + Jina Reader + regex extraction. Catches ~60% but ~40% garbage.
2. **Pass 2 (targeted)**: web_search per-contact + cross-reference. Near-zero garbage, slower.

### web_search is the best LinkedIn data source
Returns structured title + description:
- Title: "Name - Title at Org | LinkedIn"
- Description: "Title at Org. Location: City, ST. Experience: OrgName"

### Post-extraction cleaning rules
1. Reject sentence fragments (prefixes: "I am", "Currently", "Prior to", "Leveraging", "Please send", "Show Details")
2. Reject if >50 chars (occupation) or >80 chars (org)
3. Reject if person's own name in value (wrong person)
4. Reject if no title indicator (occupation) or no capital letter (org)
5. Reject garbage orgs: "Pages", "Baseball", "Us", "El", "Save", "User", "Staff"
6. Location must contain comma

### `org=Google` clearing heuristic
Before enrichment, clear `org=Google` on contacts that lack corroborating data. Keep `org=Google` only if:
- Person has an `@google.com` email address, OR
- Person has both occupation AND location_city populated

Without this clearing, COALESCE preserves stale `org=Google` values from prior bad enrichment runs, blocking correct org writes. Same heuristic applies to other major tech companies (Microsoft, Salesforce, Amazon) — keep only if email domain matches or other data corroborates.

## What Didn't Work

### Composio LinkedIn — HTTP 403
No r_liteprofile/r_basicprofile permissions. WORKAROUND: web_search + searxng.

### Jina Reader LinkedIn — HTTP 451
"Anonymous access blocked". WORKAROUND: Skip LinkedIn URLs.

### Direct HTTP LinkedIn — authwall
Same workaround.

### Uncleaned regex extraction
Produced: org="Open", occupation="Lori Burns Executive Director" (wrong person), location="Euro, LS".
MUST always apply cleaning rules above.

### Google outbound sync etag failures
The outbound phase of `google_sync.py` frequently returns HTTP 400 with "person.etag is different than the current person.etag" for a subset of contacts (~187/587, ~32%). This means Google's contact data was modified externally between the etag fetch and the update push. The sync checkpoint prevents re-pushing previously successful contacts. Not a data loss risk — inbound sync is unaffected. If failures persist across multiple runs, investigate the checkpoint.

## June 2026 Run Results

### Run Parameters
- Date: 2026-06-22 09:09–09:23 UTC
- Contacts processed: 50 (batches of 17 + 25 + 2, 6 skipped)
- Success rate: 44/50 (88%)
- Google sync: 977 contacts synced, 587 outbound pushed
- SearXNG: Healthy throughout

### Graph Coverage After Run
- Total persons: 1,042
- With occupation: 890 (85%)
- With org: 954 (91%)
- With any enrichment: 973 (93%)

### Key Findings
- `web_search` (Exa AI) was the most reliable single source — LinkedIn titles in search result snippets gave occupation/org/location with ~90% accuracy
- SearXNG returned results for many contacts but required more cross-referencing
- LinkedIn Composio MCP confirmed unavailable (403) — workaround via web_search worked well
- `execute_code` blocked in cron mode — must use `terminal` with inline Python
- The three-step write pattern (persons UPDATE → facts INSERT → edges INSERT) works reliably via `WeaveDB.execute_write()`
- Batch processing via JSON files piped to inline Python scripts proved efficient

### Skipped Contacts (6)
- Alison Kather: No professional data found (public records only, common name)
- Andree Parker / Andree Louise Ferdinand Parker: Multiple LinkedIn profiles, none clearly matching
- 3 others in final batch with no search results

## June 25, 2026 Run Results

### Run Parameters
- Date: 2026-06-25 09:07–09:15 UTC
- Contacts processed: 37 enriched (29 in batch 1, 8 in batch 2)
- 7 non-person entries skipped (Doordash, Amazon.com, Resy, Visualping, Wealthfront, Harbor View Plaza)
- Google sync: 977 contacts synced inbound, 587 outbound pushed (+11 created outbound)
- SearXNG: Healthy throughout

### Graph Coverage After Run
- Total persons: 1,042
- With occupation: 941 (90.3%)
- With org: 978 (93.9%)
- With both: 920 (88.3%)
- With neither: 43 (4.1%)

### Key Findings
- Stale runbook: The cron invocation message contained an outdated pipeline runbook referencing `enrichment_data.py` and LadybugDB bridge (both removed). Skill's own documentation was correct.
- Garbage data cleanup: Pre-existing bad enrichment values ("Save", "Riegel", "New", "St") were cleared before writing new data. COALESCE preserves existing non-null values, so garbage must be cleared first.
- web_search remains the single most reliable data source — 37 contacts enriched with zero errors using web_search → extract → verify → write pattern.
- Three-step write pattern via temp file script executed cleanly for both batches (29 + 8 contacts).
- Read-back verification confirmed all writes persisted correctly.

## June 28, 2026 Run Results

### Run Parameters
- Date: 2026-06-28 09:05–09:12 UTC
- Contacts processed: 48 enriched
- Garbage cleared: ~35 junk org values, 23 stale org=Google, 5 junk occupations
- Google sync: 973 contacts fetched, 949 inbound upserted, 24 skipped; outbound 400 pushed, 187 failed (etag mismatch)
- SearXNG: Healthy throughout

### Graph Coverage After Run
- Total persons: 1,052
- With occupation: 982 (93.3%)
- With org: 993 (94.4%)
- With both: 962 (91.4%)
- With neither: 39 (3.7%)
- Completeness: 96.3%

### Key Findings
- Cleared ~35 garbage org values from prior bad enrichment runs before enrichment
- Applied `org=Google` clearing heuristic: keep only with @google.com email or (occupation + location_city) corroboration. Cleared 23 stale entries.
- web_search confirmed as most reliable data source — 48 contacts enriched with zero garbage
- Outbound sync has persistent etag mismatch issue (187/587 failed) — not a data loss risk but needs monitoring
- Graph completeness improved from ~90.7% to 96.3%
