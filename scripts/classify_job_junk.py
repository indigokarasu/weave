import os
"""How much of org/occupation is scrape junk, and can it be detected precisely?

The examples Jared gave are four different failures:
  'Heriot' / 'Watt'                     one proper noun split across org+title
  'Franklin' / 'Past Chiefs'            same
  'Contact Me Book Katie Allen to speak' a call-to-action lifted from a page
  'Trust Issues'                        page text that is not a job
  'r and FounderThe long'               run-together scraped text
plus, from the value dump:
  'Angela Bassett'                      a celebrity's name
  'Alli Donovan Account Executive'      somebody else's name + title
  'AI Skills That Will Make You Irreplaceable'  an article headline
  '(UX) ResearcherHGICVia'              run-together

A real job title almost always contains a ROLE word. That is the strongest single
signal; everything else here is a supporting rule.
"""
import collections
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from sweep_field_placement import _ROLE_WORDS

con = sqlite3.connect(os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite"))
con.row_factory = sqlite3.Row
contact_names = {r[0].strip().lower() for r in con.execute(
    "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")}

EXTRA_ROLES = {
    "engineer", "designer", "developer", "researcher", "scientist", "manager",
    "director", "founder", "cofounder", "co-founder", "president", "chief",
    "officer", "lead", "head", "partner", "principal", "analyst", "consultant",
    "architect", "specialist", "coordinator", "administrator", "producer",
    "editor", "writer", "author", "artist", "photographer", "attorney", "lawyer",
    "counsel", "nurse", "physician", "doctor", "professor", "lecturer", "teacher",
    "instructor", "student", "intern", "associate", "assistant", "advisor",
    "strategist", "planner", "recruiter", "therapist", "coach", "trainer",
    "owner", "operator", "technician", "programmer", "marketer", "seller",
    "buyer", "agent", "broker", "banker", "trader", "accountant", "auditor",
    "chef", "curator", "dean", "chair", "fellow", "ceo", "cto", "coo", "cfo",
    "cmo", "cio", "vp", "svp", "evp", "avp", "md", "gm", "pm", "swe", "ux", "ui",
    "phd", "postdoc", "postdoctoral", "apprentice", "steward", "evangelist",
    "ambassador", "liaison", "supervisor", "controller", "treasurer", "secretary",
    "executive", "leader", "leadership", "management", "generalist", "expert",
    "practitioner", "maker", "creator", "musician", "composer", "vocalist",
    "songwriter", "actor", "animator", "illustrator", "copywriter", "publisher",
}
ROLES = {w.lower() for w in _ROLE_WORDS} | EXTRA_ROLES

CTA = re.compile(r"\b(contact me|book me|hire me|get in touch|to speak|meet the|"
                 r"learn more|read more|click here|sign up|subscribe|follow me|"
                 r"see more|view profile|available for|that will make you)\b", re.I)
RUNON = re.compile(r"[a-z][A-Z]{2,}|[a-z][A-Z][a-z]+[A-Z]")


def has_role(v):
    words = {re.sub(r"[^a-z]", "", w.lower()) for w in re.split(r"[\s/,&|()-]+", v or "")}
    return bool(words & ROLES)


rows = []
for r in con.execute(
        "SELECT id AS pid, name, occupation AS v, 'persons.occupation' AS loc "
        "FROM persons WHERE occupation IS NOT NULL AND occupation != '' "
        "UNION ALL SELECT id, name, org, 'persons.org' FROM persons "
        "WHERE org IS NOT NULL AND org != ''"):
    rows.append(dict(r))

buckets = collections.defaultdict(list)
for r in rows:
    v = (r["v"] or "").strip()
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", r["name"] or "")}
    low = v.lower()
    if CTA.search(v):
        buckets["call-to-action / page text"].append(r)
    elif RUNON.search(v):
        buckets["run-together scraped text"].append(r)
    elif low in contact_names and low not in {(r["name"] or "").lower()}:
        buckets["is another contact's name"].append(r)
    elif r["loc"].endswith("occupation") and not has_role(v):
        buckets["occupation with no role word"].append(r)

for k in ("call-to-action / page text", "run-together scraped text",
          "is another contact's name", "occupation with no role word"):
    v = buckets.get(k) or []
    print("\n=== %s : %d ===" % (k, len(v)))
    for r in sorted(v, key=lambda x: (x["v"] or "").lower())[:24]:
        print("   %-24s %-20s %r" % (str(r["name"])[:24], r["loc"], str(r["v"])[:52]))
print("\n  org/occupation values examined: %d" % len(rows))
print("  flagged: %d" % sum(len(v) for v in buckets.values()))
