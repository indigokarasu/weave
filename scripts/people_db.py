"""people_db — clean access layer for the Choice-2 People database.

Replaces the ladybug/Weave layer. INVARIANT: identity is an opaque random UUID4,
minted once, never derived. Email/phone/handles are mere identifiers (lookup keys),
never the id. This module is the ONLY sanctioned way to read/write people.

Schema (people.db):
  people(id PK uuid4, display_name, given_name, family_name, deceased, created_at, updated_at)
  identifiers(person_id, kind, value, is_primary)           # kind: email|phone|google|handle
  attributes(id, person_id, key, value, confidence, source_type, source_ref, updated_at)
  relationships(id, src_id, dst_id, type, context, confidence, source_ref, created_at)
  external_refs(person_id, system, external_id)             # continuity: weave/google ids
"""
import sqlite3, uuid, os
from datetime import datetime, timezone
import sys

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 people_db.py")
    sys.exit(0)


DEFAULT_DB = "<hermes-home>/profiles/<profile>/commons/db/people/people.db"

def _now(): return datetime.now(timezone.utc).isoformat()
def new_id(): return str(uuid.uuid4())   # opaque, attribute-free

class PeopleDB:
    def __init__(self, path=DEFAULT_DB, read_only=False):
        self.path = path; self.read_only = read_only
    def _c(self):
        c = sqlite3.connect(self.path, timeout=30); c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000"); return c

    # ---- lookup ----
    def get(self, pid):
        with self._c() as c:
            r = c.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone()
            return dict(r) if r else None
    def resolve(self, *, email=None, phone=None, external=None):
        """Resolve a person id by an identifier or external ref (email/phone/(system,id))."""
        with self._c() as c:
            if email:
                r = c.execute("SELECT person_id FROM identifiers WHERE kind='email' AND value=?", (email.lower().strip(),)).fetchone()
                if r: return r[0]
            if phone:
                d=''.join(ch for ch in phone if ch.isdigit())[-10:]
                r = c.execute("SELECT person_id FROM identifiers WHERE kind='phone' AND value LIKE ?", ('%'+d,)).fetchone()
                if r: return r[0]
            if external:
                sys_, xid = external
                r = c.execute("SELECT person_id FROM external_refs WHERE system=? AND external_id=?", (sys_, xid)).fetchone()
                if r: return r[0]
        return None
    def search(self, name, limit=10):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT id,display_name FROM people WHERE display_name LIKE ? LIMIT ?", ('%'+name+'%', limit))]
    def attributes(self, pid):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT key,value,confidence,source_type,source_ref FROM attributes WHERE person_id=?", (pid,))]
    def identifiers(self, pid):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT kind,value,is_primary FROM identifiers WHERE person_id=?", (pid,))]
    def relationships(self, pid):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT src_id,dst_id,type,context FROM relationships WHERE src_id=? OR dst_id=?", (pid,pid))]

    # ---- write ----
    def upsert_person(self, *, display_name, given_name=None, family_name=None, pid=None):
        """Create or fetch a person by name; returns opaque id. Never derives id from attributes."""
        with self._c() as c:
            if pid:
                r=c.execute("SELECT id FROM people WHERE id=?",(pid,)).fetchone()
                if r: return r[0]
            r=c.execute("SELECT id FROM people WHERE display_name=?",(display_name,)).fetchone()
            if r: return r[0]
            nid=pid or new_id(); now=_now()
            c.execute("INSERT INTO people (id,display_name,given_name,family_name,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                      (nid,display_name,given_name,family_name,now,now)); c.commit()
            return nid
    def add_identifier(self, pid, kind, value, is_primary=0):
        with self._c() as c:
            c.execute("INSERT OR IGNORE INTO identifiers (person_id,kind,value,is_primary) VALUES (?,?,?,?)",
                      (pid,kind,value.lower().strip() if kind=='email' else value,is_primary)); c.commit()
    def set_attribute(self, pid, key, value, *, confidence=0.9, source_type="people_db", source_ref=""):
        with self._c() as c:
            c.execute("INSERT INTO attributes (id,person_id,key,value,confidence,source_type,source_ref,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      (new_id(),pid,key,str(value),confidence,source_type,source_ref,_now()))
            if key=="deceased": c.execute("UPDATE people SET deceased=1 WHERE id=?",(pid,))
            c.commit()
    def add_relationship(self, src_id, dst_id, rel_type, *, context=None, confidence=0.9, source_ref="people_db"):
        with self._c() as c:
            ex=c.execute("SELECT id FROM relationships WHERE src_id=? AND dst_id=? AND type=?",(src_id,dst_id,rel_type)).fetchone()
            if ex: return ex[0]
            rid=new_id()
            c.execute("INSERT INTO relationships (id,src_id,dst_id,type,context,confidence,source_ref,created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (rid,src_id,dst_id,rel_type,context,confidence,source_ref,_now())); c.commit()
            return rid
    def set_external_ref(self, pid, system, external_id):
        with self._c() as c:
            c.execute("INSERT OR IGNORE INTO external_refs (person_id,system,external_id) VALUES (?,?,?)",(pid,system,external_id)); c.commit()

    # ---- the name<->id link Chronicle needs (opaque id + display name only) ----
    def name_id_links(self):
        with self._c() as c:
            return [(r[0], r[1]) for r in c.execute("SELECT id,display_name FROM people WHERE display_name IS NOT NULL")]

    # ---- consumer-facing query methods (replace raw persons/facts/edges SQL) ----
    def find_by_name(self, q, limit=10):
        with self._c() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, display_name AS name FROM people WHERE display_name LIKE ? ORDER BY display_name LIMIT ?", ('%'+q+'%', limit))]
    def find_by_attr(self, key, like, limit=50):
        with self._c() as c:
            return [dict(r) for r in c.execute(
                "SELECT DISTINCT p.id, p.display_name AS name FROM people p JOIN attributes a ON a.person_id=p.id "
                "WHERE a.key=? AND a.value LIKE ? LIMIT ?", (key, '%'+like+'%', limit))]
    def get_attr_values(self, pid, key):
        with self._c() as c:
            return [r[0] for r in c.execute("SELECT value FROM attributes WHERE person_id=? AND key=?", (pid, key))]
    def count_relationships(self, pid, rel_type=None):
        with self._c() as c:
            if rel_type:
                return c.execute("SELECT COUNT(*) FROM relationships WHERE (src_id=? OR dst_id=?) AND type=?", (pid,pid,rel_type)).fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM relationships WHERE src_id=? OR dst_id=?", (pid,pid)).fetchone()[0]
    def count_attributes(self, pid):
        with self._c() as c:
            return c.execute("SELECT COUNT(*) FROM attributes WHERE person_id=?", (pid,)).fetchone()[0]
    def delete_attributes(self, pid, key):
        with self._c() as c:
            c.execute("DELETE FROM attributes WHERE person_id=? AND key=?", (pid, key)); c.commit()
    def update_core(self, pid, **cols):
        """COALESCE-update core people columns (display_name/given/family/deceased) — fill-only."""
        allowed={"display_name","given_name","family_name","deceased"}
        sets=[f"{k}=COALESCE(NULLIF({k},''),?)" for k in cols if k in allowed]
        if not sets: return
        with self._c() as c:
            c.execute(f"UPDATE people SET {','.join(sets)}, updated_at=? WHERE id=?",
                      [cols[k] for k in cols if k in allowed]+[_now(),pid]); c.commit()
    def list_people(self, where_attr=None, limit=5000):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT id, display_name AS name FROM people LIMIT ?", (limit,))]
    def get_unsynced(self, system):
        """People with no external_ref for a given system (e.g. 'google') — sync candidates."""
        with self._c() as c:
            return [dict(r) for r in c.execute(
                "SELECT p.id, p.display_name AS name FROM people p "
                "WHERE NOT EXISTS (SELECT 1 FROM external_refs e WHERE e.person_id=p.id AND e.system=?)", (system,))]
    def get_external(self, pid, system):
        with self._c() as c:
            r=c.execute("SELECT external_id FROM external_refs WHERE person_id=? AND system=?", (pid, system)).fetchone()
            return r[0] if r else None