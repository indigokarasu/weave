#!/usr/bin/env python3
"""Undo the duplicate Laith Ulaby contact, and stop the check that let it through.

The create path skips a weave row when its name matches a google displayName. It
compared them with _norm_name, which folds accents and punctuation but keeps
honorific suffixes: 'Laith Ulaby, Ph.D.' keys to 'LAITH ULABY PH D'. That matched
google fine until the weave name hygiene sweep renamed the row to 'Laith Ulaby'
-- google's displayName still renders WITH the suffix ('Laith Ulaby, Ph.D.'), the
keys diverged, and the next sync created a second contact for a person who
already had one.

So: make the dedupe key suffix-insensitive, delete the contact that was created,
and merge the two weave rows.
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402

API = "https://people.googleapis.com/v1"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
DUP_RN = "people/c8720545342291933692"      # created by mistake
KEEP_RN = "people/c2013140120704559373"     # the original
WEAVE_KEEP = "4f606101"
WEAVE_DUP = "92f4d9ea"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

# ---- 1. the check
P = "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts/google_sync.py"
s = open(P).read()
old = '''    s = _ud.normalize("NFKD", s)
    s = "".join(c for c in s if not _ud.combining(c))
    return " ".join(_re.sub(r"[^A-Za-z ]", " ", s).upper().split())'''
new = '''    s = _ud.normalize("NFKD", s)
    s = "".join(c for c in s if not _ud.combining(c))
    # Drop honorific suffixes. Google renders displayName WITH the suffix while
    # weave stores it in honorific_suffixes, so keeping it here made the same
    # person key two different ways and a duplicate contact got created.
    s = _re.sub(r"[,\\s]+(?:Ph\\.?\\s?D\\.?|M\\.?D\\.?|MBA|J\\.?D\\.?|Esq\\.?|Jr\\.?|Sr\\.?"
                r"|II|III|IV|DDS|DVM|RN|CPA|PE|MSc|MS|MA|BA|BS|M\\.?HCI|MPH|MFA"
                r"|EdD|PsyD|DPhil|FAIA|AIA)\\.?\\s*$", "", s, flags=_re.I)
    return " ".join(_re.sub(r"[^A-Za-z ]", " ", s).upper().split())'''
assert s.count(old) == 1, "norm anchor %d" % s.count(old)
if a.apply:
    open(P, "w").write(s.replace(old, new))
print("  _norm_name suffix-insensitive: %s" % ("applied" if a.apply else "ready"))

tok = G.get_access_token()


def get(rn):
    rq = urllib.request.Request("%s/%s?personFields=names,emailAddresses,"
                                "phoneNumbers,organizations,urls" % (API, rn),
                                headers={"Authorization": "Bearer " + tok})
    return json.loads(urllib.request.urlopen(rq, timeout=30).read())


dup, keep = get(DUP_RN), get(KEEP_RN)
print("\n  to delete : %s  %r org=%s urls=%d emails=%s"
      % (DUP_RN[-22:], (dup.get("names") or [{}])[0].get("displayName"),
         [o.get("name") for o in (dup.get("organizations") or [])],
         len(dup.get("urls") or []),
         [e.get("value") for e in (dup.get("emailAddresses") or [])]))
print("  to keep   : %s  %r org=%s urls=%d emails=%s"
      % (KEEP_RN[-22:], (keep.get("names") or [{}])[0].get("displayName"),
         [o.get("name") for o in (keep.get("organizations") or [])],
         len(keep.get("urls") or []),
         [e.get("value") for e in (keep.get("emailAddresses") or [])]))

# does the duplicate hold anything the original lacks?
keep_orgs = {(o.get("name") or "").lower() for o in (keep.get("organizations") or [])}
lost = [o for o in (dup.get("organizations") or [])
        if (o.get("name") or "").lower() not in keep_orgs]
print("  data unique to the duplicate: %s" % (lost or "none"))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

ts = datetime.now().strftime("%Y%m%dT%H%M%S")
os.makedirs(AUDIT_DIR, exist_ok=True)
json.dump({"run_at": ts, "deleted": DUP_RN, "kept": KEEP_RN,
           "deleted_record": dup, "why": "duplicate created by the sync after a "
                                         "weave rename broke the name dedupe key"},
          open(os.path.join(AUDIT_DIR, "laith-duplicate-%s.json" % ts), "w"),
          indent=1)

req = urllib.request.Request("%s/%s:deleteContact" % (API, DUP_RN), method="DELETE",
                             headers={"Authorization": "Bearer " + tok})
with urllib.request.urlopen(req, timeout=30) as r:
    print("  duplicate deleted: HTTP %s" % r.status)

con = sqlite3.connect("/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite",
                      timeout=60)
con.row_factory = sqlite3.Row
ids = {r["id"][:8]: r["id"] for r in con.execute(
    "SELECT id FROM persons WHERE LOWER(name) LIKE '%ulaby%'")}
con.close()
print("  weave rows: %s" % list(ids))
print("  now merge: winner=%s loser=%s" % (ids.get(WEAVE_KEEP), ids.get(WEAVE_DUP)))
