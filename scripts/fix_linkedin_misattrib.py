#!/usr/bin/env python3
"""Remove LinkedIn profiles that belong to a different person.

A LinkedIn /in/ slug is built from the owner's real name, so a hyphenated,
multi-word slug sharing nothing with the contact is somebody else's profile:

    linkedin.com/in/umer-farooq-88b5b2      filed under Nicole Bacchus
    linkedin.com/in/celine-tien-70828698    filed under Jessy Yoon
    linkedin.com/in/judy-ziyu-zhu-50278339  filed under Bryce Reid
    linkedin.com/in/matthew-grimes-ab39a45  filed under Paul Theriot

Each exists twice -- once from enrichment and once re-imported from google after
being pushed there, which is why the purge's owner-exemption protected it.

Targeted on purpose. The general exemption change this would suggest would sweep
420 URLs that were in google before this session, which is far too broad to do on
inference.
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
from url_norm import dedupe_key  # noqa: E402
from url_quality import linkedin_slug_is_someone_else  # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
API = "https://people.googleapis.com/v1"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, p.name, "
    "p.google_resource_name rn FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id WHERE f.valid_until IS NULL "
    "AND f.predicate IN ('linkedin', 'profile_linkedin')").fetchall()

bad = [r for r in rows if linkedin_slug_is_someone_else(r["value"], r["name"] or "")]
print("  linkedin facts naming a different person: %d across %d contact(s)"
      % (len(bad), len({r["name"] for r in bad})))
for r in bad:
    print("     %-22s %-18s %-46s %s"
          % (str(r["name"])[:22], r["predicate"], str(r["value"])[:46], r["source_type"]))

by_rn = {}
for r in bad:
    if r["rn"]:
        by_rn.setdefault(r["rn"], set()).add(dedupe_key(r["value"]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r in bad:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  weave facts retired: %d" % len(bad))

tok = G.get_access_token()
people, page = [], None
while True:
    q = {"personFields": "names,urls,metadata", "pageSize": 1000,
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

plans = []
for p in people:
    drop = by_rn.get(p["resourceName"])
    if not drop or not p.get("urls"):
        continue
    keep = [{k: v for k, v in u.items() if k != "metadata"}
            for u in p["urls"] if dedupe_key(u.get("value")) not in drop]
    removed = [u.get("value") for u in p["urls"] if dedupe_key(u.get("value")) in drop]
    if removed:
        plans.append((p, keep, removed))
print("  google contacts to clean: %d" % len(plans))
for p, _k, rm in plans:
    print("     %-24s remove %s"
          % ((p.get("names") or [{}])[0].get("displayName", "?")[:24], rm))

written = failed = 0
for i in range(0, len(plans), 200):
    chunk = plans[i:i + 200]
    contacts = {p["resourceName"]: {"etag": p.get("etag"), "urls": keep}
                for p, keep, _r in chunk}
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
        print("  batch error: %s" % e)
    time.sleep(0.5)
print("  google updated=%d failed=%d" % (written, failed))

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "linkedin-misattribution-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "retired": [dict(r) for r in bad],
           "google_removed": [{"name": (pp.get("names") or [{}])[0].get("displayName"),
                               "removed": rm} for pp, _k, rm in plans],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r in bad)},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
