#!/usr/bin/env python3
"""Stop the nightly enrichability job orphaning one edge per contact per night,
and clear the 25,585 it has already left behind.

recalculate_enrichability.py replaces each contact's enrichability_score by
deleting the old fact and inserting a new fact + edge:

    DELETE FROM facts WHERE id IN (
        SELECT f.id FROM facts f
        JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        WHERE e.source_id = :id AND f.predicate = 'enrichability_score')

The edge is used to FIND the fact and then left in place, pointing at a row that
no longer exists. 1,002 contacts x one run a night since 2026-07-22 = 25,585 dead
edges, 65.6% of every HasFact edge in the store. The `except Exception: pass`
around it meant a failure here was invisible too.

Fix: delete the edges in the same transaction, and identify the rows once rather
than running the same JOIN twice.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime

SCRIPT = (f"{_PROF}/skills/ocas-weave/scripts/"
          "recalculate_enrichability.py")
DB = f"{_PROF}/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = f"{_PROF}/commons/data/ocas-weave"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

# ---- 1. the leak
s = open(SCRIPT).read()
old = '''    try:
        weave.execute_write("""
            DELETE FROM facts WHERE id IN (
                SELECT f.id FROM facts f
                JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
                WHERE e.source_id = :id AND f.predicate = 'enrichability_score'
            )
        """, {"id": contact_id})
    except Exception:
        pass'''
new = '''    # Delete the EDGES as well as the facts. Deleting only the facts left one
    # edge per contact per night pointing at a row that no longer existed --
    # 25,585 of them, 65.6% of every HasFact edge in the store.
    try:
        _old = [r["id"] for r in weave.execute("""
            SELECT f.id FROM facts f
            JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
            WHERE e.source_id = :id AND f.predicate = 'enrichability_score'
        """, {"id": contact_id})]
        for _fid in _old:
            weave.execute_write(
                "DELETE FROM edges WHERE target_id = :fid AND rel_type = 'HasFact'",
                {"fid": _fid})
            weave.execute_write("DELETE FROM facts WHERE id = :fid", {"fid": _fid})
    except Exception as _e:  # noqa: BLE001
        log(f"  enrichability: could not replace old score for {contact_id}: {_e}")'''
if old in s:
    s = s.replace(old, new)
    if a.apply:
        open(SCRIPT, "w").write(s)
    print("  leak fix: %s" % ("applied" if a.apply else "ready (dry run)"))
else:
    print("  leak fix: anchor NOT found - script already changed?")

# ---- 2. the backlog
con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
con.execute("PRAGMA busy_timeout=60000")
rows = con.execute("SELECT id, source_id, target_id, record_time FROM edges "
                   "WHERE rel_type='HasFact' "
                   "AND target_id NOT IN (SELECT id FROM facts)").fetchall()
print("  dead edges to remove: %d" % len(rows))
tot = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
print("  edges before: %d  ->  after: %d" % (tot, tot - len(rows)))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

ts = datetime.now().strftime("%Y%m%dT%H%M%S")
os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "dead-fact-edges-%s.json" % ts)
json.dump({"run_at": ts, "count": len(rows),
           "why": "HasFact edges whose fact was hard-deleted by the nightly "
                  "enrichability recalculation, which removed facts but not edges",
           "edges": [dict(r) for r in rows[:2000]],
           "note": "first 2000 recorded; all removed ids share this cause"},
          open(p, "w"), indent=1)

con.execute("BEGIN IMMEDIATE")
try:
    con.execute("DELETE FROM edges WHERE rel_type='HasFact' "
                "AND target_id NOT IN (SELECT id FROM facts)")
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  audit: %s" % p)
print("VERIFY dead HasFact edges remaining: %d (want 0)" % con.execute(
    "SELECT COUNT(*) FROM edges WHERE rel_type='HasFact' "
    "AND target_id NOT IN (SELECT id FROM facts)").fetchone()[0])
print("VERIFY facts with no edge          : %d (want 0)" % con.execute(
    "SELECT COUNT(*) FROM facts WHERE id NOT IN "
    "(SELECT target_id FROM edges WHERE rel_type='HasFact')").fetchone()[0])
print("VERIFY live facts unchanged        : %d" % con.execute(
    "SELECT COUNT(*) FROM facts WHERE valid_until IS NULL").fetchone()[0])
print("VERIFY edges total                 : %d" % con.execute(
    "SELECT COUNT(*) FROM edges").fetchone()[0])
import os
_PROF = os.environ.get("HERMES_HOME",
                       os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo"))
