#!/usr/bin/env python3
"""One person, one current answer to a single-valued question.

108 contacts currently hold two or more simultaneously-valid answers to
questions that only have one: Mads Paulin works at both 'Aitera Robotics' and
'MiR', and his job title is 'Robotics Leader & Founder', 'vice president of R&D'
and 'Pierre Hathout President' at once. The last of those is not even about him
-- it is another executive named on the same company blog post.

The database already has the right mechanism: a single-valued predicate is meant
to supersede, with the old fact stamped valid_until and superseded_by pointing at
the one that replaced it. These rows never went through it, because separate
pipeline runs each inserted in parallel. (Some of them I added myself, unpacking
the payload facts by direct insert rather than through the superseding path.)

Choosing between them is done on the SOURCE, not the value, and not the
timestamp -- ranking by recency would have kept 'Internet' over 'Wikimedia
Foundation' earlier in this cleanup. The question asked of each source is
whether the page it came from is about this person at all:

  names them, not a broker  linkedin.com/in/madspaulin  -- his own profile
  some other page           robophil.com/the-mir1200-…  -- an article he appears in
  a people-search broker    idcrawl.com/gwendolyn-mcginn -- a name-matched
                                                          aggregate, the exact
                                                          source that attributed
                                                          a stranger's profiles
                                                          to a contact earlier

When the best tier holds a single fact it becomes current and the rest are
superseded by it. When two facts tie at the top, nothing is guessed: they are
left as they are and printed for review. Values are never merged, edited or
invented -- only ranked.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"
SINGLE = ("org", "occupation", "location_city")

# People-search aggregates: a page assembled by matching a NAME, which is how a
# stranger's records get attached to a contact. Never the best source available.
BROKERS = {
    "idcrawl", "spokeo", "whitepages", "radaris", "beenverified", "peoplefinders",
    "truepeoplesearch", "fastpeoplesearch", "thatsthem", "peekyou", "mylife",
    "intelius", "instantcheckmate", "socialcatfish", "usphonebook", "clustrmaps",
    "rocketreach", "zoominfo", "signalhire", "contactout", "lusha", "adapt",
    "apollo", "leadiq", "kendo", "snov", "hunter", "anymailfinder",
}


def flat(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def host_of(ref):
    m = re.search(r"https?://([^/\s]+)", str(ref or ""))
    return m.group(1).lower() if m else ""


def names_the_person(ref, person_name):
    """Does the source URL itself identify this contact?

    A profile URL carries the person in its path -- /in/madspaulin. An article
    that merely mentions them does not. Requires the family name plus one more
    name part, so a common given name alone cannot qualify a page.
    """
    ref = str(ref or "").lower()
    parts = [flat(p) for p in re.split(r"\s+", person_name or "") if len(flat(p)) > 2]
    if len(parts) < 2:
        return False
    path = flat(ref)
    hit = sum(1 for p in parts if p in path)
    return hit >= 2 or (parts[-1] in path and len(parts[-1]) >= 6)


def tier(ref, person_name):
    """Lower is better."""
    h = host_of(ref)
    label = h.split(".")[0] if h else ""
    if len(h.split(".")) > 2:
        label = h.split(".")[-2]
    if label in BROKERS:
        return 3
    if names_the_person(ref, person_name):
        return 1
    return 2 if h else 3            # no URL at all is no better than a broker


ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "SELECT f.id, f.predicate, f.value, f.source_type, f.source_ref, f.record_time, "
    "e.source_id pid, p.name pname FROM facts f "
    "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
    "JOIN persons p ON p.id=e.source_id "
    "WHERE f.valid_until IS NULL AND f.predicate IN %s" % (SINGLE,))]

groups = defaultdict(list)
for r in rows:
    groups[(r["pid"], r["pname"], r["predicate"])].append(r)
conflicted = {k: v for k, v in groups.items() if len({
    str(x["value"]).strip().lower() for x in v}) > 1}

resolved, ties = [], []
for key, facts in conflicted.items():
    _pid, pname, _pred = key
    for f in facts:
        f["_tier"] = tier(f["source_ref"], pname)
    best = min(f["_tier"] for f in facts)
    top = [f for f in facts if f["_tier"] == best]
    distinct_top = {str(f["value"]).strip().lower() for f in top}
    if len(distinct_top) == 1:
        winner = top[0]
        losers = [f for f in facts if f["id"] != winner["id"]
                  and str(f["value"]).strip().lower()
                  != str(winner["value"]).strip().lower()]
        # identical duplicates at the top also collapse into the winner
        losers += [f for f in top[1:] if str(f["value"]).strip().lower()
                   == str(winner["value"]).strip().lower()]
        resolved.append((key, winner, losers))
    else:
        ties.append((key, top))

print("  contacts with a contradictory single-valued fact : %d" % len(conflicted))
print("  resolvable on source quality                     : %d" % len(resolved))
print("  genuine ties, left alone for review              : %d" % len(ties))
print("\n  tier of the winning fact:")
for t, n in Counter(w["_tier"] for _k, w, _l in resolved).most_common():
    print("     tier %d (%s) %d" % (t, {1: "the person's own page", 2: "some other page",
                                        3: "a broker or no url"}[t], n))
print("\n  examples of what is kept and what is superseded:")
for (_pid, pname, pred), w, losers in resolved[:12]:
    print("   %-20s %-13s KEEP %r" % (str(pname)[:20], pred, str(w["value"])[:38]))
    for l in losers:
        print("   %-20s %-13s   -> %-38r (%s)"
              % ("", "", str(l["value"])[:38], host_of(l["source_ref"]) or "no url"))
if ties:
    print("\n  ties -- equally good sources disagree, so nothing is chosen:")
    for (_pid, pname, pred), top in ties[:10]:
        print("   %-20s %-13s %s" % (str(pname)[:20], pred,
              " | ".join(sorted({str(f["value"])[:26] for f in top}))[:72]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for _key, w, losers in resolved:
        for l in losers:
            con.execute("UPDATE facts SET valid_until = ?, superseded_by = ? "
                        "WHERE id = ?", (now, w["id"], l["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("\n  superseded %d facts across %d contacts"
      % (sum(len(l) for _k, _w, l in resolved), len(resolved)))

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "contradictions-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "resolved": [
    {"name": k[1], "predicate": k[2], "kept": w["value"], "kept_source": w["source_ref"],
     "superseded": [{"value": l["value"], "source": l["source_ref"]} for l in ls]}
    for k, w, ls in resolved],
    "ties": [{"name": k[1], "predicate": k[2],
              "values": sorted({f["value"] for f in t})} for k, t in ties]},
    open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
