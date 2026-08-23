#!/usr/bin/env python3
"""The 155 relation edges whose target person no longer exists.

Unlike the dead HasFact edges (a live leak, now fixed), these are historical: a
Knows/SUPPRESSED edge to a person row that was removed at some point. An edge to
nobody cannot be traversed and inflates every relation count, so it is dead
weight -- but confirm what they are before removing them.
"""
import argparse
import collections
import json
import os
import sqlite3
from datetime import datetime

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT e.*, (SELECT name FROM persons WHERE id=e.source_id) AS src_name "
    "FROM edges e WHERE e.rel_type != 'HasFact' "
    "AND e.target_id NOT IN (SELECT id FROM persons)").fetchall()
print("  dead relation edges: %d" % len(rows))
print("  by rel_type: %s"
      % dict(collections.Counter(r["rel_type"] for r in rows)))
print("  do any point at a FACT instead of a person? %d"
      % con.execute("SELECT COUNT(*) FROM edges WHERE rel_type != 'HasFact' "
                    "AND target_id IN (SELECT id FROM facts)").fetchone()[0])
print("  distinct missing targets: %d"
      % len({r["target_id"] for r in rows}))
print("  sample:")
for r in rows[:8]:
    print("     %-12s %-24s -> %s (missing)"
          % (r["rel_type"], str(r["src_name"])[:24], r["target_id"][:8]))
print("  edges by day written:")
for k, v in sorted(collections.Counter(
        (r["record_time"] or "?")[:10] for r in rows).items())[:8]:
    print("     %-12s %d" % (k, v))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

ts = datetime.now().strftime("%Y%m%dT%H%M%S")
os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "dead-relation-edges-%s.json" % ts)
json.dump({"run_at": ts, "count": len(rows), "edges": [dict(r) for r in rows]},
          open(p, "w"), indent=1, default=str)
con.execute("BEGIN IMMEDIATE")
try:
    con.execute("DELETE FROM edges WHERE rel_type != 'HasFact' "
                "AND target_id NOT IN (SELECT id FROM persons)")
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  removed; audit %s" % p)
print("VERIFY dead relation edges: %d (want 0)" % con.execute(
    "SELECT COUNT(*) FROM edges WHERE rel_type != 'HasFact' "
    "AND target_id NOT IN (SELECT id FROM persons)").fetchone()[0])
print("VERIFY family edges intact: %s" % dict(
    (r["rel_type"], r["n"]) for r in con.execute(
        "SELECT rel_type, COUNT(*) n FROM edges WHERE rel_type IN "
        "('SpouseOf','ParentOf','SiblingOf','CousinOf') GROUP BY rel_type")))
