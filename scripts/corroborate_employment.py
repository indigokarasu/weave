#!/usr/bin/env python3
"""Which enrichment-added employers are corroborated by anything?

'Heriot-Watt University' was not junk-shaped -- it read as a fine employer and
was simply false. No string rule catches that. What can be checked is whether
anything ELSE about the contact agrees with it:

    katie@pictalhealth.com -> 'Pictal Health'   the email domain says so
    Ankita Akerkar         -> 'Google'          she has a developers.google.com
                                                profile
    Kim Appelquist         -> 'Heriot-Watt'     nothing at all

So each enrichment-added org is tested against the contact's own email domain,
their profile URL hosts, and their other facts. An employer no other signal
supports is a guess.

Scope: only orgs the owner did NOT already have. What was in Google before this
pipeline ran is the owner's own record and is left alone.
"""
import collections
import glob
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
from url_norm import canonical_url

con = sqlite3.connect("/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
con.row_factory = sqlite3.Row

snap = json.load(open(sorted(glob.glob("/root/google-contacts-snapshot-*.json"))[0]))
owner_org = {}
for p in snap:
    o = (p.get("organizations") or [{}])[0]
    owner_org[p["resourceName"]] = (o.get("name") or "").strip().lower()


def flat(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


rows = [dict(r) for r in con.execute(
    "SELECT id, name, org, occupation, email, google_resource_name rn FROM persons "
    "WHERE org IS NOT NULL AND org != ''")]

# everything else we know about each contact, as one searchable blob
extra = collections.defaultdict(list)
for r in con.execute(
        "SELECT e.source_id pid, f.predicate, f.value FROM facts f "
        "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
        "WHERE f.valid_until IS NULL"):
    extra[r["pid"]].append(str(r["value"]))

uncorroborated, corroborated, owner_held = [], [], 0
for r in rows:
    o = (r["org"] or "").strip()
    if owner_org.get(r["rn"] or "") == o.lower():
        owner_held += 1
        continue                     # the owner's own value: not ours to judge
    key = flat(o)
    if len(key) < 3:
        uncorroborated.append((r, "too short to corroborate"))
        continue
    why = None
    dom = flat((r["email"] or "").split("@")[-1])
    if dom and (key in dom or dom.startswith(key)):
        why = "email domain"
    if not why:
        for v in extra.get(r["id"], []):
            c = canonical_url(v) if "//" in str(v) or "." in str(v) else None
            host = flat(c.split("//")[-1].split("/")[0]) if c else ""
            if host and (key in host):
                why = "profile url host"
                break
            if key and key in flat(v) and str(v).strip().lower() != o.lower():
                why = "mentioned in another fact"
                break
    if why:
        corroborated.append((r, why))
    else:
        uncorroborated.append((r, "nothing else mentions it"))

print("  orgs on contact records            : %d" % len(rows))
print("  the owner's own value (left alone) : %d" % owner_held)
print("  enrichment-added AND corroborated  : %d" % len(corroborated))
print("  enrichment-added, NOTHING agrees   : %d" % len(uncorroborated))
print("\n  corroborated examples:")
for r, why in corroborated[:10]:
    print("     %-24s %-28r <- %s" % (str(r["name"])[:24], str(r["org"])[:28], why))
print("\n  uncorroborated (a guess with nothing behind it):")
for r, why in uncorroborated[:40]:
    print("     %-24s org=%-28r title=%r"
          % (str(r["name"])[:24], str(r["org"])[:28], str(r["occupation"])[:26]))
