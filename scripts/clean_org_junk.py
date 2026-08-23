#!/usr/bin/env python3
"""Clear the company-field junk, in weave and Google.

Held back from the flagged set after review:
  Ramp 💳 -> 'Ramp'    a company contact; org == its own name is correct
  Kaya Justeau-Sasaki -> 'Sasaki'
                       Sasaki is a real design practice, so this may be her
                       actual employer rather than half of her surname. Reported,
                       not removed -- the two readings are indistinguishable from
                       the data.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402
from org_junk import classify_org  # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
API = "https://people.googleapis.com/v1"
HOLD_BACK = {("ramp 💳", "ramp"), ("kaya justeau-sasaki", "sasaki")}

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
rows = list(con.execute(
    "SELECT id pid, name, name_given, name_family, org v, google_resource_name rn "
    "FROM persons WHERE org IS NOT NULL AND org != ''"))
plans = []
for r in rows:
    is_person = bool((r["name_given"] or "").strip() and (r["name_family"] or "").strip())
    why = classify_org(r["v"], r["name"], not is_person)
    if not why:
        continue
    if ((r["name"] or "").strip().lower(), (r["v"] or "").strip().lower()) in HOLD_BACK:
        print("  held back: %-24s org=%r" % (str(r["name"])[:24], r["v"]))
        continue
    plans.append((dict(r), why))

print("\n  company values to clear: %d" % len(plans))
for r, why in plans:
    print("     %-26s org=%-52r %s" % (str(r["name"])[:26], str(r["v"])[:52], why))

fact_rows = []
for r in con.execute(
        "SELECT f.id, f.value, e.source_id pid, p.name, p.name_given, p.name_family "
        "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
        "JOIN persons p ON p.id=e.source_id "
        "WHERE f.valid_until IS NULL AND f.predicate='org'"):
    is_person = bool((r["name_given"] or "").strip() and (r["name_family"] or "").strip())
    if ((r["name"] or "").strip().lower(), (r["value"] or "").strip().lower()) in HOLD_BACK:
        continue
    if classify_org(r["value"], r["name"], not is_person):
        fact_rows.append(dict(r))
print("  matching org FACT rows to retire: %d" % len(fact_rows))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r, _w in plans:
        con.execute("UPDATE persons SET org='' WHERE id=?", (r["pid"],))
    for r in fact_rows:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  weave updated")

want = {}
for r, _w in plans:
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
        if (o2.get("name") or "").strip().lower() in junk:
            o2.pop("name", None)
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
p = os.path.join(AUDIT_DIR, "org-junk-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "cleared": [{"id": r["pid"], "name": r["name"], "org": r["v"], "why": w}
                       for r, w in plans],
           "retired_facts": fact_rows,
           "held_back": sorted(HOLD_BACK)},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
