# Weave — Recovery Behavior

This skill implements the recovery contract from `spec-ocas-recovery.md`.

- **Evidence**: Every sync/write run writes an evidence record to `{agent_root}/commons/data/ocas-weave/evidence.jsonl`, including no-op runs. `not_activity_reason` is mandatory for no-op runs.
- **Gap detection**: On every wake, checks the evidence log. If gap exceeds cadence (24h for sync-google, 1h for enrichability-recalc), logs `gap_detected`.
- **Degraded mode**: When dependencies are unavailable, logs `degraded: <dependency>` and queues changes. Existing data remains queryable.
- **Log compaction**: Evidence/decision logs older than 30 days (no-op) or 90 days (error/gap) compacted. Sync checkpoints never auto-deleted. Last 7 days retained.