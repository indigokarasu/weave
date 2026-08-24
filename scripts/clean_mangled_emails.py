#!/usr/bin/env python3
"""Retire the 'website' facts that are really mangled email addresses.

The old inbound classifier prepended a scheme to whatever sat in a url slot, so
'artsinbox@gmail.com' parsed as userinfo + host and became the website
'https://gmail.com'. Dozens of unrelated people ended up sharing one meaningless
'website'. url_norm now rejects a bare email outright; this clears what the old
behaviour already wrote.

Facts are retired with valid_until, never deleted. persons.website is cleared
only where it holds one of these values.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")
PROVIDERS = ["gmail.com", "yahoo.com", "hotmail.com", "me.com", "aol.com",
             "outlook.com", "icloud.com", "comcast.net", "sbcglobal.net",
             "mac.com", "msn.com", "live.com", "ymail.com", "googlemail.com",
             "protonmail.com", "att.net", "verizon.net", "earthlink.net"]
VALUES = ["https://%s" % h for h in PROVIDERS] + ["http://%s" % h for h in PROVIDERS] \
    + ["https://www.%s" % h for h in PROVIDERS]

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=30)
con.row_factory = sqlite3.Row
con.execute("PRAGMA busy_timeout=30000")
ph = ",".join("?" * len(VALUES))
rows = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, e.source_id AS pid, p.name "
    "FROM facts f JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
    "JOIN persons p ON p.id = e.source_id "
    "WHERE f.valid_until IS NULL AND f.value IN (%s)" % ph, VALUES).fetchall()
cols = con.execute("SELECT id, name, website FROM persons WHERE website IN (%s)" % ph,
                   VALUES).fetchall()
print("  facts to retire            : %d" % len(rows))
print("  persons.website to clear   : %d" % len(cols))
for r in rows[:8]:
    print("     %-22s %-10s %s" % (r["name"][:22], r["predicate"], r["value"]))
for r in cols[:8]:
    print("     column %-22s %s" % (r["name"][:22], r["website"]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r in rows:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    for r in cols:
        con.execute("UPDATE persons SET website=NULL WHERE id=?", (r["id"],))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise

os.makedirs(AUDIT_DIR, exist_ok=True)
path = os.path.join(AUDIT_DIR, "mangled-email-urls-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "retired_facts": [dict(r) for r in rows],
           "cleared_website_columns": [dict(r) for r in cols],
           "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (%s)"
                         % ",".join("'%s'" % r["id"] for r in rows)},
          open(path, "w"), indent=1)
print("applied; audit at %s" % path)
left = con.execute("SELECT COUNT(*) FROM facts WHERE valid_until IS NULL AND value IN (%s)"
                   % ph, VALUES).fetchone()[0]
print("VERIFY remaining: %d (want 0)" % left)
