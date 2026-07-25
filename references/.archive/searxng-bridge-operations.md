# SearXNG & Bridge Operations

## SearXNG Health Check

```bash
curl -s --max-time 10 "http://localhost:8888/search?q=test&format=json&limit=1" | python3 -c "
import sys,json
d = json.load(sys.stdin)
results = len(d.get('results',[]))
unresponsive = len(d.get('unresponsive_engines',[]))
print(f'Results: {results}, Unresponsive engines: {unresponsive}')
for eng, reason in d.get('unresponsive_engines',[]):
    print(f'  {eng}: {reason}')
"
```

If `unresponsive_engines > 3` (more than ~50% of typical 7-engine setup):

```bash
docker restart searxng
sleep 15
# Re-check health
```

## LadybugDB Bridge Lock Management

The bridge (`ladybug-bridge-weave.service`) holds a `READ_WRITE` lock on `weave.lbug`.
All other processes (google_sync.py, enrichment scripts, direct Python) are blocked.

**Before any DB write operation:**
```bash
systemctl stop ladybug-bridge-weave.service
# If stop hangs (common):
systemctl kill --signal=SIGKILL ladybug-bridge-weave.service
# Verify:
systemctl is-active ladybug-bridge-weave.service  # should print "failed"
```

**After all writes complete:**
```bash
systemctl start ladybug-bridge-weave.service
systemctl is-active ladybug-bridge-weave.service  # should print "active"
```

**Never restart the bridge mid-pipeline.** Stop once at the start, restart once at the end.

## Python Script Output Buffering

Background Python scripts may show zero output in `process(action='poll')` due to Python's output buffering.

**Fix:** Use `python3 -u` (unbuffered) or redirect to file:
```bash
python3 -u /path/to/script.py > /tmp/output.log 2>&1
```

Then read output via `read_file(path='/tmp/output.log')`.

## Script Reference

| Script | Purpose | Commands |
|--------|---------|----------|
| `scripts/overnight_enrichment.py` | Full 3-phase enrichment pipeline | `python3 -u scripts/overnight_enrichment.py` |
| `scripts/enrichment_control.py` | SearXNG service control | `start`, `stop`, `status` |
| `scripts/google_sync.py` | Bidirectional Google Contacts sync | `python3 scripts/google_sync.py` |
| `scripts/recalculate_enrichment.py` | Recalculate enrichability scores | `python3 scripts/recalculate_enrichability.py` |

**Note:** There is no `enrichment_data.py`. The pipeline instructions that reference it are stale. Use the scripts listed above.