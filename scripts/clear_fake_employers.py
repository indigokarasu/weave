#!/usr/bin/env python3
"""Clear employers that are provably not employers.

Only values that can be judged wrong from the record itself. The wider
"enrichment-added and uncorroborated" set is NOT touched: it contains Berkeley
Lab, IDEO and Cruise Automation, which are almost certainly right, and deleting
those to be rid of the wrong ones would repeat the Heriot-Watt mistake in the
other direction.

  United Airlines  org 'American'                    a competitor airline, and
                   title 'United CEO floated idea of United'
                                                     a news headline
  Toast            org 'Saltlaketoastmastersclub'    the scraper matched "toast"
                                                     against a Toastmasters club
  Hilary Hayes     org 'Dedham'                      a town in Massachusetts
  Shahyar Ghobadpour org 'Engineering'               a discipline, not a company
  Samantha Tripodi org 'Angel Investor / Individual' a role; her title already
                                                     says Angel Investor
  Jason Jones      org 'Independent / Freelance'     a placeholder

Left alone after review:
  Summer Bedard    org 'Facebook AI'   her email is bedard@meta.com -- Facebook
                                       IS Meta, so this is corroborated; the
                                       corroboration test simply did not know
                                       the two names refer to one company.
  Michelle Nguyen  org 'Canyon Market' a real business; unverified but possible.
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

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
API = "https://people.googleapis.com/v1"

# name -> (clear_org, clear_title)
CLEAR = {
    "United Airlines":    (True, True),
    "Toast":              (True, False),
    "Hilary Hayes":       (True, False),
    "Shahyar Ghobadpour": (True, False),
    "Samantha Tripodi":   (True, False),
    "Jason Jones":        (True, False),
}

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
plans = []
for name, (co, ct) in CLEAR.items():
    r = con.execute("SELECT id, name, org, occupation, google_resource_name rn "
                    "FROM persons WHERE name = ?", (name,)).fetchone()
    if r:
        plans.append((dict(r), co, ct))
print("  contacts to clear: %d" % len(plans))
for r, co, ct in plans:
    print("     %-20s org=%-32r%s title=%-34r%s"
          % (str(r["name"])[:20], str(r["org"])[:32], " -> ''" if co else "     ",
             str(r["occupation"])[:34], " -> ''" if ct else ""))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r, co, ct in plans:
        if co:
            con.execute("UPDATE persons SET org='' WHERE id=?", (r["id"],))
        if ct:
            con.execute("UPDATE persons SET occupation='' WHERE id=?", (r["id"],))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  weave cleared")

tok = G.get_access_token()
want = {r["rn"]: (co, ct) for r, co, ct in plans if r["rn"]}
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
    if p["resourceName"] not in want:
        continue
    co, ct = want[p["resourceName"]]
    orgs = []
    for o in (p.get("organizations") or []):
        o2 = {k: v for k, v in o.items() if k != "metadata"}
        if co:
            o2.pop("name", None)
        if ct:
            o2.pop("title", None)
        if any(str(o2.get(k) or "").strip() for k in ("name", "title", "department")):
            orgs.append(o2)
    gplans.append((p, orgs))
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
p = os.path.join(AUDIT_DIR, "fake-employers-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "cleared": [{"name": r["name"], "was_org": r["org"],
                                       "was_title": r["occupation"]}
                                      for r, _c, _t in plans]},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
