#!/usr/bin/env python3
"""Sweep the whole store for data-quality defects, many classes at once.

Finding one defect class per enrichment round is slow. This checks every class
seen so far plus the ones they suggest, over all contacts, and prints counts with
examples so each can be judged.
"""
import collections
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
from url_norm import canonical_url, dedupe_key
from url_quality import is_person_profile, handle_is_opaque

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
names = [r[0] for r in con.execute(
    "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")]
persons = {r["id"]: dict(r) for r in con.execute("SELECT * FROM persons")}
facts = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, f.confidence, e.source_id pid "
    "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "WHERE f.valid_until IS NULL").fetchall()

findings = collections.OrderedDict()


def add(key, rows, sample=6):
    if rows:
        findings[key] = rows
        print("  %-52s %d" % (key, len(rows)))
        for r in rows[:sample]:
            print("       %s" % r)


print("DATA-QUALITY AUDIT")
print("=" * 76)

# 1. urls that would not pass the gate today
bad_url = []
for f in facts:
    p = str(f["predicate"])
    if not (p.startswith("profile_") or p in ("website", "linkedin")):
        continue
    per = persons.get(f["pid"]) or {}
    plat = p[8:] if p.startswith("profile_") else None
    if not is_person_profile(f["value"], per.get("name") or "", plat, names,
                             org=per.get("org") or "", email=per.get("email") or ""):
        bad_url.append("%-22s %-18s %s" % ((per.get("name") or "?")[:22], p,
                                           str(f["value"])[:44]))
add("url facts the gate would reject today", bad_url)

# 2. non-canonical urls
nc = ["%-22s %s" % ((persons.get(f["pid"], {}).get("name") or "?")[:22], f["value"])
      for f in facts
      if (str(f["predicate"]).startswith("profile_")
          or f["predicate"] in ("website", "linkedin"))
      and canonical_url(f["value"]) != f["value"]]
add("non-canonical url facts", nc)

# 3. same url under several predicates for one person
per_url = collections.defaultdict(lambda: collections.defaultdict(set))
for f in facts:
    p = str(f["predicate"])
    if p.startswith("profile_") or p in ("website", "linkedin"):
        k = dedupe_key(f["value"])
        if k:
            per_url[f["pid"]][k].add(p)
multi = ["%-22s %-40s %s" % ((persons.get(pid, {}).get("name") or "?")[:22], k[:40],
                             sorted(preds))
         for pid, urls in per_url.items() for k, preds in urls.items() if len(preds) > 1]
add("one url held under several predicates", multi)

# 4. contradictory single-valued facts
SINGLE = ("location_city", "location_country", "org", "occupation", "birthday",
          "pronouns")
contra = []
grp = collections.defaultdict(lambda: collections.defaultdict(set))
for f in facts:
    if f["predicate"] in SINGLE:
        grp[f["pid"]][f["predicate"]].add(str(f["value"]).strip())
for pid, preds in grp.items():
    for p, vals in preds.items():
        if len(vals) > 1:
            contra.append("%-22s %-16s %s" % ((persons.get(pid, {}).get("name") or "?")[:22],
                                              p, sorted(vals)[:3]))
add("contradictory single-valued facts", contra)

# 5. facts whose value looks like a person's own name in a non-name field
ownname = []
for f in facts:
    if f["predicate"] not in ("org", "occupation", "bio_summary"):
        continue
    per = persons.get(f["pid"]) or {}
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", per.get("name") or "")}
    toks = re.findall(r"[A-Za-z]{3,}", str(f["value"]))
    if toks and own and all(t.lower() in own for t in toks):
        ownname.append("%-22s %-12s %r" % ((per.get("name") or "?")[:22],
                                           f["predicate"], str(f["value"])[:36]))
add("org/occupation fact that is the contact's own name", ownname)

# 6. emails that are clearly not personal
BAD_EMAIL = re.compile(r"^(no-?reply|do-?not-?reply|postmaster|mailer-daemon|"
                       r"bounce|notifications?)[.\-_]?\d*@", re.I)
bad_em = ["%-22s %s" % ((p.get("name") or "?")[:22], p.get("email"))
          for p in persons.values()
          if p.get("email") and BAD_EMAIL.match(p["email"])]
add("persons whose email is an automated sender", bad_em)

# 7. phone numbers that are not phone numbers
badphone = []
for p in persons.values():
    v = (p.get("phone") or "").strip()
    if v and len(re.sub(r"\D", "", v)) < 7:
        badphone.append("%-22s %r" % ((p.get("name") or "?")[:22], v))
add("persons with an implausible phone number", badphone)

# 8. bio_summary that is platform boilerplate
BOILER = re.compile(r"(sign up|log in|create an account|see photos|see instagram|"
                    r"view the profiles|join now|watch the latest|discover the|"
                    r"the latest posts|page couldn|not found|404)", re.I)
bios = ["%-22s %r" % ((persons.get(f["pid"], {}).get("name") or "?")[:22],
                      str(f["value"])[:56])
        for f in facts if f["predicate"] == "bio_summary" and BOILER.search(str(f["value"]))]
add("bio_summary that is platform boilerplate", bios)

# 9. account_on values that are not domains
badacct = ["%-22s %r" % ((persons.get(f["pid"], {}).get("name") or "?")[:22], f["value"])
           for f in facts if f["predicate"] == "account_on"
           and not re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", str(f["value"]).strip().lower())]
add("account_on values that are not a domain", badacct)

# 10. facts with empty or whitespace values
empt = ["%-22s %s" % ((persons.get(f["pid"], {}).get("name") or "?")[:22], f["predicate"])
        for f in facts if not str(f["value"] or "").strip()]
add("facts with an empty value", empt)

# 11. persons with no name
noname = ["id=%s email=%r" % (p["id"][:8], p.get("email"))
          for p in persons.values() if not (p.get("name") or "").strip()]
add("persons with no name", noname)

# 12. duplicate emails across different persons
by_email = collections.defaultdict(list)
for p in persons.values():
    e = (p.get("email") or "").strip().lower()
    if e:
        by_email[e].append(p.get("name") or "?")
dupem = ["%-34s %s" % (e, v) for e, v in by_email.items() if len(v) > 1]
add("one email address on several persons", dupem)

print("=" * 76)
print("  classes with findings: %d" % len(findings))
json.dump({k: v for k, v in findings.items()},
          open("/tmp/audit_quality.json", "w"), indent=1, default=str)
