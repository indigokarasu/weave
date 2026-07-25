# SQLite Migration Patterns

## Polymorphic Foreign Keys

**Problem**: SQLite `FOREIGN KEY` constraints require the target column to be a primary key in exactly one table. If your column is a polymorphic reference (can point to multiple tables), you cannot use FK constraints.

**Common case**: An `edges` table with `target_id` that can reference `persons(id)`, `facts(id)`, or `preferences(id)` depending on `rel_type`.

**Wrong**:
```sql
CREATE TABLE edges (
    ...
    target_id TEXT NOT NULL,
    FOREIGN KEY (target_id) REFERENCES persons(id)  -- breaks HasFact/HasPreference edges
);
```

**Right**:
```sql
CREATE TABLE edges (
    ...
    target_id TEXT NOT NULL
    -- No FK on target_id; enforce referential integrity in application code
);
```

## Migrating a Live Table Without FK

When you need to remove an FK constraint from a live SQLite table:

1. SQLite doesn't support `ALTER TABLE DROP CONSTRAINT`
2. You must recreate the table without the FK
3. Pattern (from `migrate_edges_fk.py`):

```python
conn.executescript("""
    BEGIN IMMEDIATE;
    CREATE TABLE edges_new (
        -- same columns, but without the bad FK
    );
    INSERT INTO edges_new SELECT * FROM edges;
    DROP TABLE edges;
    ALTER TABLE edges_new RENAME TO edges;
    -- recreate indexes
    CREATE INDEX idx_edges_source ON edges(source_id);
    CREATE INDEX idx_edges_target ON edges(target_id);
    CREATE INDEX idx_edges_type ON edges(rel_type);
    COMMIT;
""")
```

4. Always verify row counts before and after
5. Update `CREATE TABLE IF NOT EXISTS` in your schema code so new DBs get the correct schema

## Schema Code vs Live DB Divergence

`CREATE TABLE IF NOT EXISTS` only runs for new databases. If you fix a schema bug in code:
- Existing databases keep the old schema
- You need an explicit migration script
- Write the migration script BEFORE fixing the schema code
- Run the migration immediately after
- Then fix the schema code so new DBs are correct