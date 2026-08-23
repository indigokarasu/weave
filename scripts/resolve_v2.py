#!/usr/bin/env python3
"""Give each contact one current value per single-valued field -- but only where
the choice is defensible.

My first attempt ranked by record_time and would have kept org 'Internet' over
'Wikipedia / Wikimedia Foundation' for Jimmy Wales. Record time is when a SCRAPE
ran, not when the fact became true, so recency of the row says nothing about
recency of the world.

Resolve only on evidence:
  1. precision   one value contains the others ('Sonoma' -> 'Sonoma, CA')
  2. source      one value comes from a more trustworthy source than the others;
                 what the owner curated beats what a crawler inferred

Anything else -- two values of equal standing, neither containing the other -- is
a judgement about the person, not about the data, so it is REPORTED and left
alone. Superseding it would just be guessing with a timestamp.
"""
import argparse
import collections
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
SINGLE = ("location_city", "location_country", "org", "occupation", "birthday",
          "pronouns")
TRUNCATED = {"view, ca": "Mountain View, CA"}

# higher wins. What the owner recorded outranks what a crawler guessed.
TRUST = {
    "user": 100, "user-stated": 100, "imported": 95, "google_contacts": 90,
    "email_analysis": 80, "linkedin_profile": 70,
    "scout_osint": 50, "scout_research": 50, "scout_osint_expanded": 45,
    "research": 40, "web_enrichment": 30, "search": 30, "inferred": 25,
    "scout_low_confidence": 10, "test": 0, "system": 0,
}

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
now = datetime.now(timezone.utc).isoformat()
rows = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, f.record_time, "
    "e.source_id pid, p.name FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id WHERE f.valid_until IS NULL "
    "AND f.predicate IN (%s) ORDER BY f.record_time" % ",".join("?" * len(SINGLE)),
    SINGLE).fetchall()


def norm(v):
    return re.sub(r"[^a-z0-9]", "", (v or "").lower())


def trust(r):
    return TRUST.get((r["source_type"] or "").strip(), 20)


grp = collections.defaultdict(list)
for r in rows:
    grp[(r["pid"], r["predicate"])].append(r)

resolved, ambiguous, fixes = [], [], []
for (pid, pred), rs in grp.items():
    uniq = {}
    for r in rs:
        uniq.setdefault(norm(r["value"]), []).append(r)
    if len(uniq) < 2:
        continue
    reps = [v[0] for v in uniq.values()]

    winner = reason = None
    for cand in reps:
        others = [o for o in reps if o is not cand]
        if others and all(norm(o["value"]) in norm(cand["value"]) for o in others):
            winner, reason = cand, "precision"
            break
    if winner is None:
        top = max(trust(r) for r in reps)
        best = [r for r in reps if trust(r) == top]
        if len(best) == 1 and top > min(trust(r) for r in reps):
            winner, reason = best[0], "source (%s)" % best[0]["source_type"]
    if winner is None:
        ambiguous.append((rs[0]["name"], pred, [r["value"] for r in reps],
                          [r["source_type"] for r in reps]))
        continue
    losers = [r for r in rs if r["id"] != winner["id"]]
    resolved.append((winner, losers, reason))
    fixed = TRUNCATED.get((winner["value"] or "").strip().lower())
    if fixed:
        fixes.append((winner["id"], winner["value"], fixed, winner["name"]))

print("  fields holding more than one current value: %d"
      % (len(resolved) + len(ambiguous)))
print("  resolvable on evidence : %d  %s"
      % (len(resolved), dict(collections.Counter(
          r.split(" ")[0] for _w, _l, r in resolved))))
print("  left for a human       : %d" % len(ambiguous))
for w, ls, reason in resolved[:10]:
    print("     %-20s %-13s keep %-38r  [%s]"
          % (str(w["name"])[:20], w["predicate"], str(w["value"])[:38], reason))
    for l in ls[:2]:
        print("        supersede %-38r [%s]" % (str(l["value"])[:38], l["source_type"]))
print("\n  ambiguous, left alone (sample):")
for nm, pred, vals, srcs in ambiguous[:10]:
    print("     %-20s %-13s %s" % (str(nm)[:20], pred, str(vals)[:64]))
    print("        %s" % srcs)

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

con.execute("BEGIN IMMEDIATE")
try:
    for w, ls, _r in resolved:
        for l in ls:
            con.execute("UPDATE facts SET valid_until=?, superseded_by=? WHERE id=?",
                        (now, w["id"], l["id"]))
    for fid, _was, new, _nm in fixes:
        con.execute("UPDATE facts SET value=? WHERE id=?", (new, fid))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "contradictions-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "resolved": [{"name": w["name"], "predicate": w["predicate"],
                         "kept": w["value"], "reason": rsn,
                         "superseded": [{"id": l["id"], "value": l["value"],
                                         "source": l["source_type"]} for l in ls]}
                        for w, ls, rsn in resolved],
           "left_for_human": [{"name": n, "predicate": p2, "values": v,
                               "sources": s} for n, p2, v, s in ambiguous],
           "revert_sql": "UPDATE facts SET valid_until=NULL, superseded_by=NULL "
                         "WHERE id IN (%s)"
                         % ",".join("'%s'" % l["id"] for _w, ls, _r in resolved
                                    for l in ls)},
          open(p, "w"), indent=1, default=str)
print("  applied; audit %s" % p)
print("VERIFY superseded_by dangling: %d (want 0)" % con.execute(
    "SELECT COUNT(*) FROM facts WHERE superseded_by IS NOT NULL "
    "AND superseded_by NOT IN (SELECT id FROM facts)").fetchone()[0])
