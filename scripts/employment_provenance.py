"""Where does the employment data actually come from?

'Heriot-Watt University' for a Bay Area PM was not junk-SHAPED -- it read as a
perfectly good employer. That is the dangerous kind: plausible and wrong. No
string rule finds it, because there is nothing wrong with the string.

The thing that IS knowable is provenance. What the owner typed into Google is
right by definition. What a crawler inferred from a page it found by searching a
name is a guess, and this session has already shown those guesses attaching an
adult site, a Marvel character and a foreign government to real people.

So: how much of the employment data is owner-provided, and how much is inferred?
"""
import collections
import glob
import json
import sqlite3
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G

INFERRED = {"scout_osint", "scout_research", "web_enrichment", "search",
            "scout_osint_expanded", "research", "inferred", "linkedin_profile",
            "scout_low_confidence"}
OWNER = {"google_contacts", "user", "user-stated", "imported", "email_analysis"}

con = sqlite3.connect("/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
con.row_factory = sqlite3.Row

print("=== org / occupation FACTS by source ===")
tot = collections.Counter()
for r in con.execute(
        "SELECT f.predicate, f.source_type, COUNT(*) n FROM facts f "
        "WHERE f.valid_until IS NULL AND f.predicate IN ('org','occupation') "
        "GROUP BY 1,2 ORDER BY n DESC"):
    tot[(r["predicate"], r["source_type"])] = r["n"]
    print("   %-12s %-22s %d" % (r["predicate"], r["source_type"], r["n"]))
inf = sum(n for (_p, s), n in tot.items() if s in INFERRED)
own = sum(n for (_p, s), n in tot.items() if s in OWNER)
print("   inferred=%d  owner=%d  other=%d"
      % (inf, own, sum(tot.values()) - inf - own))

print("\n=== the persons.org / persons.occupation COLUMNS ===")
print("   (the columns the contact record shows, and what google syncs)")
rows = list(con.execute(
    "SELECT id, name, org, occupation, source_type, google_resource_name rn "
    "FROM persons"))
have_org = [r for r in rows if (r["org"] or "").strip()]
have_occ = [r for r in rows if (r["occupation"] or "").strip()]
print("   persons with an org       : %d of %d" % (len(have_org), len(rows)))
print("   persons with an occupation: %d of %d" % (len(have_occ), len(rows)))

# what did GOOGLE have for these before this pipeline ever wrote to it?
snap = json.load(open(sorted(glob.glob("/root/google-contacts-snapshot-*.json"))[0]))
snap_org = {}
for p in snap:
    o = (p.get("organizations") or [{}])[0]
    snap_org[p["resourceName"]] = ((o.get("name") or "").strip(),
                                   (o.get("title") or "").strip())
print("\n   in the pre-change snapshot (2026-08-20, before this session wrote):")
print("     google contacts with an org name : %d"
      % sum(1 for v in snap_org.values() if v[0]))
print("     google contacts with a job title : %d"
      % sum(1 for v in snap_org.values() if v[1]))

matched = differ = only_weave = 0
examples = []
for r in have_org:
    rn = r["rn"]
    if not rn or rn not in snap_org:
        only_weave += 1
        continue
    g = snap_org[rn][0]
    if not g:
        only_weave += 1
        examples.append((r["name"], r["org"], "(google had none)"))
    elif g.strip().lower() == (r["org"] or "").strip().lower():
        matched += 1
    else:
        differ += 1
        if len(examples) < 14:
            examples.append((r["name"], r["org"], g))
print("\n   weave org vs the pre-change google org:")
print("     same value                 : %d" % matched)
print("     different                  : %d" % differ)
print("     google had nothing there   : %d" % only_weave)
print("\n   examples where they differ (weave value | what google had):")
for nm, w, g in examples[:14]:
    print("     %-24s %-30r %r" % (str(nm)[:24], str(w)[:30], str(g)[:30]))
