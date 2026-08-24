#!/usr/bin/env python3
"""Move titles, honorifics and pronouns out of weave's name fields.

persons has honorific_prefixes, honorific_suffixes and pronouns columns, and they
are empty on every row while the data sits inside `name` and `name_family`:

    name_family = 'Neurath, PhD'
    name_family = 'Boddicker, M.HCI (she/her)'

`name` is weave's display form and google computes its displayName WITHOUT the
suffix ('Rachel Neurath', suffix 'PhD' held separately), so weave matches that.

Order matters: pronouns are stripped first. 'Boddicker, M.HCI (she/her)' hides
the degree behind the pronouns, so a suffix-first pass sees nothing to move.

Prefixes use a deliberately short list -- Dr/Mr/Mrs/Ms/Mx/Prof/Rev. Bishop, Chief,
Judge and Coach are also ordinary surnames; matching those would rename Anna
Bishop Rehrig.
"""
import argparse
import json
import os
import re
import sqlite3
from datetime import datetime

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")

SUFFIX = re.compile(
    r"[,\s]+((?:Ph\.?\s?D\.?|M\.?D\.?|M\.?B\.?A\.?|J\.?D\.?|Esq\.?|Jr\.?|Sr\.?|"
    r"I{2,3}|IV|D\.?D\.?S\.?|D\.?V\.?M\.?|R\.?N\.?|C\.?P\.?A\.?|P\.?E\.?|"
    r"M\.?Sc\.?|M\.?S\.?|M\.?A\.?|B\.?A\.?|B\.?S\.?|M\.?HCI|LCSW|LMFT|PMP|"
    r"MPH|MFA|EdD|PsyD|DPhil|FAIA|AIA))\s*$", re.I)
PREFIX = re.compile(r"^\s*((?:Dr|Mr|Mrs|Ms|Mx|Prof|Professor|Rev|Revd)\.?)\s+(?=\S)", re.I)
PRONOUNS = re.compile(r"\s*\(\s*((?:she|he|they|ze|xe)\s*/\s*[^)]*)\)\s*", re.I)


def split(value):
    """-> (clean, [suffixes], [prefixes], pronouns)"""
    s = (value or "").strip()
    pron = ""
    m = PRONOUNS.search(s)
    if m:
        pron = m.group(1).strip()
        s = PRONOUNS.sub(" ", s).strip()
    pres = []
    while True:
        m = PREFIX.match(s)
        if not m:
            break
        pres.append(m.group(1).strip())
        s = s[m.end():].strip()
    sufs = []
    while True:
        m = SUFFIX.search(s)
        if not m:
            break
        sufs.insert(0, m.group(1).strip())
        s = s[:m.start()].strip().rstrip(",").strip()
    return re.sub(r"\s{2,}", " ", s).strip(" ,"), sufs, pres, pron


ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
plans = []
for r in con.execute("SELECT id, name, name_given, name_family, pronouns, "
                     "honorific_prefixes, honorific_suffixes FROM persons"):
    upd, sufs, pres, pron = {}, [], [], ""
    for f in ("name", "name_given", "name_family"):
        clean, s2, p2, pr2 = split(r[f])
        if clean != (r[f] or "").strip():
            if not clean:
                continue          # never blank a name field
            upd[f] = clean
        sufs += [x for x in s2 if x not in sufs]
        pres += [x for x in p2 if x not in pres]
        pron = pron or pr2
    if not upd and not sufs and not pres and not pron:
        continue
    if sufs and not (r["honorific_suffixes"] or "").strip("[] "):
        upd["honorific_suffixes"] = json.dumps(sufs)
    if pres and not (r["honorific_prefixes"] or "").strip("[] "):
        upd["honorific_prefixes"] = json.dumps(pres)
    if pron and not (r["pronouns"] or "").strip():
        upd["pronouns"] = pron
    if upd:
        plans.append((dict(r), upd))

print("  rows to fix: %d" % len(plans))
for before, upd in plans:
    print("     %-34s" % (before["name"] or "")[:34])
    for k, v in upd.items():
        print("        %-20s %r -> %r" % (k, before.get(k), v))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

ts = datetime.now().strftime("%Y%m%dT%H%M%S")
os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "weave-name-hygiene-%s.json" % ts)
json.dump([{"before": b, "updates": u} for b, u in plans], open(p, "w"),
          indent=1, default=str)

con.execute("BEGIN IMMEDIATE")
try:
    for before, upd in plans:
        sets = ", ".join("%s=?" % k for k in upd)
        con.execute("UPDATE persons SET %s WHERE id=?" % sets,
                    list(upd.values()) + [before["id"]])
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  applied; audit %s" % p)

print("\n  VERIFY:")
for r in con.execute("SELECT name, name_given, name_family, pronouns, "
                     "honorific_suffixes, honorific_prefixes FROM persons "
                     "WHERE honorific_suffixes IS NOT NULL "
                     "AND honorific_suffixes NOT IN ('', '[]')"):
    print("     %-26s given=%-12r family=%-14r suffix=%-12s pronouns=%r"
          % (r["name"][:26], r["name_given"], r["name_family"],
             r["honorific_suffixes"], r["pronouns"]))
