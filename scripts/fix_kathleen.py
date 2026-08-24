#!/usr/bin/env python3
"""Remove the false enrichment attached to Kathleen Dunkel.

She is Jared's maternal aunt (Debra's sister), per the email_analysis notes on
her own record, which place her in Dallas and then London -- her +1 469 number is
a Dallas area code. Enrichment attached a South African provincial government to
her and put its recruitment mailbox in her email field, in weave AND in google.
One of the two weave rows was even flagged enrichment_status='enriched_corrupt'.

Removed:
  email      e-recruitment@gauteng.gov.za   an org's recruitment mailbox
  org        Gauteng Provincial Government / 'Professional'
  title      'Williams'                     a surname in a job-title field
  location   Johannesburg, South Africa

Kept: name, phone, birthday, and every email_analysis note -- those are the real
record of who she is. Nothing is deleted outright: facts get valid_until, and the
audit carries the previous values.
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
import google_sync as G  # noqa: E402

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")
RN = "people/c3884260161778540397"
BAD_EMAIL = "e-recruitment@gauteng.gov.za"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
rows = con.execute("SELECT * FROM persons WHERE google_resource_name=?", (RN,)).fetchall()
print("  weave rows: %d" % len(rows))
before = [{k: r[k] for k in r.keys()} for r in rows]
for r in rows:
    print("     %s email=%r org=%r occ=%r city=%r"
          % (r["id"][:8], r["email"], r["org"], r["occupation"], r["location_city"]))

bad_facts = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id WHERE p.google_resource_name=? "
    "AND f.valid_until IS NULL AND (f.value LIKE '%gauteng%' OR f.value LIKE '%Gauteng%' "
    "OR f.predicate='enrichment_status')", (RN,)).fetchall()
print("  facts to retire:")
for f in bad_facts:
    print("     %-22s %-46s %s" % (f["predicate"], str(f["value"])[:46], f["source_type"]))

tok = G.get_access_token()
rq = urllib.request.Request(
    "https://people.googleapis.com/v1/%s?personFields=names,emailAddresses,"
    "organizations,addresses,phoneNumbers,birthdays" % RN,
    headers={"Authorization": "Bearer " + tok})
g = json.loads(urllib.request.urlopen(rq, timeout=30).read())
g_emails = [e for e in (g.get("emailAddresses") or [])
            if (e.get("value") or "").lower() != BAD_EMAIL]
print("  google emails: %s -> %s"
      % ([e.get("value") for e in (g.get("emailAddresses") or [])],
         [e.get("value") for e in g_emails]))
print("  google orgs  : %s -> []"
      % [(o.get("name"), o.get("title")) for o in (g.get("organizations") or [])])

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r in rows:
        con.execute("UPDATE persons SET email='', org='', occupation='' WHERE id=?",
                    (r["id"],))
        if "johannesburg" in str(r["location_city"] or "").lower():
            con.execute("UPDATE persons SET location_city='', location_country='' "
                        "WHERE id=?", (r["id"],))
    for f in bad_facts:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, f["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

body = {"contacts": {RN: {"etag": g["etag"],
                          "emailAddresses": [{k: v for k, v in e.items()
                                              if k != "metadata"} for e in g_emails],
                          "organizations": []}},
        "updateMask": "emailAddresses,organizations",
        "readMask": "emailAddresses,organizations"}
resp = G._api_post("https://people.googleapis.com/v1/people:batchUpdateContacts",
                   tok, body, timeout=60)
ok = all(not (r.get("status") or {}).get("code")
         for r in (resp.get("updateResult") or {}).values())
print("  google updated: %s" % ok)

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "kathleen-false-enrichment-%s.json"
                 % now[:19].replace(":", ""))
json.dump({"run_at": now, "weave_before": before,
           "retired_facts": [dict(f) for f in bad_facts],
           "google_before": {k: g.get(k) for k in
                             ("emailAddresses", "organizations", "addresses")},
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % f["id"] for f in bad_facts)},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)

for r in con.execute("SELECT id, name, email, org, occupation, location_city, phone "
                     "FROM persons WHERE google_resource_name=?", (RN,)):
    print("  VERIFY %s email=%r org=%r occ=%r city=%r phone=%r"
          % (r["id"][:8], r["email"], r["org"], r["occupation"],
             r["location_city"], r["phone"]))
