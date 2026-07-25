# Python Environment for Weave DB Scripts (June 2026)

## The Problem

The host has a split Python environment:

| Python | Path | Has `kuzu` | Has `real_ladybug` | Has `ladybug` | DB v41 support |
|---|---|---|---|---|---|
| venv 3.11 (default `python3`) | `/usr/local/lib/hermes-agent/venv/bin/python3` | ✅ | ✅ (v40 only) | ❌ | ❌ |
| system 3.13 (`/usr/bin/python3`) | `/usr/bin/python3` | ❌ | ✅ (broken circular import) | ✅ | ✅ |

The Weave database (`weave.lbug`) is storage version 41. Only the system `ladybug` package supports v41. The `real_ladybug` package (PyPI) only supports v40.

## The Fix (applied June 2026)

```bash
# Python 3.13: symlink real_ladybug -> ladybug
mv /usr/local/lib/python3.13/dist-packages/real_ladybug \
   /usr/local/lib/python3.13/dist-packages/real_ladybug.bak
ln -s /usr/local/lib/python3.13/dist-packages/ladybug \
      /usr/local/lib/python3.13/dist-packages/real_ladybug

# Python 3.11 venv: symlink real_ladybug -> ladybug (cross-version, works via __init__.py)
rm -rf /usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/real_ladybug
ln -s /usr/local/lib/python3.13/dist-packages/ladybug \
      /usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/real_ladybug
```

After this fix, both `import real_ladybug` and `import ladybug` work in both Python versions.

## Running Weave DB Scripts

**Always use `/usr/bin/python3` (system Python 3.13) for Weave DB scripts:**

```bash
/usr/bin/python3 -u <hermes-home>/skills/ocas-weave/scripts/google_sync.py
```

**Override `HOME` in cron contexts:**
```bash
HOME=/root /usr/bin/python3 -u /path/to/script.py
```

Cron sets `HOME=<hermes-home>/profiles/indigo/home`, which breaks `Path.home()` resolution.

## Verification

```bash
# Test real_ladybug import + DB access
/usr/bin/python3 -c "
import real_ladybug as lb
db = lb.Database('<hermes-home>/commons/db/ocas-weave/weave.lbug')
conn = lb.Connection(db)
r = conn.execute('MATCH (p:Person) RETURN count(p)')
print('OK:', r.get_all())
"
```

## If the Symlink Breaks

After pip upgrades or system updates, `real_ladybug` may get reinstalled over the symlink:

```bash
# Check if real_ladybug is a symlink
ls -la /usr/local/lib/python3.13/dist-packages/real_ladybug
# If it's a directory again (not a symlink), re-apply the fix above
```
