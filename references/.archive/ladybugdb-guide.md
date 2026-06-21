# LadybugDB Usage Guide

## Query Result Handling

When querying Weave via LadybugDB (`real_ladybug`), the return format depends on the Cypher clause:

- **`RETURN p`** (whole node): Each row is a **dict** with all properties plus `_ID` and `_LABEL` keys. Use `row['name']` or `row.get('name')`.
- **`RETURN p.id, p.name, ...`** (column selectors): Each row is a **list** (not a dict). Map column names to indices via `r.get_column_names()`: `cols = r.get_column_names(); row[cols.index('p.name')]`.
- **`r.get_all()`** returns a Python list of rows.
- **`r.rows_as_dict()`** returns a *QueryResult* object, NOT a Python dict.
- **`r.get_column_names()`** works for all queries and returns column name strings.

Key mistake: Using `row['name']` on a list row from column selectors raises `TypeError`. Always match access pattern to return format.

## Iteration Pitfalls (discovered Apr 2026)

- **`r.get_all()` fails on corrupt rows**: If any row has corrupted/invalid UTF-8, `get_all()` raises `UnicodeDecodeError` and returns NOTHING. Use row-by-row iteration with error handling:
  ```python
  rows = []
  while True:
      try:
          row = r.get_next()
          rows.append(row)
      except StopIteration:
          break
      except Exception as e:
          if "No more tuples" in str(e):
              break
          if "utf-8" in str(e):
              continue  # Skip corrupt row
          raise
  ```
- **End-of-results exception**: `r.get_next()` raises `Runtime exception: No more tuples in QueryResult` — NOT `StopIteration`. Check `"No more tuples" in str(e)`.
- **Import pattern**: `from real_ladybug import Database, Connection` (top-level). No `lb` submodule. `READ_ONLY`/`READ_WRITE` constants are NOT exported — use `Database(path, read_only=True)`. Connection: `Connection(db)` then `conn.execute(cypher, params)`.
  ```python
  from real_ladybug import Database, Connection
  db = Database("/path/to/weave.lbug", read_only=True)
  conn = Connection(db)
  r = conn.execute("MATCH (p:Person) RETURN p.id, p.name LIMIT 5")
  ```
- **No `randomUUID()` in Cypher**: Generate UUIDs in Python with `uuid.uuid4()` and pass as parameters. Never generate IDs in Cypher expressions.
