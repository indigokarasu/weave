import os
"""Locate the values Jared named, wherever they live, and dump the occupation
values so the junk is visible rather than inferred from shape."""
import collections
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
import google_sync as G

con = sqlite3.connect(os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite"))
con.row_factory = sqlite3.Row

FRAGMENTS = ["Heriot", "Past Chiefs", "FounderThe", "Trust Issues",
             "Book Katie Allen"]
print("=== searching weave for the named fragments ===")
for frag in FRAGMENTS:
    like = "%" + frag + "%"
    found = False
    for r in con.execute(
            "SELECT name, 'persons.org' AS loc, org AS v FROM persons WHERE org LIKE ? "
            "UNION ALL SELECT name, 'persons.occupation', occupation FROM persons "
            "WHERE occupation LIKE ?", (like, like)):
        found = True
        print("   %-22s %-20s %r" % (r["name"][:22], r["loc"], r["v"]))
    for r in con.execute(
            "SELECT p.name, f.predicate, f.value, f.source_type FROM facts f "
            "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
            "JOIN persons p ON p.id=e.source_id "
            "WHERE f.valid_until IS NULL AND f.value LIKE ?", (like,)):
        found = True
        print("   %-22s fact.%-14s %-38r %s"
              % (r["name"][:22], r["predicate"], str(r["value"])[:38], r["source_type"]))
    if not found:
        print("   %-22s NOT FOUND IN WEAVE -- checking google" % frag)

print("\n=== searching GOOGLE organizations for the same fragments ===")
tok = G.get_access_token()
people, page = [], None
while True:
    q = {"personFields": "names,organizations", "pageSize": 1000,
         "sources": "READ_SOURCE_TYPE_CONTACT"}
    if page:
        q["pageToken"] = page
    rq = urllib.request.Request(
        "https://people.googleapis.com/v1/people/me/connections?" + urllib.parse.urlencode(q),
        headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(rq, timeout=60) as r:
        d = json.loads(r.read())
    people.extend(d.get("connections", []))
    page = d.get("nextPageToken")
    if not page:
        break
for frag in FRAGMENTS:
    for p in people:
        for o in (p.get("organizations") or []):
            blob = "%s %s %s" % (o.get("name") or "", o.get("title") or "",
                                 o.get("department") or "")
            if frag.lower() in blob.lower():
                print("   %-24s name=%-28r title=%r"
                      % ((p.get("names") or [{}])[0].get("displayName", "?")[:24],
                         o.get("name"), o.get("title")))

print("\n=== all distinct OCCUPATION values (weave), alphabetical ===")
occ = collections.Counter()
for r in con.execute(
        "SELECT occupation v FROM persons WHERE occupation IS NOT NULL "
        "AND occupation != '' UNION ALL SELECT f.value FROM facts f "
        "WHERE f.predicate='occupation' AND f.valid_until IS NULL"):
    occ[(r["v"] or "").strip()] += 1
print("  distinct occupation values: %d" % len(occ))
for v in sorted(occ, key=lambda s: s.lower())[:120]:
    print("     %r" % v[:88])
