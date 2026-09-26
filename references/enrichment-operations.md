# Enrichment operations

Detail behind the SKILL.md enrichment section. Read the relevant block when
you need it — not before every run.

## Manual enrichment of a high-value contact

Use this when a person matters enough to check by hand rather than batch. The
batch paths are `quick_enrich.py` (one contact) and `overnight_enrichment.py`
(a scheduled sweep); this is the reasoning order both follow.

- [ ] **Pre-Search Seed Quality Check** — check `source_type` and `confidence` on the existing record. A low-confidence seed means the search will target the wrong person.
- [ ] **Read** — query Weave for the existing Person record. Never search first: you need to know what you already believe.
- [ ] **Search** — SearXNG, identity-resolved (full name + employer or city, not a name alone).
- [ ] **Enrich (Scout → Sift → Sherlock)** — full pipeline.
- [ ] **Write** — MERGE on Person, CREATE on Fact/Preference. Always read back.
- [ ] **Search again** — a second pass with the enriched data resolves people the first pass could not.
- [ ] **Sync** — after any write touching a mapped field, sync to Google Contacts.

## Shared script surface

Extraction, search, and validation live in `scripts/weave_enrich.py`;
`quick_enrich.py` and `overnight_enrichment.py` both import it. Do not
re-implement extraction in an individual script — the extraction bugs were
each found and fixed once, in that module, and a second copy reintroduces
them.

- Contacts with gaps: query the Weave DB directly (`query_patterns.md`).
- SearXNG health: the diagnostic curl in `enrichment-pipeline.md`.
- Skip rule: contacts where both `occupation` AND `org` are null AND
  `confidence` < 0.5 — nothing to corroborate an identity with.

## Google Contacts sync rules

Full detail in `connectors.md`. The three that matter most:

- **Match order** is `google_resource_name`, then email, then phone. **Never
  match on name alone** — two people share a name, and a name-only match
  merges a stranger's contact into your record.
- **Gap-fill only.** Weave provenance wins every conflict; Google never
  overwrites a provenance-backed value.
- **Outbound** requires `writeback.google_contacts: true` (the default) AND a
  previous sync checkpoint. Without a checkpoint there is no baseline to diff
  against, so the sync would push the whole book.

Enrichment-derived values are **withheld** from outbound on purpose: Weave's
guesses must not become Google's record, where the contact's own tooling
would treat them as confirmed.

## Unresolvable contacts

Full decision flow in `unresolvable-contacts.md`. The short form: when a name
is common and no email, phone, or location disambiguates, **skip**. Never
guess a match. Log to `decisions.jsonl` with reason `skip_unresolvable` — the
log is what lets a later run revisit the case with better evidence.

## Constraints

Full set in `constraints.md`.

## Gotchas

Full catalog in `gotchas-weave.md`, including:

- SQLite WAL mode and concurrent access
- Google OAuth token handling and cross-account contamination
- LinkedIn profile fetching
- Python environment (no `liblbug.so` needed since the SQLite migration)
- **Cron mode**: `execute_code` is BLOCKED in cron jobs — use `terminal` with
  inline `python3 -c "..."` or a script path. Foreground `terminal` times out
  at 600s; long syncs need `background=true` with `notify_on_complete=true`.
- Contact merge diagnosis and repair

## Recovery

Full contract in `recovery-weave.md`.
