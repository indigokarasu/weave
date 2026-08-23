#!/usr/bin/env python3
"""Junk in the COMPANY field specifically.

The title pass is done. Org has a different failure shape, visible in the value
dump:

  SURNAME    the contact's own surname as their employer -- the tail of the
             comma-split that produced 'Heriot' / 'Watt':
               Lauren Mayer-Beug     -> 'Beug'
               Rebecca Garza-Bortman -> 'Bortman'
               Kim-Mai Cutler        -> 'Cutler'
               Kim Appelquist        -> 'Watt'      (from Heriot-Watt)
             This was deliberately NOT applied to org before, because a COMPANY
             contact is legitimately named after itself (AlphaSights, Ramp).
             So it applies only where the contact is a person.
  TRUNCATED  a value cut at a fixed width, ending mid-phrase:
               'Bridge Builder Marriage Ministry Certified Life Coach, Jim'
               'Center for Macroecology, Evolution and Climate, Department'
               'HubSpot, Product Leader, Builder, Mentor, Angel Investor &'
  SENTENCE   prose where a company name belongs:
               'Both the Executive and Individual Contributor Levels.'
               'All of it'
"""
import collections
import re
import sqlite3
import sys

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")

COMPANY_HINT = re.compile(
    r"\b(inc|llc|ltd|corp|co|company|gmbh|plc|group|labs?|studios?|agency|bank|"
    r"hotel|cafe|shop|store|market|clinic|school|university|college|foundation|"
    r"institute|team|services?|solutions|systems|media|press|records|design|"
    r"consulting|capital|ventures|partners|holdings|airlines?|restaurant|"
    r"technologies|software|health|hospital|center|centre|museum|gallery)\b",
    re.I)
# ends on a dangling connective or a half word: a fixed-width truncation
TRUNCATED = re.compile(r"(?:,\s*[A-Z][a-z]{1,4}|&|\band\b|\bor\b|\bthe\b|\bof\b|"
                       r"\bwith\b|\bfor\b|\bat\b|\bin\b)\s*$", re.I)
# 'This American Life' and 'This Troubled Planet' are real company names, so a
# leading "This" proves nothing. Only openers that cannot begin a company name.
SENTENCE = re.compile(r"^(all of it|both the|the following|see below|as above)", re.I)
# a real legal suffix, not a truncation
LEGAL_SUFFIX = re.compile(r",?\s*(inc|inc\.|llc|ltd|ltd\.|plc|gmbh|co|co\.|corp|"
                          r"corp\.|lp|llp|pllc|pc|sa|ag|bv|nv|oy|ab|as|kk)\s*$", re.I)


def is_company_contact(name, has_person_markers):
    if has_person_markers:
        return False
    return bool(COMPANY_HINT.search(name or "")) or len(
        [w for w in re.findall(r"[A-Za-z]+", name or "")]) <= 1


def classify_org(value, contact_name, person_markers):
    v = (value or "").strip()
    if not v:
        return None
    if SENTENCE.match(v):
        return "prose where a company name belongs"
    if not LEGAL_SUFFIX.search(v):
        if len(v) >= 45 and TRUNCATED.search(v):
            return "truncated mid-phrase"
        if TRUNCATED.search(v) and len(v.split()) > 3:
            return "truncated mid-phrase"
    if person_markers:      # not a person: a company named after itself is fine
        return None
    # An eponymous firm is real and common in design, law and consulting --
    # Cooper, Sasaki, Georgeson and Wert&Co. all exist. So a SURNAME as employer
    # is reported, not removed. What is never right is a GIVEN name, or a
    # fragment of a hyphenated surname, as the company.
    parts = [w for w in re.findall(r"[A-Za-z]{2,}", contact_name or "")]
    if not parts:
        return None
    given = parts[0].lower()
    surname_parts = [w.lower() for w in
                     re.findall(r"[A-Za-z]{2,}", parts[-1] if parts else "")]
    hyphenated = [w.lower() for w in re.split(r"[-\s]+", " ".join(parts[1:]))
                  if len(w) > 1]
    vt = [w.lower() for w in re.findall(r"[A-Za-z]{2,}", v)]
    if len(vt) == 1:
        if vt[0] == given:
            return "the contact's GIVEN name as their employer"
        if len(hyphenated) > 1 and vt[0] in hyphenated:
            return "one half of the contact's hyphenated surname"
    return None


if __name__ == "__main__":
    con = sqlite3.connect(
        "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT id pid, name, name_given, name_family, org v, email, phone, "
        "birthday, occupation FROM persons WHERE org IS NOT NULL AND org != ''"))
    hits = collections.defaultdict(list)
    for r in rows:
        # A COMPANY contact legitimately has org == its own name (Google, DJI,
        # PayPal). Only a contact with BOTH a given and a family name is a
        # person, and only then is "org == my own name" wrong.
        is_person = bool((r["name_given"] or "").strip()
                         and (r["name_family"] or "").strip())
        k = classify_org(r["v"], r["name"], not is_person)
        if k:
            hits[k].append(r)
    for k in sorted(hits):
        print("\n=== %s : %d ===" % (k, len(hits[k])))
        for r in sorted(hits[k], key=lambda x: (x["v"] or "").lower()):
            print("   %-26s org=%r" % (str(r["name"])[:26], str(r["v"])[:56]))
    print("\n  org values examined: %d ; flagged: %d"
          % (len(rows), sum(len(v) for v in hits.values())))
