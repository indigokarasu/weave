#!/usr/bin/env python3
"""Re-canonicalise url facts and collapse duplicates of the same link.

Two defects, both visible on one contact's record:

  1. Four urls ending in '}' -- github, medium, x and dribbble -- because they
     were parsed out of a JSON blob and kept its closing brace. url_norm's
     trailing-junk set had ']' and ')' but not '}', so canonical_url passed them
     through and the sync pushed them to Google in that form.

  2. The same link recorded twice under two predicates: 'about.me/munaf' as both
     profile_aboutme and website, 'youtube.com/user/davcron' as both
     profile_youtube and profile_website. A generic predicate and a specific one
     both claimed it, so Google receives the link twice.

For duplicates the SPECIFIC predicate wins -- profile_github says what the link
is, profile_website and website do not. Losers are retired with valid_until
rather than deleted, which is how this store withdraws a statement.
"""
import argparse, collections, json, os, re, sqlite3, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from url_norm import canonical_url, dedupe_key

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
AUDIT = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")
GENERIC = {"website", "profile_website"}

ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
a = ap.parse_args()
con = sqlite3.connect(DB, timeout=60); con.row_factory = sqlite3.Row

rows = [dict(r) for r in con.execute(
    "SELECT f.id,f.predicate,f.value,f.record_time,e.source_id pid,p.name pname "
    "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id WHERE f.valid_until IS NULL "
    "AND (f.predicate LIKE 'profile_%' OR f.predicate IN ('website','linkedin'))")]

recanon = []
for r in rows:
    c = canonical_url(r["value"])
    if c and c != r["value"]:
        recanon.append((r, c))

groups = collections.defaultdict(list)
for r in rows:
    k = dedupe_key(canonical_url(r["value"]) or r["value"])
    if k:
        groups[(r["pid"], k)].append(r)

retire = []
for (_pid, _k), v in groups.items():
    if len(v) < 2:
        continue
    # keep the most specific predicate; tie-break on the oldest record
    v.sort(key=lambda r: (r["predicate"] in GENERIC, str(r["record_time"])))
    retire.extend(v[1:])

print("  url facts examined            : %d" % len(rows))
print("  values that re-canonicalise   : %d" % len(recanon))
for r, c in recanon[:8]:
    print("     %-20s %-42r -> %r" % (str(r["pname"])[:20], str(r["value"])[:42], c))
print("  duplicate facts to retire     : %d across %d contacts"
      % (len(retire), len({r["pid"] for r in retire})))
for r in retire[:8]:
    print("     %-20s %-16s %r" % (str(r["pname"])[:20], r["predicate"], str(r["value"])[:44]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r, c in recanon:
        con.execute("UPDATE facts SET value=? WHERE id=?", (c, r["id"]))
    for r in retire:
        con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK"); raise
print("\n  rewrote %d values, retired %d duplicates" % (len(recanon), len(retire)))
os.makedirs(AUDIT, exist_ok=True)
p = os.path.join(AUDIT, "url-repair-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "recanonicalised": [{"name": r["pname"], "from": r["value"], "to": c}
                               for r, c in recanon],
           "retired_duplicates": [{"name": r["pname"], "predicate": r["predicate"],
                                   "value": r["value"]} for r in retire]},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
