#!/usr/bin/env python3
"""Remove the aggregator / encyclopedia / catalogue URLs enrichment already wrote.

Uses the same classifier the write path now uses, so what is removed here is
exactly what would no longer be written. Enrichment-sourced only: a URL the owner
typed is theirs to keep, whatever it points at.

Removes them from google's urls and retires the weave facts (valid_until, with an
audit carrying the revert).
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
from url_quality import is_person_profile  # noqa: E402

API = "https://people.googleapis.com/v1"
DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
INFERRED = ("scout_osint", "scout_research", "web_enrichment", "inferred", "llm", "search")

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, e.source_id AS pid, "
    "p.name, p.org, p.email, p.google_resource_name AS rn "
    "FROM facts f JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
    "JOIN persons p ON p.id = e.source_id "
    "WHERE f.valid_until IS NULL AND "
    "(f.predicate LIKE 'profile\\_%' ESCAPE '\\' OR f.predicate IN ('linkedin','website'))"
).fetchall()

_all_names = [r["name"] for r in con.execute(
    "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")]
owner_keys = {(r["pid"], dedupe_key(r["value"])) for r in rows
              if (r["source_type"] or "") not in INFERRED}
bad_facts, bad_keys = [], {}
for r in rows:
    if (r["source_type"] or "") not in INFERRED:
        continue
    _plat = r["predicate"][8:] if r["predicate"].startswith("profile_") else None
    if is_person_profile(r["value"], r["name"] or "", _plat, _all_names,
                         org=r["org"] or "", email=r["email"] or ""):
        continue
    k = (r["pid"], dedupe_key(r["value"]))
    if k in owner_keys:
        continue                      # the owner has it too; leave it alone
    bad_facts.append(r)
    bad_keys.setdefault(r["rn"], set()).add(k[1])

print("  url facts examined            : %d" % len(rows))
print("  enrichment-written junk facts : %d across %d contact(s)"
      % (len(bad_facts), len({r["pid"] for r in bad_facts})))
import collections
print("  by predicate: %s"
      % dict(collections.Counter(r["predicate"] for r in bad_facts).most_common(8)))
for r in bad_facts[:14]:
    print("     %-22s %-16s %s" % (r["name"][:22], r["predicate"], r["value"][:52]))

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
    junk = bad_keys.get(p["resourceName"])
    if not junk or not p.get("urls"):
        continue
    keep = [u for u in p["urls"] if dedupe_key(u.get("value")) not in junk]
    drop = [u for u in p["urls"] if dedupe_key(u.get("value")) in junk]
    if drop:
        plans.append((p, [{k: v for k, v in u.items() if k != "metadata"} for u in keep],
                      [u.get("value") for u in drop]))
print("\n  google contacts to clean      : %d" % len(plans))
print("  google url entries to remove  : %d" % sum(len(d) for _p, _k, d in plans))
for p, _k, d in plans[:12]:
    print("     %-22s %s" % ((p.get("names") or [{}])[0].get("displayName", "?")[:22], d[:2]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
os.makedirs(AUDIT_DIR, exist_ok=True)
audit = os.path.join(AUDIT_DIR, "junk-urls-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "weave_retired": [dict(r) for r in bad_facts],
           "google_removed": [{"resourceName": p["resourceName"],
                               "name": (p.get("names") or [{}])[0].get("displayName"),
                               "removed": d} for p, _k, d in plans],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r in bad_facts)},
          open(audit, "w"), indent=1)

written = failed = 0
for i in range(0, len(plans), 200):
    chunk = plans[i:i + 200]
    contacts = {p["resourceName"]: {"etag": p.get("etag"), "urls": keep}
                for p, keep, _d in chunk}
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

con.execute("BEGIN IMMEDIATE")
try:
    for r in bad_facts:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("google updated=%d failed=%d ; weave facts retired=%d\naudit: %s"
      % (written, failed, len(bad_facts), audit))
