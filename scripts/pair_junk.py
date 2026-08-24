#!/usr/bin/env python3
import os
"""Evaluate org and title TOGETHER, not one at a time.

The two cases Jared named first survive every single-field rule, because each
half is individually plausible:

    Kim Appelquist   org='Watt'      title='Heriot'      -> 'Heriot-Watt'
    Joe Ashear       org='Franklin'  title='Past Chiefs' -> 'Franklin, Past Chiefs'

One string was split on a comma or hyphen and the halves dropped into two
fields. 'Watt' is a fine company name and 'Heriot' is a fine surname; only the
PAIR shows the damage.

The signal: a real pairing is <employer> + <role>, and a role is nameable. So a
pair where the title names no role AND the company shows no company-ness is a
split, not a job.
"""
import collections
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from job_junk_v3 import ROLES as _BASE_ROLES

# A title is often a FUNCTION or department rather than a named role --
# "Strategic Partnerships", "Talent Acquisition", "Technology Correspondent".
# Without these the pair rule flagged Criteo/Strategic Partnerships and
# NBC News/Technology Correspondent as split strings.
FUNCTIONS = {
    "partnerships", "partnership", "support", "acquisition", "engagement",
    "development", "operations", "marketing", "media", "correspondent", "clerk",
    "realtor", "culture", "training", "communications", "recruiting", "talent",
    "success", "experience", "strategy", "product", "program", "project",
    "quality", "safety", "security", "compliance", "legal", "counsel", "policy",
    "logistics", "procurement", "payroll", "benefits", "admissions", "outreach",
    "fundraising", "philanthropy", "curriculum", "instruction", "admin",
    "administrative", "editorial", "production", "postproduction", "graphics",
    "animation", "illustration", "photography", "copy", "content", "social",
    "brand", "growth", "revenue", "sales", "biz", "bizdev", "corp", "hr",
    "people", "learning", "insights", "analytics", "data", "science", "platform",
    "infrastructure", "systems", "network", "cloud", "mobile", "web", "frontend",
    "backend", "fullstack", "devops", "sre", "qa", "test", "release", "support",
    "care", "service", "relations", "affairs", "advocacy", "government",
    "community", "membership", "volunteer", "volunteers", "events", "hospitality",
    "merchandising", "buying", "planning", "forecasting", "pricing", "treasury",
    "audit", "tax", "risk", "credit", "lending", "underwriting", "claims",
    "actuarial", "clinical", "nursing", "pharmacy", "radiology", "surgery",
    "pediatrics", "oncology", "cardiology", "neurology", "psychiatry",
    "postdoctoral", "faculty", "teaching", "tutoring", "coaching", "mentoring",
    "mba", "phd", "fpa", "pmm", "pmo", "csm", "cs", "ux", "ui", "ai", "ml",
}
ROLES = {w.lower() for w in _BASE_ROLES} | FUNCTIONS

COMPANY_HINT = re.compile(
    r"\b(inc|inc\.|llc|ltd|corp|co|co\.|company|gmbh|plc|sa|ag|bv|nv|group|labs?|"
    r"studios?|agency|bank|hotel|cafe|shop|store|market|clinic|school|university|"
    r"college|foundation|institute|team|services?|solutions|systems|media|press|"
    r"records|design|consulting|capital|ventures|partners|holdings|airlines?|"
    r"restaurant|technologies|technology|software|health|hospital|center|centre|"
    r"museum|gallery|associates|brothers|sons|international|global|worldwide|"
    r"industries|enterprises|works|studio|collective|union|society|association|"
    r"network|digital|creative|ai|labs)\b", re.I)


def has_role(v):
    words = {re.sub(r"[^a-z]", "", w.lower())
             for w in re.split(r"[\s/,&|()\-+]+", v or "")}
    return bool(words & ROLES)


def looks_like_a_company(v):
    v = (v or "").strip()
    if not v:
        return False
    if COMPANY_HINT.search(v):
        return True
    # multi-word title-case names read as companies (Bang Zoom! Entertainment)
    words = re.findall(r"[A-Za-z0-9.&!'-]+", v)
    return len(words) >= 3


def evaluate_pair(org, title, contact_name, is_person):
    o, t = (org or "").strip(), (title or "").strip()
    if not (o and t) or not is_person:
        return None
    if has_role(t):
        return None                      # a nameable role: an ordinary pairing
    if looks_like_a_company(o):
        return None                      # a recognisable employer
    ow, tw = len(re.findall(r"[A-Za-z]+", o)), len(re.findall(r"[A-Za-z]+", t))
    if ow <= 2 and tw <= 3 and o[:1].isupper() and t[:1].isupper():
        return "org+title look like one string split in two"
    return None


if __name__ == "__main__":
    con = sqlite3.connect(
        os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite"))
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT id, name, name_given, name_family, org, occupation "
        "FROM persons WHERE org IS NOT NULL AND org != '' "
        "AND occupation IS NOT NULL AND occupation != ''"))
    hits = []
    for r in rows:
        is_person = bool((r["name_given"] or "").strip()
                         and (r["name_family"] or "").strip())
        why = evaluate_pair(r["org"], r["occupation"], r["name"], is_person)
        if why:
            hits.append(r)
    print("  contacts with BOTH org and title: %d" % len(rows))
    print("  pairs that look like one split string: %d\n" % len(hits))
    for r in sorted(hits, key=lambda x: (x["name"] or "").lower()):
        print("   %-26s org=%-26r title=%r"
              % (str(r["name"])[:26], str(r["org"])[:26], str(r["occupation"])[:34]))
