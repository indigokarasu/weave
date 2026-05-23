# Weave Scripts

- `weave_health_check.py` — enrichment pipeline health check (runs every 5 min via cron)
- `google_sync.py` — bidirectional Google Contacts sync
- `weave_full_sync.py` — full contact sync
- `overnight_enrichment.py` — overnight contact enrichment pipeline
- `contact_snapshots.py` — Google Contacts snapshot for safe writeback
- `enrichment_control.py` — enrichment pipeline control
- `quick_enrich.py` — quick contact enrichment
- `db_cleanup_contacts.py` — contact database cleanup
- `db_cleanup_photos.py` — photo cleanup
- `db_fix_url_labels.py` — URL label fixer
- `update.sh` — self-update from GitHub source

The health check script is also symlinked from `~/.hermes/scripts/weave_health_check.py` for cron compatibility.
