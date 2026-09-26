# Support File Index

**Read this before assuming a file or tool does not exist.** Two tables: the
ones you will actually reach for, and the long tail of one-off repair/audit
helpers.

## Reach for these first

| File | When to read |
|------|-------------|
| `references/schemas.md` | Before any DDL, upsert, or import — Python usage pattern and schema |
| `references/gotchas-weave.md` | Before any Weave operation — full gotcha catalog |
| `references/pitfalls-weave.md` | When debugging enrichment failures, data quality issues, or migration residuals — 50+ pitfalls covering all subsystems |
| `references/query_patterns.md` | Before any weave.query call — SQL templates for all modes |
| `references/connectors.md` | Before any Google/Clay sync |
| `references/sqlite-backend-research.md` | Storage backend details, migration notes, SQLite schema |
| `references/enrichment-pipeline.md` | Overnight enrichment architecture, SearXNG retry pattern |
| `references/enrichment-write-pattern.md` | **Exact SQLite write pattern for agent-driven enrichment** — three-step (persons UPDATE → facts INSERT → edges INSERT), cron-mode terminal usage, read-back verification |
| `references/cron-pipeline-runbook.md` | **The correct step-by-step runbook for agent-driven enrichment** — modern pipeline (no LadybugDB bridge, no enrichment_data.py), cron-mode terminal usage, confidence scoring guide, read-back verification pattern |
| `references/constraints.md` | Full constraint set |
| `references/config-defaults.md` | Before reading `config.json` or changing a default — default config structure and precedence |
| `references/self-update.md` | When the skill is stale, or after any fleet update — the retired `weave.update`, where the centralized updater lives, and the two silent failure modes (broken frontmatter, phantom refs) |
| `references/cron-mode-notes.md` | **When running under cron** — stale-runbook override table, agent-driven enrichment checklist, schema reminder, `execute_code`-blocked constraint |
| `references/okrs.md` | When asked whether enrichment quality is on target — OKR definitions and thresholds |
| `references/init_pattern.md` | On first invocation or when the DB fails to auto-init — `_ensure_init()` pattern |
| `references/import_export.md` | Before any `weave.import.csv` or `weave.export` — `COPY FROM`/`COPY TO` patterns |
| `references/database_maintenance.md` | Before any destructive repair, purge, or merge — audit, repair, and rollback procedures |
| `references/graph-storage-backend-research.md` | When revisiting the storage backend — evaluation of alternatives to LadybugDB, why SQLite adjacency lists won |
| `references/enrichment-data-quality.md` | When enrichment output looks wrong — data quality patterns, garbage categories, validation rules, SearXNG reliability |
| `references/unresolvable-contacts.md` | **Unresolvable contacts protocol** — when to skip (common name, no disambiguator, multiple conflicting profiles), identity resolution ladder, confidence thresholds, log format |
| `references/enrichment-operations.md` | When enriching a specific contact by hand, or hitting a sync/merge problem — manual pipeline, Google match rules, constraints, gotcha catalog |
| `references/enrichment-pipeline-execution.md` | **Before starting an enrichment run** — pre-flight checklist (inbound sync, SearXNG health, discovery probe, junk sweep, stale-DB removal, edges FK) and per-direction I/O examples |
| `references/error-handling.md` | When something failed — failure → symptom → handling table, the three costliest failures, and the destructive-operation rule |
| `references/recovery-weave.md` | When a sync or writeback half-completed — the recovery contract |
| `references/discovery-fallback.md` | **When Scout sources are degraded/unavailable** — SearXNG backoff pattern, DuckDuckGo HTML scrape recipe, page-fetch options, and the no-fabrication defer path. Read before any enrichment run where web_search/SearXNG/LinkedIn MCP are suspect. |
| `references/support-file-map.md` | When you need a script not listed here — per-script one-line index of the ~70 repair/audit helpers |
| `references/.archive/` | Never — retired material kept for history; do not follow pointers into it |
| `scripts/weave_sqlite.py` | SQLite backend module — import `WeaveDB` from here |
| `scripts/google_api.py` | Shared Google OAuth + API helpers — import `get_access_token`, `api_get`, `api_post`, `api_patch`, `PEOPLE_API_BASE` from here. All scripts that talk to Google APIs should use this module, not duplicate auth logic. |
| `scripts/weave_enrich.py` | Shared enrichment extraction, search, and validation. Contains `searxng_search`, `fetch_page`, `extract_from_content`, `validate_field`, `is_auth_walled`, `build_scout_queries`. Used by both `quick_enrich.py` and `overnight_enrichment.py` — do not duplicate this logic in individual scripts. |
| `scripts/quick_enrich.py` | For a single high-value contact interactively (`--help` for flags); the batch equivalent is `overnight_enrichment.py` |
| `scripts/overnight_enrichment.py` | For a scheduled batch enrichment run — reads `WEAVE_BATCH_SIZE`, `WEAVE_DEADLINE_HOUR_ET`, and the cooldown gates |
| `scripts/google_sync.py` | **The only sanctioned Google sync path.** Requires `AGENT_ROOT` + `HOME`; exits 2 on a revoked token. Never hand-roll a second sync. |
| `scripts/dryall.py` | **Before any `google_sync --apply`** — read-only payload dry-run; reports field coverage and anomalies, exits 1 if any found |
| `scripts/merge_persons.py` | When two `persons` rows are one human — requires corroborating identifiers, never a name match |
| `scripts/verify_integrity.py` | After any repair/merge/purge — knows which table each edge type points at |
| `scripts/people_db.py` | Before reading the Choice-2 People database — the access layer and its invariants |
| `scripts/people_to_google.py` | Before changing outbound payload shape — person → clean Google contact-card mapping |
| `scripts/audit_quality.py` | When a whole-sweep quality review is needed — many defect classes in one pass |
| `scripts/test_weave_enrich.py` | After changing `weave_enrich.py` — adversarial tests, every fixture a real observed failure |
| `scripts/test_temporal.py` | After changing temporal/supersede logic — run on throwaway DBs, `-k` to filter; production never touched |
| `scripts/test_company_gate.py` / `test_employer_gate.py` / `test_contact_urls.py` / `test_diacritics.py` | After changing the corresponding gate or URL logic — each encodes a real regression |
| `scripts/README.md` | Before choosing or running any script — CLI-vs-library map, usage conventions, and dry-run/destructive-operation rules |
| `scripts/discovery_probe.py` | Run at pipeline start to test which discovery sources are live (SearXNG, DDG, notes on web_search/LinkedIn MCP). Decides proceed / fall back / defer. |
| `scripts/restore_google_urls.py` | When a URL purge over-rejected — dry-run by default, `--apply` to merge the URLs back |

