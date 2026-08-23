#!/usr/bin/env python3
"""Merge the remaining duplicate GOOGLE contacts, the way Rachel Neurath was done.

  Ricardo Prada       both at Google
  Jenni Katajamaki    accent variant of one distinctive Finnish name, both at
                      architecture practices
  Ljubica Lu Chatman  one distinctive name; the two records are different job
                      eras (Google, then Meta)

The survivor is the record carrying the reachable data (email/phone). The other
contributes anything unique -- urls, employers, honorific suffix -- and is then
deleted; google keeps deleted contacts recoverable for 30 days and the audit
holds the full record.

Employers are UNIONED rather than replaced: two entries are a career, not a
conflict, and google models organizations as a list.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402
from url_norm import canonical_url, dedupe_key  # noqa: E402

API = "https://people.googleapis.com/v1"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
FIELDS = ("names,emailAddresses,phoneNumbers,organizations,addresses,urls,"
          "birthdays,relations,userDefined,events,biographies,nicknames")

# (keep, delete) -- keep is the one with email/phone
PAIRS = [
    ("people/c3347190798555787617", "people/c6993346889346831539"),   # Ricardo Prada
    ("people/c8805887987349952389", "people/c1152363427501788630"),   # Jenni Katajamaki
    ("people/c3510657638259566146", "people/c9222435109289537225"),   # Ljubica Lu Chatman
]

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


plans = []
for keep_rn, del_rn in PAIRS:
    A, B = get(keep_rn), get(del_rn)
    na = {k: v for k, v in (A.get("names") or [{}])[0].items()
          if k not in ("metadata", "displayName", "displayNameLastFirst",
                       "unstructuredName")}
    nb = (B.get("names") or [{}])[0]
    for f in ("honorificSuffix", "honorificPrefix", "middleName"):
        if nb.get(f) and not na.get(f):
            na[f] = nb[f]
    # Keep the spelling that carries diacritics. Folding accents is for MATCHING;
    # the record itself should hold the real name (Katajamäki, not Katajamaki).
    import unicodedata as _ud

    def _folded(x):
        return "".join(c for c in _ud.normalize("NFKD", x or "")
                       if not _ud.combining(c))

    def _accents(x):
        return sum(1 for c in _ud.normalize("NFKD", x or "") if _ud.combining(c))

    for f in ("givenName", "familyName", "middleName"):
        av, bv = na.get(f) or "", nb.get(f) or ""
        if av and bv and _folded(av) == _folded(bv) and _accents(bv) > _accents(av):
            print("     keeping accented spelling for %s: %r over %r" % (f, bv, av))
            na[f] = bv

    urls, seen = [], set()
    for src in (strip(A.get("urls")), strip(B.get("urls"))):
        for u in src:
            c = canonical_url(u.get("value"))
            if not c or dedupe_key(c) in seen:
                continue
            seen.add(dedupe_key(c))
            e = dict(u)
            e["value"] = c
            urls.append(e)

    orgs = strip(A.get("organizations"))
    for o in strip(B.get("organizations")):
        if not any((x.get("name") or "").strip().lower()
                   == (o.get("name") or "").strip().lower() for x in orgs):
            orgs.append(o)

    emails, ekeys = strip(A.get("emailAddresses")), set()
    ekeys = {(e.get("value") or "").lower() for e in emails}
    for e in strip(B.get("emailAddresses")):
        if (e.get("value") or "").lower() not in ekeys:
            emails.append(e)
            ekeys.add((e.get("value") or "").lower())

    phones = strip(A.get("phoneNumbers"))
    pkeys = {"".join(ch for ch in (p.get("value") or "") if ch.isdigit()) for p in phones}
    for p in strip(B.get("phoneNumbers")):
        d = "".join(ch for ch in (p.get("value") or "") if ch.isdigit())
        if d and d not in pkeys:
            phones.append(p)
            pkeys.add(d)

    body = {"names": [na], "urls": urls, "organizations": orgs,
            "emailAddresses": emails, "phoneNumbers": phones}
    body = {k: v for k, v in body.items() if v}
    lost = ({dedupe_key(u.get("value")) for u in strip(B.get("urls"))} |
            {dedupe_key(u.get("value")) for u in strip(A.get("urls"))}) - \
           {dedupe_key(u.get("value")) for u in urls}
    lost.discard(None)
    plans.append((keep_rn, del_rn, A, B, body, lost))

    print("  %-26s keep %s  delete %s"
          % ((A.get("names") or [{}])[0].get("displayName"), keep_rn[-14:], del_rn[-14:]))
    print("     merged names : %s" % json.dumps(na, ensure_ascii=False)[:120])
    print("     orgs         : %s" % json.dumps(orgs, ensure_ascii=False)[:130])
    print("     urls         : %d   emails: %d   phones: %d"
          % (len(urls), len(emails), len(phones)))
    print("     urls lost    : %s" % (lost or "none"))

if any(l for _k, _d, _A, _B, _b, l in plans):
    raise SystemExit("refusing: a merge would drop a url")
if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

ts = datetime.now().strftime("%Y%m%dT%H%M%S")
os.makedirs(AUDIT_DIR, exist_ok=True)
json.dump([{"keep": k, "deleted": d, "keep_before": A, "deleted_record": B,
            "merged_body": b} for k, d, A, B, b, _l in plans],
          open(os.path.join(AUDIT_DIR, "google-dupe-merge-%s.json" % ts), "w"),
          indent=1)

for keep_rn, del_rn, A, _B, body, _l in plans:
    mask = ",".join(body.keys())
    r = G._api_post("%s/people:batchUpdateContacts" % API, tok,
                    {"contacts": {keep_rn: dict(body, etag=A["etag"])},
                     "updateMask": mask, "readMask": mask}, timeout=60)
    ok = all(not (x.get("status") or {}).get("code")
             for x in (r.get("updateResult") or {}).values())
    print("  %s updated: %s" % (keep_rn[-14:], ok))
    if not ok:
        print("     skipping delete of %s" % del_rn)
        continue
    req = urllib.request.Request("%s/%s:deleteContact" % (API, del_rn), method="DELETE",
                                 headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as resp:
        print("  %s deleted: HTTP %s" % (del_rn[-14:], resp.status))
    time.sleep(0.5)
print("  audit written to %s" % AUDIT_DIR)
