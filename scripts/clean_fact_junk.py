#!/usr/bin/env python3
"""The junk rules were only ever applied to the contact columns, not the graph.

Earlier passes cleaned persons.org and persons.occupation -- the two fields the
address book shows -- and left the facts table untouched. So the same scrape
debris is still sitting in the graph: Mads Paulin has an occupation fact reading
'Pierre Hathout President', which is a different person's name and title.

That matters beyond tidiness. Enrichment reads existing facts to decide what a
contact already has, the corroboration gate reads them to decide what to trust,
and a job change promotes a fact into the visible column. Junk left in the graph
gets a second chance to reach the record every time one of those runs.

This applies the identical classifiers to org and occupation FACTS. Nothing is
deleted: a rejected fact is stamped valid_until, which is how this database
retires a statement without losing that it was once made.
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
from job_junk_v3 import classify as classify_job   # noqa: E402
from org_junk import classify_org                  # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row

# every contact name, so "this is somebody else's name" can be tested
names = [str(r["name"]) for r in con.execute(
    "SELECT name FROM persons WHERE name IS NOT NULL AND name <> ''")]

rows = [dict(r) for r in con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, e.source_id pid, "
    "p.name pname, p.name_family pfam, p.org porg, p.occupation pocc "
    "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id "
    "WHERE f.valid_until IS NULL AND f.predicate IN ('org','occupation')")]

bad = []
for r in rows:
    v = str(r["value"] or "").strip()
    if not v:
        continue
    why = None
    if r["predicate"] == "occupation":
        why = classify_job(v, r["pname"] or "", "occupation", names)
    elif (r["pfam"] or "").strip():
        # person contacts only; a company's org is legitimately its own name
        why = classify_org(v, r["pname"] or "", set())
    if why:
        bad.append((r, why))

print("  org/occupation facts examined : %d" % len(rows))
print("  facts the junk rules reject   : %d" % len(bad))
print("\n  by reason:")
for why, n in Counter(w for _r, w in bad).most_common():
    print("     %-52s %d" % (why, n))
print("\n  examples:")
for r, why in bad[:22]:
    live = " <- ALSO ON THE RECORD" if (
        str(r["value"]).strip() == str(r["pocc" if r["predicate"] == "occupation"
                                         else "porg"] or "").strip()) else ""
    print("     %-20s %-11s %-40r %s%s"
          % (str(r["pname"])[:20], r["predicate"], str(r["value"])[:40], why[:34], live))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r, _why in bad:
        con.execute("UPDATE facts SET valid_until = ? WHERE id = ?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("\n  retired %d junk facts" % len(bad))

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "fact-junk-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "retired": [
    {"fact_id": r["id"], "name": r["pname"], "predicate": r["predicate"],
     "value": r["value"], "why": w} for r, w in bad]},
    open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
