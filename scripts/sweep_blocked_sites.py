#!/usr/bin/env python3
import os
"""Stop serving facts sourced from sites that give no information.

Driven by the measured verdicts in the probe cache rather than a hand-written
list: any host whose entry says answers_for_any is true returned identical visible
text for a handle nobody owns, so a profile recorded there attributes nothing.

Marks valid_until (consumers filter valid_until IS NULL); does not delete. Audit
written for exact reversal. Pass --apply to write.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
CACHE = os.path.join(os.environ.get("AGENT_ROOT", os.path.join(os.path.expanduser("~"), ".hermes")), "commons/data/ocas-scout/soft404-sites.json")
AUDIT = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave/quarantine-template-sites.json")
APPLY = "--apply" in sys.argv

verdicts = json.load(open(CACHE))
bad_hosts = sorted(h for h, v in verdicts.items() if v.get("answers_for_any"))
print("hosts that answer for any handle (measured): %s" % ", ".join(bad_hosts))
if not bad_hosts:
    sys.exit(0)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

rows = con.execute("""
SELECT f.id, f.predicate, f.value, f.source_ref, f.source_type, p.name
FROM facts f
JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
JOIN persons p ON p.id = e.source_id
WHERE f.source_type LIKE 'scout%' AND f.valid_until IS NULL
""").fetchall()


def host_of(s):
    s = (s or "").lower()
    for pre in ("https://", "http://"):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.split("/")[0].replace("www.", "")


# A fact whose evidence lies OUTSIDE the answers-for-any host is not what this
# sweep is for. The sweep exists because such a host serves identical text to a
# fetcher, so "the profile page loaded" proves nothing. That reasoning does not
# reach a profile tied to the contact by a page they own (first_party) or by
# indexed result text naming them and their employer (context) -- neither reads
# the host at all. A slug tie IS the slug resembling a name, which is the
# namesake failure this pipeline refuses, so it stays sweepable.
INDEPENDENT_TIE = ("scout_linkedin_first_party", "scout_linkedin_context")

hit = []
for r in rows:
    if r["source_type"] in INDEPENDENT_TIE:
        continue
    h_ref = host_of(r["source_ref"])
    h_val = host_of(r["value"])
    if any(b in h_ref or b in h_val for b in bad_hosts):
        hit.append(r)

print("facts to invalidate : %d across %d people"
      % (len(hit), len({r["name"] for r in hit})))
preds = {}
for r in hit:
    preds[r["predicate"]] = preds.get(r["predicate"], 0) + 1
for k, v in sorted(preds.items(), key=lambda kv: -kv[1])[:12]:
    print("  %-22s %d" % (k, v))

if not APPLY:
    print("\ndry run — pass --apply to write")
    sys.exit(0)

stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
json.dump({
    "quarantined_at": stamp,
    "hosts": bad_hosts,
    "reason": ("the site returns identical visible text for a handle that cannot "
               "exist, so a profile recorded there attributes nothing"),
    "revert": "UPDATE facts SET valid_until = NULL WHERE id IN (<ids below>)",
    "facts": [{"id": r["id"], "name": r["name"], "predicate": r["predicate"],
               "value": r["value"], "source_ref": r["source_ref"]} for r in hit],
}, open(AUDIT, "w"), indent=1)

cur = con.cursor()
cur.execute("BEGIN IMMEDIATE")
try:
    cur.executemany("UPDATE facts SET valid_until = ? WHERE id = ? AND valid_until IS NULL",
                    [(stamp, r["id"]) for r in hit])
    con.commit()
except Exception:
    con.rollback()
    raise

still = con.execute(
    "SELECT COUNT(*) c FROM facts WHERE id IN (%s) AND valid_until IS NULL"
    % ",".join("?" * len(hit)), [r["id"] for r in hit]).fetchone()["c"] if hit else 0
print("\ninvalidated %d facts; still valid among them: %d (expect 0)" % (len(hit), still))
print("audit: %s" % AUDIT)
