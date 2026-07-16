#!/usr/bin/env python3
"""
Weave SQLite backend — drop-in replacement for LadybugDB.

Usage:
    from weave_sqlite import WeaveDB
    db = WeaveDB()          # auto-inits schema, WAL mode
    db.upsert_person({...})
    rows = db.execute("SELECT * FROM persons WHERE ...", params)
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 weave_sqlite.py")
    sys.exit(0)



AGENT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = AGENT_ROOT / "commons" / "db" / "ocas-weave" / "weave.sqlite"

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_given TEXT, name_family TEXT,
    email TEXT, phone TEXT, location_city TEXT, location_country TEXT,
    occupation TEXT, org TEXT, google_resource_name TEXT, clay_id TEXT,
    source_type TEXT NOT NULL DEFAULT 'imported', source_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.8, event_time TEXT,
    record_time TEXT NOT NULL DEFAULT (datetime('now')),
    valid_from TEXT, valid_until TEXT
);
CREATE TABLE IF NOT EXISTS preferences (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, value TEXT NOT NULL,
    valence TEXT NOT NULL DEFAULT 'like', confidence REAL NOT NULL DEFAULT 0.8,
    source_type TEXT NOT NULL DEFAULT 'imported', source_ref TEXT NOT NULL DEFAULT '',
    record_time TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY, predicate TEXT NOT NULL, value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.8, source_type TEXT NOT NULL DEFAULT 'imported',
    source_ref TEXT NOT NULL DEFAULT '', record_time TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
    rel_type TEXT NOT NULL DEFAULT 'Knows', strength REAL, since TEXT, context TEXT,
    source_ref TEXT, confidence REAL,
    record_time TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES persons(id)
);
CREATE INDEX IF NOT EXISTS idx_persons_name ON persons(name);
CREATE INDEX IF NOT EXISTS idx_persons_email ON persons(email);
CREATE INDEX IF NOT EXISTS idx_persons_grn ON persons(google_resource_name);
CREATE INDEX IF NOT EXISTS idx_persons_clay ON persons(clay_id);
CREATE INDEX IF NOT EXISTS idx_persons_record_time ON persons(record_time);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(rel_type);
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate);
CREATE INDEX IF NOT EXISTS idx_preferences_category ON preferences(category);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class WeaveDB:
    def __init__(self, db_path: str | Path | None = None, read_only: bool = False):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.read_only = read_only
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_ready = False

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        if not self._schema_ready:
            conn.executescript(SCHEMA_SQL)
            self._schema_ready = True
        try:
            yield conn
            if not self.read_only:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, sql: str, params=()) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def execute_write(self, sql: str, params=()) -> int:
        with self._connect() as conn:
            return conn.execute(sql, params).lastrowid or 0

    def executemany(self, sql: str, params_list: list) -> None:
        with self._connect() as conn:
            conn.executemany(sql, params_list)

    # ── Person ──────────────────────────────────────────────────────────

    def upsert_person(self, person: dict) -> str:
        now = _now()
        pid = person.get("id") or _uuid()
        name = person.get("name", "")
        existing = self.execute("SELECT id FROM persons WHERE id = ? OR name = ?", (pid, name))
        if existing:
            eid = existing[0]["id"]
            sets, params = [], {"id": eid, "record_time": now}
            for src, col in {"name":"name","name_given":"name_given","name_family":"name_family",
                "email":"email","phone":"phone","location_city":"location_city",
                "location_country":"location_country","occupation":"occupation","org":"org",
                "google_resource_name":"google_resource_name","clay_id":"clay_id",
                "source_type":"source_type","source_ref":"source_ref","confidence":"confidence",
                "event_time":"event_time","valid_from":"valid_from","valid_until":"valid_until"}.items():
                v = person.get(src)
                if v is not None and v != "":
                    sets.append(f"{col} = COALESCE(NULLIF({col}, ''), ?)")
                    params.setdefault(src, v)
            if sets:
                self.execute_write(f"UPDATE persons SET {', '.join(sets)} WHERE id = :id", params)
            return eid
        else:
            fields = ["id","name","record_time"]
            vals = ["?","?","?"]
            params = [pid, name, now]
            for f in ["name_given","name_family","email","phone","location_city","location_country",
                      "occupation","org","google_resource_name","clay_id","event_time","valid_from","valid_until"]:
                if f in person and person[f] is not None:
                    fields.append(f); vals.append("?"); params.append(person[f])
            if "source_type" not in person:
                fields.append("source_type"); vals.append("?"); params.append("imported")
            if "source_ref" not in person:
                fields.append("source_ref"); vals.append("?"); params.append("")
            if "confidence" not in person:
                fields.append("confidence"); vals.append("?"); params.append(0.8)
            self.execute_write(f"INSERT INTO persons ({','.join(fields)}) VALUES ({','.join(vals)})", params)
            return pid

    def get_person(self, person_id: str) -> dict | None:
        rows = self.execute("SELECT * FROM persons WHERE id = ?", (person_id,))
        return rows[0] if rows else None

    def find_by_name(self, name_query: str, limit: int = 10) -> list[dict]:
        return self.execute("SELECT id, name, org, location_city FROM persons WHERE name LIKE ? ORDER BY name LIMIT ?", (f"%{name_query}%", limit))

    # ── Edges ───────────────────────────────────────────────────────────

    def upsert_edge(self, source_id: str, target_id: str, rel_type: str = "Knows", **props) -> str:
        now = _now()
        existing = self.execute("SELECT id FROM edges WHERE source_id = ? AND target_id = ? AND rel_type = ?", (source_id, target_id, rel_type))
        if existing:
            eid = existing[0]["id"]
            sets, params = ["record_time = ?"], [now]
            for k, v in props.items():
                if v is not None: sets.append(f"{k} = ?"); params.append(v)
            params.append(eid)
            self.execute_write(f"UPDATE edges SET {', '.join(sets)} WHERE id = ?", params)
            return eid
        else:
            eid = _uuid()
            fields = ["id","source_id","target_id","rel_type","record_time"]
            vals = ["?,?,?,?,?"]
            params = [eid, source_id, target_id, rel_type, now]
            for k, v in props.items():
                if v is not None: fields.append(k); vals.append("?"); params.append(v)
            self.execute_write(f"INSERT INTO edges ({','.join(fields)}) VALUES ({','.join(vals)})", params)
            return eid

    def get_relationships(self, person_id: str) -> list[dict]:
        return self.execute("SELECT e.*, p.name as target_name FROM edges e JOIN persons p ON p.id = e.target_id WHERE e.source_id = ? AND e.rel_type = 'Knows'", (person_id,))

    def get_facts(self, person_id: str) -> list[dict]:
        return self.execute("SELECT f.* FROM facts f JOIN edges e ON e.target_id = f.id WHERE e.source_id = ? AND e.rel_type = 'HasFact'", (person_id,))

    def get_facts_by_predicate(self, person_id: str, predicate: str) -> list[dict]:
        return self.execute("SELECT f.* FROM facts f JOIN edges e ON e.target_id = f.id WHERE e.source_id = ? AND e.rel_type = 'HasFact' AND f.predicate = ?", (person_id, predicate))

    def get_preferences(self, person_id: str) -> list[dict]:
        return self.execute("SELECT p.* FROM preferences p JOIN edges e ON e.target_id = p.id WHERE e.source_id = ? AND e.rel_type = 'HasPreference'", (person_id,))

    # ── Write helpers ────────────────────────────────────────────────────

    def create_fact(self, predicate: str, value: str, **props) -> str:
        fid, now = _uuid(), _now()
        self.execute_write("INSERT INTO facts (id, predicate, value, confidence, source_type, source_ref, record_time) VALUES (?,?,?,?,?,?,?)",
            (fid, predicate, value, props.get("confidence", 0.8), props.get("source_type", "imported"), props.get("source_ref", ""), now))
        return fid

    def link_fact(self, person_id: str, fact_id: str) -> None:
        self.execute_write("INSERT OR IGNORE INTO edges (id, source_id, target_id, rel_type, record_time) VALUES (?,?,?,?,?)",
            (_uuid(), person_id, fact_id, "HasFact", _now()))

    def create_preference(self, category: str, value: str, **props) -> str:
        pid, now = _uuid(), _now()
        self.execute_write("INSERT INTO preferences (id, category, value, valence, confidence, source_type, source_ref, record_time) VALUES (?,?,?,?,?,?,?,?)",
            (pid, category, value, props.get("valence", "like"), props.get("confidence", 0.8), props.get("source_type", "imported"), props.get("source_ref", ""), now))
        return pid

    # ── Graph queries ───────────────────────────────────────────────────

    def find_connection(self, from_id: str, to_id: str, max_depth: int = 4) -> list[dict]:
        return self.execute("""
            WITH RECURSIVE path(target_id, depth) AS (
                SELECT target_id, 1 FROM edges WHERE source_id = ? AND rel_type = 'Knows'
                UNION ALL
                SELECT e.target_id, p.depth + 1 FROM edges e
                JOIN path p ON p.target_id = e.source_id
                WHERE e.rel_type = 'Knows' AND p.depth < ?
                  AND e.target_id NOT IN (SELECT target_id FROM path)
            )
            SELECT DISTINCT n.name, MIN(p.depth) as distance
            FROM path p JOIN persons n ON n.id = p.target_id
            WHERE p.target_id = ? GROUP BY n.name
        """, (from_id, max_depth, to_id))

    def find_mutual_connections(self, a_id: str, b_id: str) -> list[dict]:
        return self.execute("""
            SELECT DISTINCT n.name, n.id, n.org
            FROM edges e1 JOIN edges e2 ON e2.source_id = e1.target_id
            JOIN persons n ON n.id = e1.target_id
            WHERE e1.source_id = ? AND e2.target_id = ? AND e1.rel_type = 'Knows' AND e2.rel_type = 'Knows' AND e1.target_id != ?
        """, (a_id, b_id, b_id))

    def find_by_city(self, city: str) -> list[dict]:
        return self.execute("SELECT name, org, occupation, id FROM persons WHERE location_city LIKE ? ORDER BY name", (f"%{city}%",))

    # ── Sync helpers ─────────────────────────────────────────────────────

    def get_modified_since(self, ts: str) -> list[dict]:
        return self.execute("SELECT * FROM persons WHERE record_time > ? ORDER BY record_time DESC", (ts,))

    def get_unsynced_google(self) -> list[dict]:
        return self.execute("SELECT id, name, email FROM persons WHERE google_resource_name IS NULL OR google_resource_name = ''")

    def get_unsynced_clay(self) -> list[dict]:
        return self.execute("SELECT id, name, email FROM persons WHERE clay_id IS NULL OR clay_id = ''")

    def get_persons_with_grn(self) -> list[dict]:
        return self.execute("SELECT * FROM persons WHERE google_resource_name IS NOT NULL AND google_resource_name != ''")

    def clear_google_resource_name(self, person_id: str) -> None:
        self.execute_write("UPDATE persons SET google_resource_name = NULL WHERE id = ?", (person_id,))

    def get_all_persons(self) -> list[dict]:
        return self.execute("SELECT * FROM persons ORDER BY name")

    # ── Bulk import ─────────────────────────────────────────────────────

    def bulk_import(self, table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        if table == "persons":
            now = _now()
            data = [(p.get("id") or _uuid(), p.get("name",""), p.get("name_given"), p.get("name_family"),
                p.get("email"), p.get("phone"), p.get("location_city"), p.get("location_country"),
                p.get("occupation"), p.get("org"), p.get("google_resource_name"), p.get("clay_id"),
                p.get("source_type","imported"), p.get("source_ref",""), p.get("confidence",0.8),
                p.get("event_time"), now, p.get("valid_from"), p.get("valid_until")) for p in rows]
            with self._connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO persons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", data)
        elif table == "edges":
            now = _now()
            data = [(e.get("id") or _uuid(), e["source_id"], e["target_id"], e.get("rel_type","Knows"),
                e.get("strength"), e.get("since"), e.get("context"), e.get("source_ref"),
                e.get("confidence"), e.get("record_time", now)) for e in rows]
            with self._connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?,?,?,?,?,?)", data)
        elif table == "preferences":
            now = _now()
            data = [(p.get("id") or _uuid(), p.get("category","other"), p.get("value",""),
                p.get("valence","like"), p.get("confidence",0.8), p.get("source_type","imported"),
                p.get("source_ref",""), p.get("record_time", now)) for p in rows]
            with self._connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO preferences VALUES (?,?,?,?,?,?,?,?)", data)
        elif table == "facts":
            now = _now()
            data = [(f.get("id") or _uuid(), f.get("predicate",""), f.get("value",""),
                f.get("confidence",0.8), f.get("source_type","imported"), f.get("source_ref",""),
                f.get("record_time", now)) for f in rows]
            with self._connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?)", data)
        return len(rows)
