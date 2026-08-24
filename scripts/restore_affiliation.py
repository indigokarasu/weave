#!/usr/bin/env python3
"""Restore facts the pre-affiliation gate wrongly retired.

The last purge removed every unknown-host page with a path, which included real
affiliation pages (a TechCrunch author page, a university faculty directory).
Now that the gate knows about affiliation, re-test everything it retired today
and bring back what would now be kept -- in weave and in google.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
import google_sync as G  # noqa: E402
from url_norm import canonical_url, dedupe_key  # noqa: E402
from url_quality import is_person_profile  # noqa: E402

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
API = "https://people.googleapis.com/v1"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
names = [r[0] for r in con.execute(
    "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")]

# everything retired today by a url purge
rows = con.execute(
    "SELECT f.id, f.predicate, f.value, e.source_id pid, p.name, p.org, p.email, "
    "p.google_resource_name rn FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id "
    "WHERE f.valid_until IS NOT NULL AND f.valid_until > '2026-08-21' "
    "AND (f.predicate LIKE 'profile\\_%' ESCAPE '\\' OR f.predicate='website')"
).fetchall()
print("  url facts retired today: %d" % len(rows))

# facts still held under a profile_* predicate for the same person: their
# `website` twin was retired as a duplicate (defect B), not for quality, and
# must stay retired
import collections
held = collections.defaultdict(set)
for r in con.execute(
        "SELECT e.source_id pid, f.value FROM facts f "
        "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
        "WHERE f.valid_until IS NULL AND f.predicate LIKE 'profile\\_%' ESCAPE '\\'"):
    k = dedupe_key(r["value"])
    if k:
        held[r["pid"]].add(k)

restore = []
for r in rows:
    if r["predicate"] == "website" and dedupe_key(r["value"]) in held.get(r["pid"], set()):
        continue        # duplicate of a live profile_* fact; stays retired
    plat = r["predicate"][8:] if str(r["predicate"]).startswith("profile_") else None
    if is_person_profile(r["value"], r["name"] or "", plat, names,
                         org=r["org"] or "", email=r["email"] or ""):
        restore.append(r)
print("  would now be KEPT (restore): %d" % len(restore))
for r in restore[:14]:
    print("     %-24s %-18s %s" % (str(r["name"])[:24], r["predicate"], str(r["value"])[:46]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

con.execute("BEGIN IMMEDIATE")
try:
    for r in restore:
        con.execute("UPDATE facts SET valid_until=NULL WHERE id=?", (r["id"],))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  weave facts restored: %d" % len(restore))

# put them back in google too, where the contact is linked
want = {}
for r in restore:
    if r["rn"]:
        want.setdefault(r["rn"], []).append(canonical_url(r["value"]))
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
    add = want.get(p["resourceName"])
    if not add:
        continue
    cur = [{k: v for k, v in u.items() if k != "metadata"} for u in (p.get("urls") or [])]
    have = {dedupe_key(u.get("value")) for u in cur}
    new = list(cur)
    for v in add:
        if v and dedupe_key(v) not in have:
            new.append({"value": v})
            have.add(dedupe_key(v))
    if len(new) != len(cur):
        plans.append((p, new))
print("  google contacts to restore urls on: %d" % len(plans))

written = failed = 0
for i in range(0, len(plans), 200):
    chunk = plans[i:i + 200]
    contacts = {p["resourceName"]: {"etag": p.get("etag"), "urls": urls}
                for p, urls in chunk}
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
print("  google restored=%d failed=%d" % (written, failed))
