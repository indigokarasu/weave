# contact_snapshots.py — Known Bugs & Fixes

## Datetime Timezone Bug (June 2026)

**Symptom**: Snapshot creation fails with:
```
WARNING - snapshot failed: can't compare offset-naive and offset-aware datetimes
```

**Root cause**: `contact_snapshots.py` mixes naive and aware datetime objects when comparing timestamps.

**Impact**: No snapshot is created. Outbound sync proceeds without rollback safety net.

**Fix**: In `contact_snapshots.py`, ensure all datetime comparisons use timezone-aware objects:
```python
from datetime import datetime, timezone
now = datetime.now(timezone.utc)  # Always use aware
```

**Workaround**: If snapshot fails but outbound also fails (e.g., scope error), no data loss. If snapshot fails and outbound SUCCEEDS, there's no rollback — verify outbound results carefully.

## Verification After Snapshot

Always check the snapshot file has content:
```bash
wc -l ~/.hermes/commons/db/ocas-weave/snapshots/<snapshot_file>
```
0 lines = snapshot didn't run. Non-zero = snapshot captured.