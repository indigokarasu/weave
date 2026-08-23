#!/usr/bin/env python3
"""Clear scrape junk out of org/occupation, in weave and in Google.

63 values flagged by job_junk_v3, minus two false positives found on review:
  'HelloKindred (formerly VentureWeb)'  a real company that renamed -- the word
                                        "formerly" in a parenthetical is part of
                                        the name, not commentary about the role
  'eyeStarr'                            Andrea STARR's own company; the
                                        lowercase opener is branding

Cleared rather than corrected: a fragment like 'ogyBud' or 'ndidate' cannot be
repaired, and inventing a plausible title would be worse than an empty field.
The contact keeps everything else.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402
from job_junk_v3 import classify  # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
API = "https://people.googleapis.com/v1"

# reviewed and kept: the rule fires but the value is real
KEEP = {
    "hellokindred (formerly ventureweb)",   # the company renamed
    "eyestarr",                             # Andrea Starr's own company
}

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
names = {r[0].strip().lower() for r in con.execute(
    "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")}

rows = list(con.execute(
    "SELECT id pid, name, occupation v, 'occupation' f, google_resource_name rn "
    "FROM persons WHERE occupation IS NOT NULL AND occupation != '' "
    "UNION ALL SELECT id, name, org, 'org', google_resource_name FROM persons "
    "WHERE org IS NOT NULL AND org != ''"))

plans = []
for r in rows:
    if (r["v"] or "").strip().lower() in KEEP:
        continue
    why = classify(r["v"], r["name"], r["f"], names)
    if why:
        plans.append((dict(r), why))

by_reason = {}
for r, why in plans:
    by_reason.setdefault(why, []).append(r)
print("  values to clear: %d" % len(plans))
for why in sorted(by_reason):
    print("\n   %s (%d)" % (why, len(by_reason[why])))
    for r in sorted(by_reason[why], key=lambda x: (x["v"] or "").lower())[:20]:
        print("      %-24s %-11s %r" % (str(r["name"])[:24], r["f"], str(r["v"])[:54]))

# also clear the matching fact rows, so the next sync does not put them back
fact_rows = []
for r in con.execute(
        "SELECT f.id, f.predicate, f.value, e.source_id pid, p.name FROM facts f "
        "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
        "JOIN persons p ON p.id=e.source_id WHERE f.valid_until IS NULL "
        "AND f.predicate IN ('org','occupation')"):
    if (r["value"] or "").strip().lower() in KEEP:
        continue
    if classify(r["value"], r["name"], r["predicate"], names):
        fact_rows.append(dict(r))
print("\n  matching FACT rows to retire: %d" % len(fact_rows))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r, _why in plans:
        con.execute("UPDATE persons SET %s='' WHERE id=?" % r["f"], (r["pid"],))
    for r in fact_rows:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  weave updated")

# clear the same values in google
want = {}
for r, _why in plans:
    if r["rn"]:
        want.setdefault(r["rn"], set()).add((r["v"] or "").strip().lower())
tok = G.get_access_token()
people, page = [], None
while True:
    q = {"personFields": "names,organizations,metadata", "pageSize": 1000,
         "sources": "READ_SOURCE_TYPE_CONTACT"}
    if page:
        q["pageToken"] = page
    rq = urllib.request.Request(API + "/people/me/connections?" + urllib.parse.urlencode(q),
                                headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(rq, timeout=60) as rr:
        d = json.loads(rr.read())
    people.extend(d.get("connections", []))
    page = d.get("nextPageToken")
    if not page:
        break

gplans = []
for p in people:
    junk = want.get(p["resourceName"])
    if not junk:
        continue
    orgs, changed = [], False
    for o in (p.get("organizations") or []):
        o2 = {k: v for k, v in o.items() if k != "metadata"}
        for fld in ("name", "title"):
            if (o2.get(fld) or "").strip().lower() in junk:
                o2.pop(fld, None)
                changed = True
        if any(str(o2.get(k) or "").strip() for k in ("name", "title", "department")):
            orgs.append(o2)
        else:
            changed = True
    if changed:
        gplans.append((p, orgs))
print("  google contacts to update: %d" % len(gplans))

written = failed = 0
for i in range(0, len(gplans), 200):
    chunk = gplans[i:i + 200]
    contacts = {p["resourceName"]: {"etag": p.get("etag"), "organizations": o}
                for p, o in chunk}
    try:
        resp = G._api_post(API + "/people:batchUpdateContacts", tok,
                           {"contacts": contacts, "updateMask": "organizations",
                            "readMask": "organizations"}, timeout=120)
        for _rn, res in (resp.get("updateResult") or {}).items():
            if (res.get("status") or {}).get("code"):
                failed += 1
            else:
                written += 1
    except Exception as e:  # noqa: BLE001
        failed += len(chunk)
        print("  batch error: %s" % e)
    time.sleep(0.5)
print("  google updated=%d failed=%d" % (written, failed))

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "job-junk-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "cleared_columns": [{"id": r["pid"], "name": r["name"],
                                "field": r["f"], "value": r["v"], "why": w}
                               for r, w in plans],
           "retired_facts": fact_rows,
           "google_cleared": [{"name": (pp.get("names") or [{}])[0].get("displayName"),
                               "organizations_now": o} for pp, o in gplans]},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