## One-off repair and audit helpers

One line per bundled script that is not in the table above. These are
single-purpose tools from specific repair sessions; read the line to decide
whether one applies, then run it with `--help`.

| File | Notes |
|------|-------|
| `scripts/audit_deep.py` | Second-pass audit: classes the first sweep did not look for. The first audit is down to three classes, two ... |
| `scripts/audit_identifiers.py` | Where is a person's NAME acting as their identifier? A name is not an identity: two people share one, one p... |
| `scripts/audit_quality.py` | Sweep the whole store for data-quality defects, many classes at once. Finding one defect class per enrichme... |
| `scripts/classify_job_junk.py` | How much of org/occupation is scrape junk, and can it be detected precisely? The examples the operator gave... |
| `scripts/clean_dead_relations.py` | The 155 relation edges whose target person no longer exists. Unlike the dead HasFact edges (a live leak, no... |
| `scripts/clean_fact_junk.py` | The junk rules were only ever applied to the contact columns, not the graph. Earlier passes cleaned persons... |
| `scripts/clean_job_junk.py` | Clear scrape junk out of org/occupation, in weave and in Google. 63 values flagged by job_junk_v3, minus tw... |
| `scripts/clean_mangled_emails.py` | Retire the 'website' facts that are really mangled email addresses. The old inbound classifier prepended a ... |
| `scripts/clean_org_junk.py` | Clear the company-field junk, in weave and Google. Held back from the flagged set after review: Ramp 💳 -> '... |
| `scripts/clear_backlog.py` | Clear what the four defects already wrote. B 547 `website` facts that are our own pushed profile URLs, re-i... |
| `scripts/clear_fake_employers.py` | Clear employers that are provably not employers. Only values that can be judged wrong from the record itsel... |
| `scripts/clear_unbacked_orgs.py` | Employers on the visible record that nothing in the system ever claimed. This is deliberately NARROWER than... |
| `scripts/company_gate.py` | Decide whether a contact record is an organisation rather than a person. A company is never enriched. Perso... |
| `scripts/company_label.py` | Classify Google contacts as companies (not people), then: 1. add them to a Google contact group ("label") n... |
| `scripts/contact_extras.py` | Import the Google Contacts fields weave was fetching and discarding. sync_inbound already asked the People ... |
| `scripts/contact_urls.py` | Import hand-curated URLs/biographies from Google Contacts into weave. Google's People API already returns `... |
| `scripts/corroborate_employment.py` | Which enrichment-added employers are corroborated by anything? 'Heriot-Watt University' was not junk-shaped... |
| `scripts/dedupe_facts.py` | Collapse exact duplicate facts: same person, same predicate, same value. These came from check-then-write w... |
| `scripts/dryall.py` | Usage: dryall.py [--help] |
| `scripts/employer_gate.py` | An inferred employer may only reach the contact record if something agrees. 'Heriot-Watt University' was no... |
| `scripts/employment_provenance.py` | Where does the employment data actually come from? 'Heriot-Watt University' for a Bay Area PM was not junk-... |
| `scripts/find_junk_values.py` | Locate the values the operator named, wherever they live, and dump the occupation values so the junk is vis... |
| `scripts/fix_google_urls.py` | Re-canonicalise and de-duplicate the urls on GOOGLE contacts. weave was repaired already; Google still hold... |
| `scripts/fix_identity.py` | Stop treating a person's NAME as their identifier. persons.id is a UUID on all 978 rows, so the core identi... |
| `scripts/fix_laith.py` | Undo the duplicate Laith Ulaby contact, and stop the check that let it through. The create path skips a wea... |
| `scripts/fix_linkedin_misattrib.py` | Remove LinkedIn profiles that belong to a different person. A LinkedIn /in/ slug is built from the owner's ... |
| `scripts/fix_names_places.py` | Two more classes from the audit. A. Three contacts whose NAME is an email address. A name field holding 'so... |
| `scripts/fix_orphan_edges.py` | Stop the nightly enrichability job orphaning one edge per contact per night, and clear the 25,585 it has al... |
| `scripts/fix_pairs.py` | Repair or clear the org/title pairs that are one string split in two. Decided per contact rather than by ru... |
| `scripts/job_junk_rules.py` | Precise detectors for scrape junk in org/occupation. My first pass used three crude rules and each had a la... |
| `scripts/job_junk_v2.py` | Detect scrape junk in org/occupation, by structure rather than by vocabulary. Reading all 693 distinct occu... |
| `scripts/job_junk_v3.py` | Scrape-junk detection for org/occupation, third attempt. v2 had two false-positive classes big enough to ma... |
| `scripts/merge_certain.py` | Merge every weave person group that is a duplicate by construction. A group is certain when two or more row... |
| `scripts/merge_google_pairs.py` | Merge the remaining duplicate GOOGLE contacts, the way Rachel Neurath was done. Ricardo Prada both at Googl... |
| `scripts/merge_persons.py` | Merge two weave person rows that are the same human. Duplicate rows are not merely untidy: they split a per... |
| `scripts/merge_rachel.py` | Merge the two Rachel Neurath google contacts into one, then the two weave rows. A people/c98850802839146440... |
| `scripts/normalize_weave_urls.py` | Bring weave's url facts to the same canonical form as google's. Two facts holding the same link in differen... |
| `scripts/org_junk.py` | Junk in the COMPANY field specifically. The title pass is done. Org has a different failure shape, visible ... |
| `scripts/orphan_halves.py` | Orgs left orphaned when their paired title was cleared as junk. Clearing one half of a split string leaves ... |
| `scripts/pair_junk.py` | Evaluate org and title TOGETHER, not one at a time. The two cases the operator named first survive every si... |
| `scripts/people_db.py` | people_db — clean access layer for the Choice-2 People database. Replaces the ladybug/Weave layer. INVARIAN... |
| `scripts/people_to_google.py` | Map a People-DB person -> a CLEAN Google contact-card payload (Choice-2). Card fields only, properly labele... |
| `scripts/purge_junk_urls.py` | Remove the aggregator / encyclopedia / catalogue URLs enrichment already wrote. Uses the same classifier th... |
| `scripts/rationalize_db.py` | Clean up the store and put data in the field that models it. 1. Pipeline metrics become single-valued. comp... |
| `scripts/rationalize_predicates.py` | One URL, one predicate, and the predicate should name the platform. Two problems: linkedin vs profile_linke... |
| `scripts/repair_urls.py` | Re-canonicalise url facts and collapse duplicates of the same link. Two defects, both visible on one contac... |
| `scripts/resolve_contradictions.py` | One person, one current answer to a single-valued question. 108 contacts currently hold two or more simulta... |
| `scripts/resolve_v2.py` | Give each contact one current value per single-valued field -- but only where the choice is defensible. My ... |
| `scripts/restore_affiliation.py` | Restore facts the pre-affiliation gate wrongly retired. The last purge removed every unknown-host page with... |
| `scripts/restore_google_urls.py` | Put back URLs removed by a purge run that over-rejected. The slug-vs-name rule threw away real handles (git... |
| `scripts/revert_repairs.py` | Undo four "repairs" that were actually fabrications. I took mangled scrape fragments and reconstructed them... |
| `scripts/survey_orgs.py` | Every org and occupation value, so the junk patterns are visible rather than guessed. the operator named fi... |
| `scripts/sweep_blocked_sites.py` | Stop serving facts sourced from sites that give no information. Driven by the measured verdicts in the prob... |
| `scripts/sweep_field_placement.py` | Move contact data into the field it belongs in, and drop custom fields that only restate (or contradict) a ... |
| `scripts/sweep_google_url_quality.py` | Remove from Google the URLs the publish gate would now refuse. Purging by weave provenance missed the ones ... |
| `scripts/sweep_job_fields.py` | Clear job-field junk by TESTING the value, not by matching a remembered string. The sweep matched google's ... |
| `scripts/sweep_name_suffix.py` | Move degree suffixes and pronouns out of the family-name field. Google Contacts has honorificSuffix for 'Ph... |
| `scripts/sweep_url_hygiene.py` | Normalise every contact URL in Google Contacts and remove duplicates. Google holds 3,829 URLs of which ~930... |
| `scripts/sweep_weave_names.py` | Move titles, honorifics and pronouns out of weave's name fields. persons has honorific_prefixes, honorific_... |
| `scripts/tag_sync.py` | Bi-directional tag/label sync: Google contactGroups <-> weave book_tags/book_contact_tags. ADDITIVE ONLY (u... |
| `scripts/test_company_gate.py` | company_gate: a company is never sent to person-OSINT, a person always is. Fixtures are invented. Person na... |
| `scripts/test_contact_urls.py` | Tests for contact_urls. Includes regressions for the two defects the adversarial review reproduced: fragmen... |
| `scripts/test_diacritics.py` | Diacritics are folded for MATCHING only, never in stored or transmitted values. Folding accents made accent... |
| `scripts/test_employer_gate.py` | Cases from this session's real data, both directions. |
| `scripts/test_temporal.py` | Temporal-validity model tests for store_scout_findings. Additive graph: facts are never deleted. Single-val... |
| `scripts/test_weave_enrich.py` | Adversarial tests for weave_enrich. Every fixture here encodes a REAL failure observed on 2026-08-13, when ... |
| `scripts/unlaunder.py` | Undo the enrichment->google->weave round trip. Until the outbound URL query was gated on source_type, every... |
| `scripts/unpack_blob_facts.py` | Facts whose value is a whole serialized payload instead of one statement. A fact is supposed to say one thi... |
| `scripts/url_norm.py` | One canonical form for a contact URL, used by every path that reads or writes one. There were two: the inbo... |
| `scripts/url_quality.py` | Is this URL a page about a PERSON we identified, or just a page containing a name? ocas-scout promotes gene... |
| `scripts/verify_integrity.py` | Integrity check that knows which table each edge type points at. Twice now a check that assumed one target ... |
| `scripts/weave_full_sync.py` | Full Weave → Google Contacts outbound sync. ALL mapped fields per google-field-map.md. Queries Person nodes... |
