# Default Configuration

Default `config.json` written by `_ensure_config()` on first use:

```json
{
  "skill_id": "ocas-weave",
  "skill_version": "2.3.0",
  "config_version": "1",
  "created_at": "",
  "updated_at": "",
  "writeback": {
    "google_contacts": false,
    "clay": false
  },
  "last_sync": {
    "google_contacts": null,
    "clay": null
  },
  "retention": {
    "days": 0
  }
}
```

## Writeback flags

Outbound sync to Google Contacts or Clay requires the corresponding `writeback` flag set `true` **AND** explicit per-sync user approval. Neither alone is sufficient.

## Retention

`retention.days: 0` means infinite retention. Set to a positive integer to enable automatic pruning of stale records.

## Config location

`{agent_root}/commons/db/ocas-weave/config.json`

Health checks should verify this file exists. If missing, run `weave.init` to trigger auto-creation via `_ensure_init()`.
