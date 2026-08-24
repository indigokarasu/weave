#!/usr/bin/env python3
"""Clean up the store and put data in the field that models it.

1. Pipeline metrics become single-valued. completeness_score, data_quality_score,
   enrichment_score, enrichability_score and enrichment_status are bookkeeping,
   recomputed on a schedule and appended as multi-valued facts, so people carry
   several contradictory scores at once and none is marked current. All five have
   live readers, so they are kept -- but only the newest of each, per person.

2. Birthdays and anniversaries move into the columns that exist for them.
   persons.birthday and persons.anniversary are empty on all 981 rows while 204
   birthday facts and 2 anniversary facts sit in the graph. The facts stay (they
   carry provenance); the columns are filled from them so the contact record is
   self-describing.

3. Placeholder and own-name values are cleared. 'Self-Employed' and 'Freelance'
   are not employers, 'Student' is not a job title, and an occupation that is
   only the contact's own name ('Renee Jean' -> occupation 'Jean') is a parsing
   artefact.

Dry-run by default; every removal is valid_until, never DELETE.
"""
import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")
METRICS = ("completeness_score", "data_quality_score", "enrichability_score",
           "enrichment_score", "enrichment_status", "enrichment")
JUNK = {"multiple companies", "self", "self-employed", "n/a", "na", "-", "unknown",
        "none", "linkedin employees", "linkedin member", "professional", "various",
        "freelance", "independent", "student", "retired", "company", "employees"}

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
con.execute("PRAGMA busy_timeout=60000")
now = datetime.now(timezone.utc).isoformat()
audit = {"run_at": now}

# ---- 1. metrics -> single-valued
ph = ",".join("?" * len(METRICS))
rows = con.execute(
    "SELECT f.id, f.predicate, f.value, f.record_time, e.source_id AS pid "
    "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "WHERE f.valid_until IS NULL AND f.predicate IN (%s) "
    "ORDER BY e.source_id, f.predicate, f.record_time DESC, f.id" % ph, METRICS).fetchall()
keep, stale = set(), []
for r in rows:
    k = (r["pid"], r["predicate"])
    if k in keep:
        stale.append(dict(r))
    else:
        keep.add(k)
print("  1. metric facts live: %d -> keeping newest per person: %d, retiring %d"
      % (len(rows), len(keep), len(stale)))
audit["stale_metrics"] = stale[:500]
audit["stale_metric_count"] = len(stale)

# ---- 2. birthdays / anniversaries into their columns
fills = []
for pred, col in (("birthday", "birthday"), ("anniversary", "anniversary")):
    for r in con.execute(
            "SELECT p.id, p.name, p.%s AS cur, f.value, MAX(f.record_time) rt "
            "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
            "JOIN persons p ON p.id=e.source_id "
            "WHERE f.predicate=? AND f.valid_until IS NULL "
            "GROUP BY p.id" % col, (pred,)):
        if not (r["cur"] or "").strip() and (r["value"] or "").strip():
            fills.append((r["id"], col, r["value"], r["name"]))
print("  2. %d contact record(s) will get their birthday/anniversary column filled"
      % len(fills))
for pid, col, v, nm in fills[:6]:
    print("       %-26s %s = %r" % (nm[:26], col, v))
audit["column_fills"] = [{"id": p, "column": c, "value": v, "name": n}
                         for p, c, v, n in fills]

# ---- 3. placeholder / own-name values
clears = []
for r in con.execute("SELECT id, name, org, occupation FROM persons"):
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", r["name"] or "")}
    for col in ("org", "occupation"):
        v = (r[col] or "").strip()
        if not v:
            continue
        toks = re.findall(r"[A-Za-z]{3,}", v)
        if v.lower() in JUNK:
            clears.append((r["id"], col, v, r["name"], "placeholder"))
        elif col == "occupation" and toks and own and all(t.lower() in own for t in toks):
            # Only occupation. An ORG matching the contact name is often correct:
            # company contacts are named after the company (AlphaSights, Ramp),
            # and people do work at eponymous firms.
            clears.append((r["id"], col, v, r["name"], "is the contact's own name"))
print("  3. %d placeholder/own-name value(s) to clear" % len(clears))
for _pid, col, v, nm, why in clears[:10]:
    print("       %-26s %-11s %-24r %s" % (nm[:26], col, v, why))
audit["cleared_values"] = [{"id": p, "column": c, "value": v, "name": n, "why": w}
                           for p, c, v, n, w in clears]

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

con.execute("BEGIN IMMEDIATE")
try:
    for r in stale:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    for pid, col, v, _n in fills:
        con.execute("UPDATE persons SET %s=? WHERE id=?" % col, (v, pid))
    for pid, col, _v, _n, _w in clears:
        con.execute("UPDATE persons SET %s='' WHERE id=?" % col, (pid,))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "rationalize-%s.json" % now[:19].replace(":", ""))
audit["revert_sql"] = ("UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                       % ",".join("'%s'" % r["id"] for r in stale[:500]))
json.dump(audit, open(p, "w"), indent=1, default=str)
print("  applied; audit %s" % p)

live = con.execute("SELECT COUNT(*) FROM facts WHERE valid_until IS NULL").fetchone()[0]
met = con.execute("SELECT COUNT(*) FROM facts WHERE valid_until IS NULL "
                  "AND predicate IN (%s)" % ph, METRICS).fetchone()[0]
dup = con.execute(
    "SELECT COUNT(*) FROM (SELECT e.source_id, f.predicate, COUNT(*) n FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "WHERE f.valid_until IS NULL AND f.predicate IN (%s) GROUP BY 1,2 HAVING n>1)" % ph,
    METRICS).fetchone()[0]
print("VERIFY live facts            : %d" % live)
print("VERIFY metric facts          : %d (%.1f%% of live)" % (met, 100.0 * met / live))
print("VERIFY duplicate metric pairs: %d (want 0)" % dup)
print("VERIFY birthday columns set  : %d" % con.execute(
    "SELECT COUNT(*) FROM persons WHERE birthday IS NOT NULL AND birthday != ''"
).fetchone()[0])
