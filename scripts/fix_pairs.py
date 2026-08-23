#!/usr/bin/env python3
"""Repair or clear the org/title pairs that are one string split in two.

Decided per contact rather than by rule: the pair rule reaches about 76%
precision, and clearing a real employer is worse than leaving a messy one. Each
entry below was read individually.

REPAIR -- the two halves name a real institution, so put it back together:
    Watt      + Heriot                       -> Heriot-Watt University
    Buffalo   + University                   -> University at Buffalo
    Austin    + Univ of Texas                -> University of Texas at Austin
    Carnegie  + Computer Interaction Institute
                                             -> Carnegie Mellon University,
                                                Human-Computer Interaction Institute
CLEAR -- the halves are a person's name, a stray word, or page text.
LEAVE -- real pairings the rule flagged only because my vocabulary lacks the
    word: RLH/'FP&A', Netflix/'Games', Vaisala/'Services',
    Pilehaveskolen/'Specialskoler', Kogeto/'Creative Management Professional',
    Independent Practice/'Registered Psychotherapist'.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
API = "https://people.googleapis.com/v1"

# name -> (new_org, new_title)  ; "" clears the field, None leaves it
REPAIR = {
    "Kim Appelquist":   ("Heriot-Watt University", ""),
    "Frosty Gross":     ("University at Buffalo", ""),
    "Theresa Votolato": ("University of Texas at Austin", ""),
    "Dan Saffer":       ("Carnegie Mellon University",
                         "Human-Computer Interaction Institute"),
}
CLEAR = {
    "Joe Ashear":            ("", ""),      # Franklin / Past Chiefs
    "Noreen Kukkonen":       ("", ""),      # Petteri / Juha -- two given names
    "Kiyana Badiee":         ("", ""),      # Age / Lisa Yoo
    "Michael Cobra":         (None, ""),    # COBRA is his firm; Mike Diaz is not his title
    "Danielle Snyder":       (None, ""),    # Machinify real; 'Molloy' is a name
    "Jairo Velez":           ("", ""),      # Stade / Peru
    "Peng Hong":             (None, ""),    # Autodesk real; 'Recently' is not a title
    "Joyce Cutts":           ("", ""),      # Retired / Recreation
    "Jamie Leach":           (None, ""),    # Marietta real; 'R Materials Management'
    "Kristy Bittles":        (None, ""),    # Cured real; 'Prospeo Key Contacts'
    "Siva Sabaretnam":       (None, ""),    # Meta real; 'Previously'
    "Taylor Umphenour":      ("", ""),      # See / Posts
    "Valerie Guevara-Grimes": ("", ""),     # CP / SHRM
    "Eli Altman":            (None, ""),    # OpenAI real; 'Trust Issues'
}
TYPO = {"Yufei Faye Liu": (None, "Internship")}   # 'Intership'

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
plans = []
for src, label in ((REPAIR, "repair"), (CLEAR, "clear"), (TYPO, "typo")):
    for name, (new_org, new_title) in src.items():
        r = con.execute("SELECT id, name, org, occupation, google_resource_name rn "
                        "FROM persons WHERE name = ?", (name,)).fetchone()
        if not r:
            print("  NOT FOUND: %s" % name)
            continue
        plans.append((dict(r), new_org, new_title, label))

print("  contacts to change: %d\n" % len(plans))
for r, no, nt, label in plans:
    print("   %-9s %-24s org %-26r -> %-28r" % (label, str(r["name"])[:24],
                                                str(r["org"])[:26],
                                                r["org"] if no is None else no))
    print("             %-24s title %-24r -> %r" % ("", str(r["occupation"])[:24],
                                                    r["occupation"] if nt is None else nt))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for r, no, nt, _l in plans:
        if no is not None:
            con.execute("UPDATE persons SET org=? WHERE id=?", (no, r["id"]))
        if nt is not None:
            con.execute("UPDATE persons SET occupation=? WHERE id=?", (nt, r["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  weave updated")

tok = G.get_access_token()
by_rn = {r["rn"]: (no, nt) for r, no, nt, _l in plans if r["rn"]}
people, page = [], None
while True:
    q = {"personFields": "names,organizations,metadata", "pageSize": 1000,
         "sources": "READ_SOURCE_TYPE_CONTACT"}
    if page:
        q["pageToken"] = page
    rq = urllib.request.Request(API + "/people/me/connections?" + urllib.parse.urlencode(q),
                                headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(rq, timeout=60) as rr:
        d = json.loads(rr.read())
    people.extend(d.get("connections", []))
    page = d.get("nextPageToken")
    if not page:
        break

gplans = []
for p in people:
    if p["resourceName"] not in by_rn:
        continue
    no, nt = by_rn[p["resourceName"]]
    o = {k: v for k, v in ((p.get("organizations") or [{}])[0]).items()
         if k != "metadata"}
    if no is not None:
        if no:
            o["name"] = no
        else:
            o.pop("name", None)
    if nt is not None:
        if nt:
            o["title"] = nt
        else:
            o.pop("title", None)
    orgs = [o] if any(str(o.get(k) or "").strip()
                      for k in ("name", "title", "department")) else []
    gplans.append((p, orgs))
print("  google contacts to update: %d" % len(gplans))
written = failed = 0
for i in range(0, len(gplans), 200):
    chunk = gplans[i:i + 200]
    contacts = {p["resourceName"]: {"etag": p.get("etag"), "organizations": o}
                for p, o in chunk}
    try:
        resp = G._api_post(API + "/people:batchUpdateContacts", tok,
                           {"contacts": contacts, "updateMask": "organizations",
                            "readMask": "organizations"}, timeout=120)
        for _rn, res in (resp.get("updateResult") or {}).items():
            if (res.get("status") or {}).get("code"):
                failed += 1
            else:
                written += 1
    except Exception as e:  # noqa: BLE001
        failed += len(chunk)
        print("  batch error: %s" % e)
    time.sleep(0.5)
print("  google updated=%d failed=%d" % (written, failed))

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "pair-repair-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now,
           "changes": [{"name": r["name"], "was_org": r["org"],
                        "was_title": r["occupation"], "now_org": no,
                        "now_title": nt, "kind": l} for r, no, nt, l in plans]},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
