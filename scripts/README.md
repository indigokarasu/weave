# Weave Scripts

Use this map before selecting or modifying a script.

| File | When to read |
|------|-------------|
| `weave_sqlite.py` | Before any database operation — import `WeaveDB` from here |
| `google_api.py` | Before any Google API call — shared token refresh and request helpers |
| `weave_enrich.py` | Before enrichment work — shared extraction, search, validation, and identity checks |
| `discovery_probe.py` | Before an enrichment run — probe SearXNG/DDG availability and decide proceed/fallback/defer |
| `google_sync.py` | Before Google Contacts sync — run with `--push-person <weave_person_id>` for targeted outbound only |
| `overnight_enrichment.py` | Before batch enrichment — run `--help` for usage; reads contacts with gaps and writes via WeaveDB |
| `recalculate_enrichability.py` | Before recalculating enrichability scores — run `--help` for usage |
| `enrichment_control.py` | Before controlling enrichment — run `--help` for usage |
| `weave_full_sync.py` | Before a full sync — run `--help` for usage |
| `audit_*.py` | When auditing data quality — run `--help` for usage |
| `clean_*.py` | When cleaning a specific data class — run `--help` for usage |
| `fix_*.py` | When applying a specific repair — run `--help` for usage |
| `merge_*.py` | When merging duplicate records — run `--help` for usage |
| `sweep_*.py` | When applying a scoped data sweep — run `--help` for usage |
| `test_*.py` | Before claiming script correctness — run with pytest or `--help` where supported |

## CLI vs Library Modules

- Files with `if __name__ == "__main__"` are executable CLI entry points. Run them with `--help` for flags and examples.
- Files without `__main__` are library modules imported by CLI entry points. Do not run them directly.
- Shared modules must not inspect or mutate `sys.argv` at import time; `--help` belongs in the executable entry point.
- Destructive operations require an explicit `--apply` or equivalent flag; default to dry-run/preview behavior.
