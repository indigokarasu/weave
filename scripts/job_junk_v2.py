#!/usr/bin/env python3
"""Detect scrape junk in org/occupation, by structure rather than by vocabulary.

Reading all 693 distinct occupations, the junk falls into four shapes. Each rule
below keys on something a real job title never does, and each is checked against
the full value list before use -- my first attempt flagged DuckDuckGo, OpenAI,
iOS, Chiropractor and Clinical Neuropsychologist, all real.

FRAGMENT   the value starts or ends mid-word, or two words are fused by a
           missing space. A scrape took a slice out of a sentence:
             'd, Google Creative LabLin'   'ccessCo-Founder & Chief Tech'
             'e. Founder, Executive Di'    'erknownWhi'
             'Art Director & Boo'          'AssistantAssistant Lec'
PAGE       navigation or marketing copy lifted from a web page:
             'Careers'  'Apply'  'Current Openings'  'All Restaurants'
             'Contact Me Book Katie Allen to speak'
             'AI Skills That Will Make You Irreplaceable'
META       commentary about the role rather than the role:
             'Formerly'  'ent to present'  'Ashley Hirsch is currently the Manager'
             'II/7I changed role'
PERSON     somebody's name in a job-title field:
             'Angela Bassett'  'Ari Kukkonen'  'Benjamin Brown'
             'Alli Donovan Account Executive'
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
    "cmo", "cio", "vp", "evp", "svp", "executive", "leader", "adjuster",
    "underwriter", "technologist", "psychologist", "chiropractor", "midwife",
    "veterinarian", "dentist", "pharmacist", "surgeon", "practitioner",
    "operations", "marketing", "sales", "finance", "engineering", "design",
    "research", "animator", "illustrator", "copywriter", "publisher", "musician",
    "pastor", "worker", "representative", "staff", "ethicist", "essayist",
    "neuropsychologist", "cardiology", "clinical", "professional",
}

# real values that would otherwise trip a structural rule
ALLOW = {
    "flash artist, motionographer",      # lowercase but a real self-description
    "director",                          # lowercase spelling of a real title
    "co-founder",
}

PAGE = re.compile(
    r"^(careers?|apply|current openings|jobs?|all restaurants|all locations|"
    r"our team|the team|leadership|about|about us|contact|contact us|home|menu|"
    r"blog|news|press|events?|shop|store|login|equipo|field day|feelings lyrics)$",
    re.I)
CTA = re.compile(
    r"\b(contact me|book me|hire me|to speak|meet the leadership|"
    r"that will make you|click here|sign up|subscribe|learn more|read more|"
    r"to join my team|to visit|become a|discover a|crunchbase person profile)\b",
    re.I)
META = re.compile(
    r"\b(formerly|is currently|and held|to present|changed role|used to be|"
    r"previously held|see profile|no longer)\b|^\s*(formerly|ent to present)\s*$",
    re.I)
# a role word fused straight into a capital: a missing space
GLUED = re.compile(
    r"(?:designer|researcher|assistant|engineer|manager|director|founder|"
    r"president|member|retired|lecturer|consultant|analyst|developer|architect|"
    r"division|access|officer)[A-Z]", re.I)
# starts mid-word: one or two letters then punctuation, or a lowercase opener
STARTS_MID = re.compile(r"^[a-z]{1,2}[.,]\s|^[a-z]{1,3}[A-Z]")
# ends mid-word: last token is a 2-4 letter capitalised stub with no vowel-y ending
ENDS_STUB = re.compile(r"\b[A-Z][a-z]{1,3}$")
KNOWN_TAIL = {"Lab", "Inc", "Ltd", "Cir", "Ops", "Eng", "Art", "Law", "Med",
              "Web", "Dev", "Sci", "Tech", "Mgr", "Dir", "Exec", "Lead", "Chief",
              "Coach", "Staff", "Owner", "Nurse", "Chef", "Aide", "Rep"}


def has_role(v):
    words = {re.sub(r"[^a-z]", "", w.lower())
             for w in re.split(r"[\s/,&|()\-+]+", v or "")}
    return bool(words & ROLES)


def classify(value, contact_name, field, contact_names):
    v = (value or "").strip()
    if not v or v.lower() in ALLOW:
        return None
    if PAGE.match(v) or CTA.search(v):
        return "page or marketing copy"
    if META.search(v):
        return "commentary about the role, not the role"
    if GLUED.search(v) or STARTS_MID.search(v):
        return "fragment: words fused or starts mid-word"
    m = ENDS_STUB.search(v)
    if m and m.group(0) not in KNOWN_TAIL and len(v.split()) > 1:
        return "fragment: ends mid-word"
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
        "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
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
