#!/usr/bin/env python3
"""Undo the enrichment->google->weave round trip.

Until the outbound URL query was gated on source_type, every URL ocas-scout
guessed was written into the real address book, and the next inbound re-imported
it as a 'google_contacts' fact -- the provenance the gate treats as owner-typed.
So a guess became curated data in one round trip, and pages like a fashion
article or a namesake's wikipedia entry are now sitting on real contacts.

A URL is treated as laundered when weave holds it from an enrichment source AND
either weave has no google_contacts fact for it, or that fact was recorded AFTER
the enrichment one. A URL the owner had first (google_contacts fact older than
the scout one) is left alone -- scout merely rediscovered it.

Removes them from google's urls, and retires the laundered weave facts.
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
    "SELECT f.id, f.predicate, f.value, f.source_type, f.record_time, "
    "e.source_id AS pid, p.name, p.google_resource_name AS rn "
    "FROM facts f JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
    "JOIN persons p ON p.id = e.source_id "
    "WHERE f.valid_until IS NULL AND p.google_resource_name IS NOT NULL "
    "AND (f.predicate LIKE 'profile\\_%' ESCAPE '\\' "
    "OR f.predicate IN ('linkedin', 'website'))").fetchall()

# per (person, url key): the earliest enrichment record and the earliest owner record
inf_at, own_at, inf_rows, own_rows = {}, {}, {}, {}
for r in rows:
    k = (r["pid"], dedupe_key(r["value"]))
    if k[1] is None:
        continue
    if (r["source_type"] or "") in INFERRED:
        if k not in inf_at or r["record_time"] < inf_at[k]:
            inf_at[k] = r["record_time"]
        inf_rows.setdefault(k, []).append(r)
    else:
        if k not in own_at or r["record_time"] < own_at[k]:
            own_at[k] = r["record_time"]
        own_rows.setdefault(k, []).append(r)

laundered = set()
for k, t in inf_at.items():
    if k not in own_at or own_at[k] > t:
        laundered.add(k)
print("  enrichment-sourced url keys : %d" % len(inf_at))
print("  of those, laundered         : %d" % len(laundered))

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

pid_of = {r["rn"]: r["pid"] for r in rows}
plans, removed_examples = [], []
for p in people:
    pid = pid_of.get(p["resourceName"])
    if not pid or not p.get("urls"):
        continue
    keep, drop = [], []
    for u in p["urls"]:
        k = (pid, dedupe_key(u.get("value")))
        (drop if k in laundered else keep).append(u)
    if drop:
        plans.append((p, keep, drop))
        for u in drop[:2]:
            removed_examples.append(((p.get("names") or [{}])[0].get("displayName", "?"),
                                     u.get("value")))
print("  google contacts to clean    : %d" % len(plans))
print("  google url entries to remove: %d" % sum(len(d) for _p, _k, d in plans))
for n, v in removed_examples[:18]:
    print("     %-22s %s" % (n[:22], str(v)[:60]))

wretire = [r for k in laundered for r in own_rows.get(k, [])]
print("  weave facts to retire (laundered 'google_contacts' copies): %d" % len(wretire))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
os.makedirs(AUDIT_DIR, exist_ok=True)
audit = os.path.join(AUDIT_DIR, "unlaunder-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "google_removed": [{"resourceName": p["resourceName"],
                               "name": (p.get("names") or [{}])[0].get("displayName"),
                               "removed": [u.get("value") for u in d]}
                              for p, _k, d in plans],
           "weave_retired": [dict(r) for r in wretire],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r in wretire)},
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
    for r in wretire:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("google contacts updated=%d failed=%d; weave facts retired=%d\naudit: %s"
      % (written, failed, len(wretire), audit))
