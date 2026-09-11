# Init Pattern

WeaveDB auto-initializes on first command via `WeaveDB.__init__()`.

## How It Works

Every command that opens the database runs `_ensure_init()` first. This handles:
1. Creating the SQLite database file at the canonical path
2. Setting WAL mode (`PRAGMA journal_mode=WAL`)
3. Running `CREATE TABLE IF NOT EXISTS` for all schema tables
4. Creating the config file if missing

No manual init command is needed — the first `WeaveDB()` call triggers everything.

## Canonical DB Path

The DB lives at:
```
{agent_root}/commons/db/ocas-weave/weave.sqlite
```

Where `{agent_root}` resolves to `<hermes-home>/profiles/indigo` in the default profile.

## Verification

After init, verify the DB is healthy:
```python
from weave_sqlite import WeaveDB
weave = WeaveDB()
count = weave.execute("SELECT COUNT(*) as cnt FROM persons")[0]["cnt"]
print(f"DB initialized with {count} persons")
```

If the DB file exists but has 0 tables, something went wrong during init — check WAL files (`weave.sqlite-wal`, `weave.sqlite-shm`) for corruption.
