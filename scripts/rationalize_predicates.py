#!/usr/bin/env python3
"""One URL, one predicate, and the predicate should name the platform.

Two problems:

  linkedin vs profile_linkedin   the same URL under two predicate names, so it
                                 counts twice as corroboration and shows twice.
  website = a platform profile   quora.com/profile/<x>, 500px.com/<x>,
                                 flickr.com/people/<x> filed as a generic
                                 `website` when the platform is known.

Reclassifying is not cosmetic: the quality gate treats a KNOWN platform by its
path shape and an unknown host much more strictly, so a mislabelled profile is
judged by the wrong rule -- which is why 414 of these show as "rejectable".

Retires the redundant copy (never deletes) and rewrites the predicate where the
platform is recognisable.
"""
import argparse
import collections
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from contact_urls import classify_url  # noqa: E402
from url_norm import dedupe_key  # noqa: E402

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")

# the canonical predicate when two names mean the same thing
# profile_<platform> names a platform; profile_website names none, so the
# generic case is just . linkedin has both spellings for historic
# reasons and folds into the platform form.
ALIASES = {"linkedin": "profile_linkedin", "linkedin_url": "profile_linkedin",
           "profile_website": "website"}

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
    "AND (f.predicate LIKE 'profile\\_%' ESCAPE '\\' "
    "OR f.predicate IN ('website', 'linkedin', 'linkedin_url')) "
    "ORDER BY f.record_time").fetchall()

# 1. rename alias predicates, and reclassify `website` where the platform is known
renames = []
for r in rows:
    pred = str(r["predicate"])
    new = ALIASES.get(pred)
    if not new and pred == "website":
        klass, _canon = classify_url(r["value"])
        if klass and klass != "website":
            new = ALIASES.get(klass, klass)
    if new and new != pred:
        renames.append((r, new))
print("  predicates to rewrite: %d" % len(renames))
print("     %s" % dict(collections.Counter("%s -> %s" % (r["predicate"], n)
                                           for r, n in renames).most_common(8)))
for r, n in renames[:6]:
    print("       %-22s %-16s -> %-18s %s"
          % (str(r["name"])[:22], r["predicate"], n, str(r["value"])[:38]))

# 2. after renaming, collapse (person, predicate, url) duplicates
after = {}
dupes = []
for r in rows:
    pred = dict(renames).get(r["id"])
    pred = pred if pred else str(r["predicate"])
    pred = next((n for rr, n in renames if rr["id"] == r["id"]), str(r["predicate"]))
    k = (r["pid"], pred, dedupe_key(r["value"]))
    if k in after:
        dupes.append((r, after[k]))
    else:
        after[k] = r["id"]
print("\n  duplicate (person, predicate, url) after rewriting: %d" % len(dupes))
for r, _keep in dupes[:6]:
    print("       %-22s %-16s %s" % (str(r["name"])[:22], r["predicate"],
                                     str(r["value"])[:42]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

dupe_ids = {r["id"] for r, _k in dupes}
con.execute("BEGIN IMMEDIATE")
try:
    for r, new in renames:
        if r["id"] in dupe_ids:
            continue
        con.execute("UPDATE facts SET predicate=? WHERE id=?", (new, r["id"]))
    for r, _keep in dupes:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "predicate-rationalize-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "renamed": [{"id": r["id"], "was": r["predicate"], "now": n,
                        "value": r["value"], "name": r["name"]} for r, n in renames],
           "retired_duplicates": [dict(r) for r, _k in dupes],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r, _k in dupes)},
          open(p, "w"), indent=1, default=str)
print("  applied; audit %s" % p)
print("VERIFY legacy `linkedin` predicate remaining: %d" % con.execute(
    "SELECT COUNT(*) FROM facts WHERE predicate IN ('linkedin','linkedin_url') "
    "AND valid_until IS NULL").fetchone()[0])
seen = {}
multi = 0
for r in con.execute(
        "SELECT e.source_id pid, f.value, f.predicate FROM facts f "
        "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
        "WHERE f.valid_until IS NULL AND (f.predicate LIKE 'profile\\_%' ESCAPE '\\' "
        "OR f.predicate='website')"):
    k = (r["pid"], dedupe_key(r["value"]))
    seen.setdefault(k, set()).add(r["predicate"])
multi = sum(1 for v in seen.values() if len(v) > 1)
print("VERIFY one url under >1 predicate: %d (want 0)" % multi)
