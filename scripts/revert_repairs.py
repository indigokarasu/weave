#!/usr/bin/env python3
"""Undo four "repairs" that were actually fabrications.

I took mangled scrape fragments and reconstructed them into real institutions:

    'Watt'    + 'Heriot'                 -> 'Heriot-Watt University'
    'Buffalo' + 'University'             -> 'University at Buffalo'
    'Austin'  + 'Univ of Texas'          -> 'University of Texas at Austin'
    'Carnegie'+ 'Computer Interaction Institute'
                                         -> 'Carnegie Mellon University'

Every one of those is a guess dressed as a fact. Heriot-Watt is a specialist
university in Edinburgh; nothing connects a Bay Area PM to it. The fragments came
from a scrape that hit some page for unrelated reasons, and turning them into a
named employer makes the error harder to spot, not easier -- the exact failure
this whole session has been about.

An unverifiable fragment gets cleared. It does not get upgraded.
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

FABRICATED = ["Kim Appelquist", "Frosty Gross", "Theresa Votolato", "Dan Saffer"]

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "SELECT id, name, org, occupation, google_resource_name rn FROM persons "
    "WHERE name IN (%s)" % ",".join("?" * len(FABRICATED)), FABRICATED)]
print("  reverting invented employers on %d contact(s):" % len(rows))
for r in rows:
    print("     %-22s org=%-34r title=%r -> both cleared"
          % (str(r["name"])[:22], str(r["org"])[:34], str(r["occupation"])[:28]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r in rows:
        con.execute("UPDATE persons SET org='', occupation='' WHERE id=?", (r["id"],))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  weave cleared")

tok = G.get_access_token()
want = {r["rn"] for r in rows if r["rn"]}
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
gplans = [(p, []) for p in people if p["resourceName"] in want]
print("  google contacts to clear: %d" % len(gplans))
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
p = os.path.join(AUDIT_DIR, "revert-fabrication-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "reason": "reconstructed employers were fabrications",
           "reverted": rows}, open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
