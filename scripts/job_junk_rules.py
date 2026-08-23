#!/usr/bin/env python3
"""Precise detectors for scrape junk in org/occupation.

My first pass used three crude rules and each had a large false-positive class:
  "another contact's name"  matched org='Google' -- Google IS a contact
  "run-together capitals"   matched DuckDuckGo, OpenAI, IxDA, iOS, GenAI
  "no role word"            matched Chiropractor, Civic Technologist,
                            Clinical Neuropsychologist -- real jobs my word list
                            simply did not contain

So these rules are built the other way round: each one keys on a structure that
has no legitimate reading, and is checked against the real values before use.

  NAV      page furniture lifted from a site: 'Careers', 'Apply',
           'All Restaurants', 'Contact Me Book <name> to speak'
  GLUED    a role word fused to the next word by a missing space:
           'DesignerRetiredH', 'ResearcherHGICVia', 'AssistantAssistant Lec'
           -- a real title never runs a role word straight into a capital
  OWNNAME  an occupation that is a PERSON's name sharing a name-part with the
           contact and containing no role word: 'Angela Bassett' on Angela
           Guzman, 'Benjamin Brown' on Ben Brown, 'Ari Kukkonen' on Dan Kukkonen
"""
import collections
import re
import sqlite3
import sys

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
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
    "cmo", "vp", "executive", "leader", "adjuster", "underwriter", "technologist",
    "neuropsychologist", "chiropractor", "psychologist", "psychiatrist",
    "economist", "statistician", "actuary", "surveyor", "paralegal", "midwife",
    "veterinarian", "dentist", "pharmacist", "optometrist", "radiologist",
    "anesthesiologist", "surgeon", "practitioner", "operations", "marketing",
    "sales", "finance", "engineering", "design", "research",
}

NAV = re.compile(
    r"^(careers?|apply|jobs?|about|about us|contact|contact us|home|menu|"
    r"all restaurants|all locations|our team|the team|meet the team|"
    r"leadership|our story|blog|news|press|events?|shop|store|login|sign in)$",
    re.I)
CTA = re.compile(r"\b(contact me|book me|hire me|get in touch|to speak|"
                 r"meet the leadership|that will make you|click here|sign up|"
                 r"subscribe|learn more|read more|available for booking)\b", re.I)
# a role word running straight into a capital letter = a missing space
GLUED = re.compile(
    r"(?:designer|researcher|assistant|engineer|manager|director|founder|"
    r"president|member|retired|lecturer|consultant|analyst|developer|architect)"
    r"[A-Z]", re.I)


def has_role(v):
    words = {re.sub(r"[^a-z]", "", w.lower())
             for w in re.split(r"[\s/,&|()\-]+", v or "")}
    return bool(words & ROLES)


def looks_like_a_person_name(v):
    t = re.findall(r"[A-Za-z'\-]{2,}", v or "")
    return 2 <= len(t) <= 3 and all(w[:1].isupper() for w in t)


def classify(value, contact_name, field):
    v = (value or "").strip()
    if not v:
        return None
    if NAV.match(v):
        return "page navigation text"
    if CTA.search(v):
        return "call-to-action lifted from a page"
    if GLUED.search(v):
        return "words fused by a missing space"
    if field == "occupation" and looks_like_a_person_name(v) and not has_role(v):
        own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", contact_name or "")}
        vt = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", v)}
        if own & vt:
            return "a person's name, not a job title"
    return None


if __name__ == "__main__":
    con = sqlite3.connect(
        "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT id pid, name, occupation v, 'occupation' f FROM persons "
        "WHERE occupation IS NOT NULL AND occupation != '' "
        "UNION ALL SELECT id, name, org, 'org' FROM persons "
        "WHERE org IS NOT NULL AND org != ''"))
    hits = collections.defaultdict(list)
    for r in rows:
        k = classify(r["v"], r["name"], r["f"])
        if k:
            hits[k].append(r)
    for k, v in hits.items():
        print("\n=== %s : %d ===" % (k, len(v)))
        for r in sorted(v, key=lambda x: (x["v"] or "").lower()):
            print("   %-24s %-11s %r" % (str(r["name"])[:24], r["f"], str(r["v"])[:56]))
    print("\n  values examined: %d ; flagged: %d"
          % (len(rows), sum(len(v) for v in hits.values())))
