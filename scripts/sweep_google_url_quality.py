#!/usr/bin/env python3
"""Remove from Google the URLs the publish gate would now refuse.

Purging by weave provenance missed the ones that had already round-tripped: a
scout guess pushed into google and re-imported comes back tagged
'google_contacts', so a source-based sweep skips it and the next sync pushes it
again. This sweep asks the gate directly about what google actually holds.

Protected: any URL present in the pre-push snapshot taken before today's first
write. That snapshot is the best available record of what the owner had, so
nothing in it is removed -- only what enrichment has added since.
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402
import sqlite3  # noqa: E402
from url_norm import dedupe_key  # noqa: E402
from url_quality import is_person_profile  # noqa: E402

API = "https://people.googleapis.com/v1"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect("/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
con.row_factory = sqlite3.Row
names = [r["name"] for r in con.execute(
    "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")]

snap_file = sorted(glob.glob("/root/google-contacts-snapshot-*.json"))[0]
snap = json.load(open(snap_file))
protected = {}
for p in snap:
    protected[p["resourceName"]] = {dedupe_key(u.get("value"))
                                    for u in (p.get("urls") or [])}
print("  protected by the pre-push snapshot (%s): %d urls"
      % (os.path.basename(snap_file), sum(len(v) for v in protected.values())))

tok = G.get_access_token()
people, page = [], None
while True:
    q = {"personFields": "names,urls,metadata", "pageSize": 1000,
         "sources": "READ_SOURCE_TYPE_CONTACT"}
    if page:
        q["pageToken"] = page
    rq = urllib.request.Request(API + "/people/me/connections?" + urllib.parse.urlencode(q),
                                headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(rq, timeout=60) as r:
        d = json.loads(r.read())
    people.extend(d.get("connections", []))
    page = d.get("nextPageToken")
    if not page:
        break

plans = []
for p in people:
    urls = p.get("urls") or []
    if not urls:
        continue
    nm = (p.get("names") or [{}])[0].get("displayName", "")
    safe = protected.get(p["resourceName"], set())
    keep, drop = [], []
    for u in urls:
        v = u.get("value") or ""
        if dedupe_key(v) in safe or is_person_profile(v, nm, None, names):
            keep.append({k: x for k, x in u.items() if k != "metadata"})
        else:
            drop.append(v)
    if drop:
        plans.append((p, nm, keep, drop))

print("  contacts to clean : %d" % len(plans))
print("  urls to remove    : %d" % sum(len(d) for _p, _n, _k, d in plans))
for _p, nm, _k, d in plans[:20]:
    print("     %-22s %s" % (nm[:22], d[:2]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now().strftime("%Y%m%dT%H%M%S")
audit = os.path.join(AUDIT_DIR, "google-url-quality-%s.json" % now)
json.dump([{"resourceName": p["resourceName"], "name": nm, "removed": d}
           for p, nm, _k, d in plans], open(audit, "w"), indent=1)

written = failed = 0
for i in range(0, len(plans), 200):
    chunk = plans[i:i + 200]
    contacts = {p["resourceName"]: {"etag": p.get("etag"), "urls": keep}
                for p, _n, keep, _d in chunk}
    try:
        resp = G._api_post(API + "/people:batchUpdateContacts", tok,
                           {"contacts": contacts, "updateMask": "urls",
                            "readMask": "urls"}, timeout=120)
        for _rn, res in (resp.get("updateResult") or {}).items():
            if (res.get("status") or {}).get("code"):
                failed += 1
            else:
                written += 1
    except Exception as e:  # noqa: BLE001
        failed += len(chunk)
        print("  batch error: %s: %s" % (type(e).__name__, e))
    time.sleep(0.5)
print("updated=%d failed=%d  audit=%s" % (written, failed, audit))
