#!/usr/bin/env python3
"""Clear what the four defects already wrote.

  B  547 `website` facts that are our own pushed profile URLs, re-imported and
     relabelled 'google_contacts'. The profile_* fact is the real record; the
     `website` copy is a duplicate with false provenance.
  C  profile URLs whose handle is numeric.
  D  location_city facts and columns holding a country or placeholder.

Retire, never delete. A `website` fact is only retired when the SAME url is held
under a profile_* predicate for the SAME person -- a website nobody else holds is
left alone.
"""
import argparse
import collections
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
from url_norm import dedupe_key  # noqa: E402
from url_quality import handle_is_opaque  # noqa: E402
from weave_enrich import _is_a_city  # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
now = datetime.now(timezone.utc).isoformat()

rows = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, e.source_id pid, p.name "
    "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id WHERE f.valid_until IS NULL").fetchall()

held = collections.defaultdict(set)
for r in rows:
    if str(r["predicate"]).startswith("profile_"):
        k = dedupe_key(r["value"])
        if k:
            held[r["pid"]].add(k)

retire_b, retire_c, retire_d = [], [], []
for r in rows:
    pred, val = str(r["predicate"]), r["value"]
    if pred == "website" and dedupe_key(val) in held.get(r["pid"], set()):
        retire_b.append(r)
    elif pred.startswith("profile_") and handle_is_opaque(val):
        retire_c.append(r)
    elif pred == "location_city" and not _is_a_city(val):
        retire_d.append(r)

print("  B duplicate `website` copies of a profile url : %d" % len(retire_b))
for r in retire_b[:5]:
    print("       %-24s %s" % (r["name"][:24], str(r["value"])[:52]))
print("  C profile urls with an opaque/numeric handle  : %d" % len(retire_c))
for r in retire_c[:8]:
    print("       %-24s %-18s %s" % (r["name"][:24], r["predicate"], str(r["value"])[:44]))
print("  D location_city facts that are not a city     : %d" % len(retire_d))
for r in retire_d[:6]:
    print("       %-24s %r" % (r["name"][:24], r["value"]))

cols = con.execute("SELECT id, name, location_city FROM persons "
                   "WHERE location_city IS NOT NULL AND location_city != ''").fetchall()
col_clear = [c for c in cols if not _is_a_city(c["location_city"])]
print("  D persons.location_city columns to clear      : %d" % len(col_clear))
for c in col_clear:
    print("       %-24s %r" % (c["name"][:24], c["location_city"]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

allr = retire_b + retire_c + retire_d
con.execute("BEGIN IMMEDIATE")
try:
    for r in allr:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    for c in col_clear:
        con.execute("UPDATE persons SET location_city='' WHERE id=?", (c["id"],))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "backlog-clear-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "duplicate_website": [dict(r) for r in retire_b],
           "opaque_handle": [dict(r) for r in retire_c],
           "not_a_city": [dict(r) for r in retire_d],
           "columns_cleared": [dict(c) for c in col_clear],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r in allr)},
          open(p, "w"), indent=1, default=str)
print("  applied; audit %s" % p)
print("VERIFY live facts: %d" % con.execute(
    "SELECT COUNT(*) FROM facts WHERE valid_until IS NULL").fetchone()[0])
