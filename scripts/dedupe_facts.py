#!/usr/bin/env python3
"""Collapse exact duplicate facts: same person, same predicate, same value.

These came from check-then-write without a lock (the pattern contact_urls.py now
guards with BEGIN IMMEDIATE). A duplicate is not just clutter -- the enrichment
pipeline counts corroborating facts, so the same claim stored twice reads as two
independent sources.

Keeps the OLDEST row of each group (the original assertion, with its original
source_type and record_time) and retires the rest with valid_until. Nothing is
deleted; the audit file carries the revert.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
con.execute("PRAGMA busy_timeout=60000")

rows = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, f.record_time, "
    "e.source_id AS pid, p.name "
    "FROM facts f JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
    "JOIN persons p ON p.id = e.source_id "
    "WHERE f.valid_until IS NULL "
    "ORDER BY f.record_time, f.id").fetchall()

groups = {}
for r in rows:
    groups.setdefault((r["pid"], r["predicate"], r["value"]), []).append(r)
dupes = {k: v for k, v in groups.items() if len(v) > 1}
retire = [r for v in dupes.values() for r in v[1:]]

print("  live facts                 : %d" % len(rows))
print("  duplicate groups           : %d" % len(dupes))
print("  rows to retire             : %d" % len(retire))
import collections
print("  by predicate               : %s"
      % dict(collections.Counter(r["predicate"] for r in retire).most_common(10)))
for k, v in list(dupes.items())[:6]:
    print("     %-20s %-16s %-30s x%d"
          % (v[0]["name"][:20], k[1], str(k[2])[:30], len(v)))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r in retire:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

os.makedirs(AUDIT_DIR, exist_ok=True)
path = os.path.join(AUDIT_DIR, "duplicate-facts-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "retired": [dict(r) for r in retire],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r in retire)},
          open(path, "w"), indent=1)
print("applied; audit at %s" % path)
left = con.execute(
    "SELECT COUNT(*) FROM (SELECT e.source_id, f.predicate, f.value, COUNT(*) n "
    "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "WHERE f.valid_until IS NULL GROUP BY 1,2,3 HAVING n>1)").fetchone()[0]
print("VERIFY duplicate groups remaining: %d (want 0)" % left)
