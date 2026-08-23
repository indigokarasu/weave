#!/usr/bin/env python3
"""Merge the two Rachel Neurath google contacts into one, then the two weave rows.

  A  people/c98850802839146440   gmail, phone, gravatar/instagram/klout,
                                 partner=Jesse Lefkowitz,
                                 org 'Metropolitan' / title 'Rachel Neureth'  <- junk
  B  people/c2248142060781889228 linkedin, honorificSuffix 'PhD',
                                 org 'Berkeley Lab' / 'Postdoctoral Researcher'

A survives because it carries the reachable data (email, phone, relation) and the
richer weave row. B contributes its linkedin, its honorific suffix and its real
employer. A's organization is dropped rather than merged: a title that is the
contact's own name misspelled is the junk pattern already swept elsewhere.

B is then deleted. Google keeps deleted contacts recoverable for 30 days, and the
audit file holds B's full record either way.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402
from url_norm import canonical_url, dedupe_key  # noqa: E402

API = "https://people.googleapis.com/v1"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
A = "people/c98850802839146440"
B = "people/c2248142060781889228"
FIELDS = ("names,emailAddresses,phoneNumbers,organizations,addresses,urls,"
          "birthdays,relations,userDefined,events,biographies,nicknames")

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

tok = G.get_access_token()


def get(rn):
    rq = urllib.request.Request("%s/%s?personFields=%s" % (API, rn, FIELDS),
                                headers={"Authorization": "Bearer " + tok})
    return json.loads(urllib.request.urlopen(rq, timeout=30).read())


def strip(items):
    return [{k: v for k, v in i.items() if k != "metadata"} for i in (items or [])]


pa, pb = get(A), get(B)

# names: A's, plus B's honorific suffix
na = {k: v for k, v in (pa.get("names") or [{}])[0].items()
      if k not in ("metadata", "displayName", "displayNameLastFirst")}
nb = (pb.get("names") or [{}])[0]
if nb.get("honorificSuffix") and not na.get("honorificSuffix"):
    na["honorificSuffix"] = nb["honorificSuffix"]

# urls: union, canonical, deduped
urls, seen = [], set()
for src in (strip(pa.get("urls")), strip(pb.get("urls"))):
    for u in src:
        c = canonical_url(u.get("value"))
        if not c or dedupe_key(c) in seen:
            continue
        seen.add(dedupe_key(c))
        e = dict(u)
        e["value"] = c
        urls.append(e)

# organizations: B's real employer; A's 'Metropolitan / Rachel Neureth' is junk
own = {w.lower() for w in (nb.get("givenName", "") + " "
                           + nb.get("familyName", "")).split() if w}


def org_is_junk(o):
    title = (o.get("title") or "").strip().lower()
    if not title:
        return False
    words = [w for w in title.replace(".", " ").split() if w]
    # a title made only of (near-)variants of the contact's own name
    return words and all(
        any(w[:4] == n[:4] for n in own) for w in words)


orgs = [o for o in strip(pb.get("organizations")) if o]
for o in strip(pa.get("organizations")):
    if org_is_junk(o):
        print("  dropping junk organization from A: %s" % json.dumps(o))
        continue
    if not any((x.get("name") or "").lower() == (o.get("name") or "").lower()
               for x in orgs):
        orgs.append(o)

body = {"names": [na], "urls": urls, "organizations": orgs}
mask = "names,urls,organizations"
print("  A will become:")
for k, v in body.items():
    print("     %-16s %s" % (k, json.dumps(v, ensure_ascii=False)[:170]))
print("  B to delete: %s (%r)" % (B, nb.get("displayName")))

# nothing B holds may vanish
b_urls = {dedupe_key(u.get("value")) for u in strip(pb.get("urls"))}
kept = {dedupe_key(u.get("value")) for u in urls}
missing = {k for k in b_urls if k} - kept
a_urls = {dedupe_key(u.get("value")) for u in strip(pa.get("urls"))}
missing |= {k for k in a_urls if k} - kept
print("  urls that would be lost: %s (want none)" % (missing or "none"))
if missing:
    raise SystemExit("refusing: the merge would drop a url")

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

ts = datetime.now().strftime("%Y%m%dT%H%M%S")
os.makedirs(AUDIT_DIR, exist_ok=True)
audit = os.path.join(AUDIT_DIR, "rachel-neurath-merge-%s.json" % ts)
json.dump({"run_at": ts, "kept": A, "deleted": B,
           "A_before": pa, "B_before": pb, "A_after_body": body},
          open(audit, "w"), indent=1)

resp = G._api_post("%s/people:batchUpdateContacts" % API, tok,
                   {"contacts": {A: dict(body, etag=pa["etag"])},
                    "updateMask": mask, "readMask": mask}, timeout=60)
ok = all(not (r.get("status") or {}).get("code")
         for r in (resp.get("updateResult") or {}).values())
print("  A updated: %s" % ok)
if not ok:
    raise SystemExit("A did not update; B left alone")

req = urllib.request.Request("%s/%s:deleteContact" % (API, B), method="DELETE",
                             headers={"Authorization": "Bearer " + tok})
with urllib.request.urlopen(req, timeout=30) as r:
    print("  B deleted: HTTP %s (recoverable from google contacts trash for 30 days)"
          % r.status)

after = get(A)
print("\n  VERIFY A now:")
for k in ("names", "emailAddresses", "phoneNumbers", "urls", "organizations", "relations"):
    print("     %-16s %s" % (k, json.dumps(strip(after.get(k)), ensure_ascii=False)[:170]))
print("  audit: %s" % audit)
