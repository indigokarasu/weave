#!/usr/bin/env python3
import os
"""Scrape-junk detection for org/occupation, third attempt.

v2 had two false-positive classes big enough to make it useless:

  "ends mid-word" flagged Best Buy, Capital One, NBC News, Jones Day, Top Dog --
  real company names ending in a short capitalised word. Dropped entirely: a
  trailing short word carries no signal.

  "words fused" flagged Cline Architects, because GLUED used re.I with an [A-Z]
  class, so [A-Z] matched the lowercase 's' of "Architects". Case-insensitive
  matching silently destroys a case-based rule -- the same mistake made earlier
  in this session on a different regex.

What survives keys on structure that has no legitimate reading:
  LOWER_START   a value beginning mid-word: 'd, Google Creative LabLin',
                'ent to present', 'erknownWhi', 'f the successfulCo Cre'
  GLUED         two words fused, detected case-SENSITIVELY:
                'ResearcherHGICVia', 'DesignerRetiredH', 'AssistantAssistant Lec'
  PAGE / CTA    site furniture: 'Careers', 'Contact Me Book <name> to speak'
  META          commentary: 'Formerly', 'II/7I changed role'
  PERSON        a name where a job belongs: 'Angela Bassett', 'Robert Plant'
"""
import collections
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from sweep_field_placement import _ROLE_WORDS

ROLES = {w.lower() for w in _ROLE_WORDS} | {
    "engineer", "designer", "developer", "researcher", "scientist", "manager",
    "director", "founder", "president", "chief", "officer", "lead", "head",
    "partner", "principal", "analyst", "consultant", "architect", "specialist",
    "coordinator", "administrator", "producer", "editor", "writer", "author",
    "artist", "photographer", "attorney", "lawyer", "counsel", "nurse",
    "physician", "doctor", "professor", "lecturer", "teacher", "instructor",
    "student", "intern", "associate", "assistant", "advisor", "strategist",
    "planner", "recruiter", "therapist", "coach", "trainer", "owner", "operator",
    "technician", "programmer", "agent", "broker", "banker", "accountant",
    "chef", "curator", "dean", "chair", "fellow", "ceo", "cto", "coo", "cfo",
    "cmo", "vp", "evp", "svp", "executive", "leader", "adjuster", "underwriter",
    "technologist", "psychologist", "chiropractor", "midwife", "veterinarian",
    "dentist", "pharmacist", "surgeon", "practitioner", "operations",
    "marketing", "sales", "finance", "engineering", "design", "research",
    "animator", "illustrator", "copywriter", "publisher", "musician", "pastor",
    "worker", "representative", "staff", "obituary", "profiles",
}

PAGE = re.compile(
    r"^(careers?|apply|current openings|jobs?|all restaurants|all locations|"
    r"our team|the team|leadership|about|about us|contact|contact us|home|menu|"
    r"blog|news|press|events?|shop|store|login|equipo|field day|feelings lyrics|"
    r"verified info|how to get a job)$", re.I)
CTA = re.compile(
    r"\b(contact me|book me|hire me|to speak|meet the leadership|"
    r"that will make you|click here|sign up|subscribe|learn more|read more|"
    r"to join my team|to visit|become a health|discover a career|"
    r"crunchbase person profile|how to hire)\b", re.I)
META = re.compile(
    r"\b(formerly|is currently|and held leadership|to present|changed role|"
    r"used to be|previously held|no longer)\b", re.I)

# CASE-SENSITIVE: a role word fused straight into a capital = a missing space.
GLUED = re.compile(
    r"(?:Designer|Researcher|Assistant|Engineer|Manager|Director|Founder|"
    r"President|Member|Retired|Lecturer|Consultant|Analyst|Developer|Division|"
    r"Access|Officer|Lab)(?=[A-Z])")
# begins mid-word: a stray 1-3 letter opener, or lowercase then a capital
LOWER_START = re.compile(r"^(?:[a-z]{1,3}[.,]\s|[a-z]{1,4}[A-Z]|[a-z]+\s+(?:to|the)\s)")
# a lone lowercase fragment with no role word at all
LONE_FRAGMENT = re.compile(r"^[a-z][a-z\s]{2,24}$")


def has_role(v):
    words = {re.sub(r"[^a-z]", "", w.lower())
             for w in re.split(r"[\s/,&|()\-+]+", v or "")}
    return bool(words & ROLES)


def classify(value, contact_name, field, contact_names):
    v = (value or "").strip()
    if not v:
        return None
    if PAGE.match(v) or CTA.search(v):
        return "page or marketing copy"
    if META.search(v):
        return "commentary about the role, not the role"
    if GLUED.search(v):
        return "fragment: two words fused by a missing space"
    if LOWER_START.match(v) or (LONE_FRAGMENT.match(v) and not has_role(v)):
        return "fragment: begins mid-word"
    if field == "occupation":
        t = re.findall(r"[A-Za-z'\-]{2,}", v)
        if 2 <= len(t) <= 3 and all(w[:1].isupper() for w in t) and not has_role(v):
            own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", contact_name or "")}
            vt = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", v)}
            if (own & vt) or v.strip().lower() in contact_names:
                return "a person's name, not a job title"
    return None


if __name__ == "__main__":
    con = sqlite3.connect(
        os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite"))
    con.row_factory = sqlite3.Row
    names = {r[0].strip().lower() for r in con.execute(
        "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")}
    rows = list(con.execute(
        "SELECT id pid, name, occupation v, 'occupation' f FROM persons "
        "WHERE occupation IS NOT NULL AND occupation != '' "
        "UNION ALL SELECT id, name, org, 'org' FROM persons "
        "WHERE org IS NOT NULL AND org != ''"))
    hits = collections.defaultdict(list)
    for r in rows:
        k = classify(r["v"], r["name"], r["f"], names)
        if k:
            hits[k].append(r)
    for k in sorted(hits):
        v = hits[k]
        print("\n=== %s : %d ===" % (k, len(v)))
        for r in sorted(v, key=lambda x: (x["v"] or "").lower()):
            print("   %-24s %-11s %r" % (str(r["name"])[:24], r["f"], str(r["v"])[:58]))
    print("\n  values examined: %d ; flagged: %d"
          % (len(rows), sum(len(v) for v in hits.values())))
