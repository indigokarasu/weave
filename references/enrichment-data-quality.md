# Enrichment Data Quality Patterns

## Garbage Data Categories (identified 2026-06-18)

### Org Values — Reject These Patterns

| Pattern | Example | Why It's Wrong |
|---------|---------|----------------|
| Known city names | "Chicago", "Los Angeles", "Boston" | City used as org |
| Single generic words | "Professional", "Employees", "Newsletter", "Joy" | Not a company |
| Sentence fragments | "Leading Organizations Across the World of Open" | Verbose fragment |
| Lowercase single words | "per", "new", "updates" | Not a proper noun |
| Person's own name | "Sharon" for contact "Sharon McQueen" | Name echo |
| Email-like values | "leaflet@1.9.4" | Junk data |

### Occupation Values — Reject These Patterns

| Pattern | Example | Why It's Wrong |
|---------|---------|----------------|
| Not a job title | "RGB, a Director" | Garbage extraction |
| Sentence fragments | "As the Editorial Director" | Starts with verb |
| Single non-title words | "Experienced", "Early" | Not a role |
| Location names | "Phoenix, AZ" | Location as occupation |

### Email Values — Reject These Patterns

| Pattern | Example | Why It's Wrong |
|---------|---------|----------------|
| Doesn't belong to person | contact@example.com for Jenny Shears | Clearly not hers |
| Junk domains | "leaflet@1.9.4" | Bot-generated |

## Validation Rules Applied (weave_enrich.py validate_field)

### Org validation
- Reject known city names (STATIC_CITIES list)
- Reject generic non-company words: professional, accidents, newsletter, employees, per, joy
- Reject sentence fragments containing: was, were, been, being, have, has, had
- Require at least one uppercase letter (proper noun check)
- Reject person's own name as org value
- Length must be 2-80 chars

### Occupation validation
- Must contain a word from TITLE_INDICATORS list, OR all words > 2 chars must be title-case
- Must not start with: is, are, was, were, the, a, an
- Length between 3-60 chars

### Deduplication
After every enrichment run, check for duplicates:
```sql
SELECT source_id, predicate, value, COUNT(*) as cnt
FROM facts f JOIN edges e ON f.id = e.target_id
WHERE e.rel_type = 'HasFact' AND f.source_type = 'web_enrichment'
GROUP BY source_id, predicate, value
HAVING COUNT(*) > 1
```

## SearXNG Reliability
- SearXNG can return connection reset errors under rapid sequential load
- Retry pattern: 3 attempts with 2s/4s/8s exponential backoff
- If >50% engines unresponsive, restart SearXNG (systemctl or docker)
