# Google Sync — Environment Setup

## Required Environment Variables

`google_sync.py` requires these env vars to run correctly:

| Variable | Value | Why |
|----------|-------|-----|
| `LBUG_C_API_LIB_PATH` | `/tmp/liblbug.so` | LadybugDB C API shared library (not shipped in wheel) |
| `AGENT_ROOT` | `<hermes-home>/profiles/indigo` | Profile path where Weave DB lives |
| `HOME` | `/root` | Prevents `Path.home()` breakage in cron |

## Full Command

```bash
LBUG_C_API_LIB_PATH=/tmp/liblbug.so \
  AGENT_ROOT=<hermes-home>/profiles/indigo \
  HOME=/root \
  python3 -u \
  <hermes-home>/profiles/indigo/skills/ocas-weave/scripts/google_sync.py
```

## Installing liblbug.so

The `real_ladybug` Python wheel does NOT ship a working `liblbug.so`. Download from GitHub:

```bash
curl -sL -o /tmp/liblbug-linux-x86_64.tar.gz \
  "https://github.com/LadybugDB/ladybug/releases/download/v0.17.1/liblbug-linux-x86_64.tar.gz"
tar xzf /tmp/liblbug-linux-x86_64.tar.gz -C /tmp
# Produces: /tmp/liblbug.so  (symlink) -> /tmp/liblbug.so.0.17.1
```

Verify it works:
```bash
LBUG_C_API_LIB_PATH=/tmp/liblbug.so python3 -c "
import ladybug as lb
db = lb.Database('/tmp/_test.lbug')
print('OK')
db.close()
"
```

## Cron Job

The `weave:sync-google` cron job should use the full command above. If the cron job
runs the script without these env vars, it will fail with:
- `RuntimeError: Could not find lbug C API shared library` (missing `LBUG_C_API_LIB_PATH`)
- `ModuleNotFoundError: No module named 'ladybug'` (if using system Python)
- DB path resolution wrong (missing `AGENT_ROOT`)
