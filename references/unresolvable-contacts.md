# Unresolvable Contacts Protocol

When processing contacts through the enrichment pipeline, some contacts cannot be identity-resolved via web search. These must be **skipped** — never guess a match.

## When to Classify as Unresolvable

A contact is **unresolvable** when ANY of these conditions apply:

1. **Common name + no disambiguating data**: Name like "Contact A", "Contact C", "Contact B", "Contact D" with no email, phone, or location. web_search returns 1000+ profiles with no way to identify the correct one.

2. **No last name**: "Davy", "Laurent", "Cedric" — single names that appear in thousands of LinkedIn profiles.

3. **Multiple conflicting profiles**: web_search returns several different people with the same name in different industries/locations, and none can be confidently linked to the contact record.

4. **Only a business/service as name**: Some "persons" are actually companies — see Non-Person Entries below.

## The Identity Resolution Ladder

Before declaring a contact unresolvable, attempt ALL of these in order:

1. `web_search("{name} {email_domain_or_location}")` — add any known email domain or location
2. `web_search("{name} LinkedIn {org}")` — if org is known
3. Direct LinkedIn URL check: `curl -s "https://r.jina.ai/linkedin.com/in/{attempted_username}"` — only works if you have a username to try
4. SearXNG supplementary: `curl -s "http://localhost:8888/search?q={name}+{location}&format=json&limit=5"`

If none produce a confident match (same person, same location/org), mark as unresolvable.

## Unresolvable ≠ Permanent

A contact that's unresolvable today may become resolvable later if:
- They get a Google Contacts sync that adds email/phone/location
- Their LinkedIn profile becomes more discoverable
- More context is added to their Weave record

Don't delete or flag permanently. Just skip this run and re-check next run.

## Non-Person Entries (Skip Permanently)

These are businesses/services that entered Google Contacts as people. Skip them every run:

- Doordash, Amazon.com, Resy, Visualping, Wealthfront, Harborworks Studio
- OpenTable, PayPal, Venmo, Google (as a person entry), DJI
- Any contact whose email is info@, support@, hello@, no-reply@

## Decision Flowchart

```
Contact with gap (missing occ or org)
├── Is it a business/service? → SKIP (permanent)
├── Name is common + no email/phone/location?
│   └── web_search with location/email domain
│       └── Single confident match? → ENRICH
│       └── Multiple matches, none confident? → SKIP (unresolvable)
├── web_search returns exact name + LinkedIn URL?
│   └── Extract from LinkedIn title/description
│       └── Occupation + org + location present? → ENRICH
│       └── Only partial data? → ENRICH with confidence 0.6-0.7
└── No LinkedIn found via web_search?
    └── SearXNG only
        └── Confident match? → ENRICH
        └── No confident match? → SKIP (unresolvable)
```

## Confidence Thresholds for Identity Resolution

| Evidence Level | Confidence | Action |
|---------------|------------|--------|
| LinkedIn profile URL matches name + location, title parse succeeds | 0.85-0.95 | Write |
| Linked title mentions name, single result, location plausible | 0.70-0.84 | Write |
| web_search shows one likely match but no direct proof | 0.60-0.69 | Write with low confidence |
| Multiple possible matches, none definitive | <0.60 | Skip |

## Log Format for Skipped Contacts

Log unresolvable skips to decisions.jsonl with:
```json
{
  "action": "skip_unresolvable",
  "contact_id": "...",
  "name": "...",
  "reason": "common_name_no_disambiguator | no_last_name | multiple_conflicting_profiles | non_person_entry",
  "attempted": ["web_search", "searxng"],
  "timestamp": "2026-06-26T09:00:00Z"
}
```