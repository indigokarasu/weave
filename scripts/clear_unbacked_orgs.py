#!/usr/bin/env python3
"""Employers on the visible record that nothing in the system ever claimed.

This is deliberately NARROWER than "uncorroborated". Earlier in this cleanup I
refused to bulk-delete uncorroborated employers, and that still stands: Berkeley
Lab, IDEO and Cruise Automation all fail corroboration and are almost certainly
true. Deleting those to be rid of the wrong ones repeats the Heriot-Watt mistake
in the other direction.

The class here is different and provable from the data alone. To qualify, an org
on persons.org must be ALL of:

  1. absent from the graph -- not one org fact for that contact, live or retired,
     holds this value. No pipeline run, no import, no user edit ever recorded a
     statement that this person works there. The value has no author.
  2. uncorroborated -- the contact's own email domain, profile URLs and other
     facts say nothing about it.
  3. not the owner's own -- it differs from what Google held before this
     pipeline started writing, so it is not something Jared typed.

'Indigotelecomgroup' on Indigo Karasu is the type case: no fact anywhere claims
it, a gmail address cannot support it, and it is a plain namesake match on the
contact's name -- the same failure that attributed Heriot-Watt to a Bay Area PM.
Where a contact has a live org FACT that the gate accepts, that value is offered
as a replacement rather than leaving the field empty.

Nothing is invented: a record either gets a corroborated value that already
exists in its own graph, or it gets cleared.
"""
import argparse, collections, glob, json, os, sqlite3, sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
from employer_gate import corroborate

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60); con.row_factory = sqlite3.Row

snap = json.load(open(sorted(glob.glob("/root/google-contacts-snapshot-*.json"))[0]))
owner = {}
for p in snap:
    o = (p.get("organizations") or [{}])[0]
    owner[p["resourceName"]] = (o.get("name") or "").strip().lower()

facts = collections.defaultdict(list)
orgfacts = collections.defaultdict(list)
for r in con.execute("SELECT e.source_id pid, f.predicate, f.value, f.valid_until "
                     "FROM facts f JOIN edges e ON e.target_id=f.id "
                     "AND e.rel_type='HasFact'"):
    v = str(r["value"] or "")
    if r["valid_until"] is None:
        facts[r["pid"]].append(v)
    if r["predicate"] == "org":
        orgfacts[r["pid"]].append((v, r["valid_until"] is None))

clear, replace, kept = [], [], 0
for p in con.execute("SELECT id,name,org,email,google_resource_name rn FROM persons "
                     "WHERE TRIM(COALESCE(org,''))<>''"):
    org = (p["org"] or "").strip()
    if owner.get(p["rn"] or "") == org.lower():
        kept += 1; continue                      # the owner's own value
    ever = {v.strip().lower() for v, _live in orgfacts.get(p["id"], [])}
    if org.lower() in ever:
        kept += 1; continue                      # something did claim it
    vals = facts.get(p["id"], [])
    urls = [v for v in vals if "://" in v]
    other = [v for v in vals if v not in urls]
    emails = [p["email"] or ""] + [v for v in other if "@" in v and " " not in v.strip()]
    if any(corroborate(org, em, urls, other) for em in emails):
        kept += 1; continue                      # corroborated after all
    # is there a LIVE org fact the gate accepts? then use it instead of clearing
    better = None
    for v, live in orgfacts.get(p["id"], []):
        if not live: continue
        if any(corroborate(v, em, urls, other) for em in emails):
            better = v; break
    (replace if better else clear).append((dict(p), better))

print("  orgs on the record                          : %d" % (len(clear)+len(replace)+kept))
print("  left alone (owner's own, claimed, or corroborated): %d" % kept)
print("  no fact ever claimed it, uncorroborated      : %d" % (len(clear)+len(replace)))
print("     -> replaced by a corroborated fact they already have : %d" % len(replace))
print("     -> cleared                                           : %d" % len(clear))
print("\n  replacements:")
for p, b in replace[:10]:
    print("     %-24s %-26r -> %r" % (str(p["name"])[:24], str(p["org"])[:26], b))
print("\n  to be cleared (sample):")
for p, _b in clear[:20]:
    print("     %-24s %-30r email=%s" % (str(p["name"])[:24], str(p["org"])[:30],
                                         (p["email"] or "-")[:26]))
if not a.apply:
    print("\ndry run; pass --apply to write")
