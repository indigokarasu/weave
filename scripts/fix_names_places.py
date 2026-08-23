#!/usr/bin/env python3
"""Two more classes from the audit.

A. Three contacts whose NAME is an email address. A name field holding
   'katie@pictalhealth.com' is unusable for matching, display or relation
   resolution. Derive a name from the local part where it plainly encodes one
   (first.last / first_last), and leave it alone otherwise rather than guessing.

B. location_city holds the same place spelled several ways ('San Francisco, CA'
   vs 'San Francisco, CA, USA', 'SF' vs 'San Francisco', 'Sonoma' vs
   'Sonoma, CA'). Keep the most informative spelling and retire the rest, so the
   contact has ONE current city.
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

ABBREV = {"sf": "San Francisco", "nyc": "New York", "la": "Los Angeles",
          "sfo": "San Francisco", "bay area": "San Francisco Bay Area"}
COUNTRY_TAIL = re.compile(r"\s*,\s*(usa|u\.s\.a\.|united states(?: of america)?|us|"
                          r"uk|u\.k\.|united kingdom)\s*$", re.I)


def canon_place(v):
    """Comparison key: drop a trailing country and any punctuation."""
    s = COUNTRY_TAIL.sub("", (v or "").strip())
    s = ABBREV.get(s.strip().lower(), s)
    return re.sub(r"[^a-z]", "", s.lower())


def informativeness(v):
    """More parts and more characters = more informative."""
    return (len([p for p in (v or "").split(",") if p.strip()]), len(v or ""))


def tidy_place(v):
    """Drop a country that the region already implies.

    'San Francisco, CA, USA' -> 'San Francisco, CA'  (CA implies USA)
    'Bristol, UK'            -> 'Bristol, UK'        (Bristol alone is ambiguous)
    """
    parts = [p.strip() for p in (v or "").split(",") if p.strip()]
    if len(parts) >= 3 and COUNTRY_TAIL.search(", " + parts[-1]):
        return ", ".join(parts[:-1])
    return ", ".join(parts)


ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
now = datetime.now(timezone.utc).isoformat()

# ---- A. names that are email addresses
name_fixes = []
for r in con.execute("SELECT id, name, name_given, name_family, email FROM persons"):
    nm = (r["name"] or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", nm):
        continue
    local = nm.split("@")[0]
    parts = [p for p in re.split(r"[._\-]+", local) if p.isalpha() and len(p) > 1]
    if len(parts) >= 2:
        derived = " ".join(p.capitalize() for p in parts[:2])
        name_fixes.append((r["id"], nm, derived, parts[0].capitalize(),
                           parts[1].capitalize(), r["email"]))
    else:
        name_fixes.append((r["id"], nm, None, None, None, r["email"]))
print("  A. contacts whose name is an email address: %d" % len(name_fixes))
for _i, old, new, g, f, em in name_fixes:
    print("       %-34s -> %s" % (old, ("%r (given=%r family=%r)" % (new, g, f))
                                  if new else "cannot derive a name; left alone"))

# ---- B. one city per contact
rows = con.execute(
    "SELECT f.id, f.value, f.record_time, e.source_id pid, p.name FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id "
    "WHERE f.predicate='location_city' AND f.valid_until IS NULL "
    "ORDER BY f.record_time").fetchall()
by = collections.defaultdict(list)
for r in rows:
    by[r["pid"]].append(r)
retire, col_fix, rewrites = [], [], []
for pid, rs in by.items():
    groups = collections.defaultdict(list)
    for r in rs:
        groups[canon_place(r["value"])].append(r)
    for _k, g in groups.items():
        if len(g) < 2:
            continue
        best = max(g, key=lambda r: informativeness(r["value"]))
        keep_val = tidy_place(best["value"])
        for r in g:
            if r["id"] != best["id"]:
                retire.append((r, keep_val))
        # rewrite the survivor if the country was redundant
        if keep_val != best["value"]:
            rewrites.append((best["id"], best["value"], keep_val, best["name"]))
        col_fix.append((pid, keep_val, best["name"]))
print("\n  B. duplicate spellings of one city: %d fact(s) to retire" % len(retire))
if rewrites:
    print("     survivors rewritten to drop a redundant country: %d" % len(rewrites))
    for _i, was, now_, nm in rewrites[:6]:
        print("       %-24s %-26r -> %r" % (str(nm)[:24], was, now_))
seen = set()
for r, keep in retire[:10]:
    if r["name"] in seen:
        continue
    seen.add(r["name"])
    print("       %-24s drop %-26r keep %r" % (str(r["name"])[:24], r["value"], keep))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

con.execute("BEGIN IMMEDIATE")
try:
    for pid, old, new, g, f, _em in name_fixes:
        if new:
            con.execute("UPDATE persons SET name=?, name_given=?, name_family=? "
                        "WHERE id=?", (new, g, f, pid))
    for r, _keep in retire:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    for fid, _was, new_v, _nm in rewrites:
        con.execute("UPDATE facts SET value=? WHERE id=?", (new_v, fid))
    for pid, best, _nm in col_fix:
        con.execute("UPDATE persons SET location_city=? WHERE id=? AND "
                    "(location_city IS NULL OR location_city='')", (best, pid))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "names-places-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "name_fixes": [{"id": i, "was": o, "now": n} for i, o, n, _g, _f, _e in name_fixes],
           "retired_city_facts": [dict(r) for r, _k in retire],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r, _k in retire)},
          open(p, "w"), indent=1, default=str)
print("  applied; audit %s" % p)
print("VERIFY names that are emails: %d" % con.execute(
    "SELECT COUNT(*) FROM persons WHERE name LIKE '%@%.%'").fetchone()[0])
