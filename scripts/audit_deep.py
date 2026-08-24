#!/usr/bin/env python3
import os
"""Second-pass audit: classes the first sweep did not look for.

The first audit is down to three classes, two of which are correct as they
stand. That is not the same as the data being clean -- it means I have run out
of the checks I thought of first. These are the next ones.
"""
import collections
import json
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
con = sqlite3.connect(os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite"))
con.row_factory = sqlite3.Row
persons = {r["id"]: dict(r) for r in con.execute("SELECT * FROM persons")}
facts = con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, f.confidence, f.record_time, "
    "e.source_id pid FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "WHERE f.valid_until IS NULL").fetchall()
found = collections.OrderedDict()


def add(k, rows, n=6):
    if rows:
        found[k] = rows
        print("  %-54s %d" % (k, len(rows)))
        for r in rows[:n]:
            print("       %s" % r)


def nm(pid):
    return (persons.get(pid) or {}).get("name") or "?"


print("DEEP AUDIT")
print("=" * 78)

# confidence out of range
add("facts with confidence outside 0..1",
    ["%-22s %-16s conf=%s" % (nm(f["pid"]), f["predicate"], f["confidence"])
     for f in facts if f["confidence"] is not None
     and not (0.0 <= float(f["confidence"]) <= 1.0)])

# birthdays that cannot be right
now_y = datetime.now().year
bad_bd = []
for f in facts:
    if f["predicate"] != "birthday":
        continue
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(f["value"]).strip())
    if not m:
        continue
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y > now_y or y < 1900 or not (1 <= mo <= 12) or not (1 <= d <= 31):
        bad_bd.append("%-22s %r" % (nm(f["pid"]), f["value"]))
add("birthdays that are impossible", bad_bd)

# a person's own record referring to themselves
add("facts whose value is the contact's own full name",
    ["%-22s %-16s %r" % (nm(f["pid"]), f["predicate"], str(f["value"])[:34])
     for f in facts
     if f["predicate"] not in ("username", "bio_summary", "note")
     and re.sub(r"[^a-z]", "", str(f["value"]).lower())
     == re.sub(r"[^a-z]", "", nm(f["pid"]).lower())
     and len(str(f["value"]).strip()) > 3])

# org / occupation holding a URL or an email
add("org or occupation holding a url or email",
    ["%-22s %-12s %r" % (nm(f["pid"]), f["predicate"], str(f["value"])[:40])
     for f in facts if f["predicate"] in ("org", "occupation")
     and re.search(r"https?://|www\.|@[a-z0-9.-]+\.[a-z]{2,}", str(f["value"]), re.I)])

# a location in the occupation field, or a job in the location field
CITY_ISH = re.compile(r"^[A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z]{2}$")
add("occupation that looks like a place",
    ["%-22s %r" % (nm(f["pid"]), f["value"])
     for f in facts if f["predicate"] == "occupation"
     and CITY_ISH.match(str(f["value"]).strip())])

# phone numbers
badphone = []
for p in persons.values():
    v = (p.get("phone") or "").strip()
    if not v:
        continue
    digits = re.sub(r"\D", "", v)
    if len(digits) < 7 or len(digits) > 15:
        badphone.append("%-22s %r (%d digits)" % (p.get("name"), v, len(digits)))
add("phone numbers with an implausible digit count", badphone)

# emails that are not addresses
add("persons whose email is not a valid address",
    ["%-22s %r" % (p.get("name"), p.get("email")) for p in persons.values()
     if (p.get("email") or "").strip()
     and not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", p["email"].strip())])

# note facts that are enormous or empty
add("note facts over 2000 characters",
    ["%-22s %d chars" % (nm(f["pid"]), len(str(f["value"])))
     for f in facts if f["predicate"] == "note" and len(str(f["value"])) > 2000])

# account_on duplicates per person
acc = collections.defaultdict(list)
for f in facts:
    if f["predicate"] == "account_on":
        acc[f["pid"]].append(str(f["value"]).strip().lower())
add("contacts with duplicate account_on entries",
    ["%-22s %s" % (nm(pid), [v for v, c in collections.Counter(v).items() if c > 1])
     for pid, v in acc.items() if len(v) != len(set(v))])

# pronouns that are not pronouns
add("pronouns values that are not pronouns",
    ["%-22s %r" % (nm(f["pid"]), f["value"]) for f in facts
     if f["predicate"] == "pronouns"
     and not re.match(r"^\s*(she|he|they|ze|xe|it)\s*/", str(f["value"]), re.I)])

# A deceased contact keeping facts is correct -- their record documents who they
# were. What would be wrong is WEB RESEARCH conducted on them, so look only for
# enrichment-sourced facts, not for facts drawn from the owner's own email.
_OSINT = {"scout_osint", "scout_research", "web_enrichment", "search",
          "scout_osint_expanded", "research", "inferred"}
add("deceased/archived contacts carrying web-research facts",
    ["%-22s %s" % (p.get("name"),
                   sorted({f["predicate"] for f in facts
                           if f["pid"] == p["id"] and f["source_type"] in _OSINT}))
     for p in persons.values()
     if (str(p.get("is_deceased") or "0") not in ("0", "", "None")
         or str(p.get("is_archived") or "0") not in ("0", "", "None"))
     and any(f["pid"] == p["id"] and f["source_type"] in _OSINT for f in facts)])

# relations pointing at a person who is also the source
add("self-referential relation edges",
    ["%-22s %s" % (nm(r["source_id"]), r["rel_type"])
     for r in con.execute("SELECT source_id, target_id, rel_type FROM edges "
                          "WHERE source_id = target_id")])

# facts recorded in the future. record_time is UTC, so compare against UTC --
# comparing to local time flagged every fact written in the last few hours.
from datetime import timezone as _tz
_utc_now = datetime.now(_tz.utc).isoformat()
add("facts recorded with a future timestamp",
    ["%-22s %-16s %s" % (nm(f["pid"]), f["predicate"], f["record_time"])
     for f in facts if (f["record_time"] or "") > _utc_now])

print("=" * 78)
print("  classes with findings: %d" % len(found))
json.dump(found, open("/tmp/audit_deep.json", "w"), indent=1, default=str)
