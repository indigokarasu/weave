#!/usr/bin/env python3
"""company_gate: a company is never sent to person-OSINT, a person always is.

Fixtures are invented. Person names span several naming traditions on purpose:
an Anglo-only fixture set once hid a matcher that silently dropped every name
token under four characters, which skewed hard against East Asian surnames.
"""

import sys

from company_gate import is_company, name_looks_like_org

FAILS = []


def check(desc, got, want):
    if got != want:
        FAILS.append("%s\n     got %r want %r" % (desc, got, want))


def gate(name, given="", family="", org="", is_company_col=None, tagged=False):
    rec = {"name": name, "name_given": given, "name_family": family, "org": org}
    if is_company_col is not None:
        rec["is_company"] = is_company_col
    return is_company(rec, tagged_company=tagged)[0]


# --- the reviewed answer is authoritative in BOTH directions -----------------
check("is_company=1 wins over a person-shaped record",
      gate("Jane Cooper", "Jane", "Cooper", "Acme", is_company_col=1), True)
check("is_company=0 wins over every heuristic",
      gate("Bay Area Plumbing", "Bay Area Plumbing", "", "Bay Area Plumbing",
           is_company_col=0), False)
check("is_company=0 protects a person whose surname is a trade word",
      gate("Sarah Church", "Sarah", "Church", "", is_company_col=0), False)

# NULL means nobody has classified the record yet, so the heuristics run. A
# DEFAULT of 0 on the column would make every unreviewed row read as "reviewed,
# and a person" and disable the heuristics store-wide.
check("unset is_company still runs the heuristics",
      gate("Northwind Ltd", is_company_col=None), True)

# --- the Company tag ---------------------------------------------------------
check("the Company tag refuses the record",
      gate("Anything At All", "Anything", "", "", tagged=True), True)

# --- legal suffixes ----------------------------------------------------------
for nm in ("Northwind Inc", "Northwind Inc.", "Northwind LLC", "Northwind Ltd",
           "Northwind GmbH", "Northwind PLC", "Northwind Pty"):
    check("legal suffix %r" % nm, gate(nm), True)

# --- trade and institution words ---------------------------------------------
for nm in ("Riverside Credit Union", "Elm Street Clinic", "Bright Electrolysis",
           "Corner Custom Framing", "Maple Furniture", "Lakeside Health",
           "Halton Electrical Services", "Fountain Plaza", "Delaney Cleaning",
           "Bay Roofing", "Mission Dental", "Rincon Yoga"):
    check("trade word %r" % nm, gate(nm), True)

# --- bare brands -------------------------------------------------------------
for nm in ("Hertz", "CVS", "T-Mobile", "Amazon.com", "PayPal", "Venmo",
           "Citibank", "Chase Bank"):
    check("brand %r" % nm, gate(nm), True)

# --- the imported-company shape: no surname, org repeats the name ------------
check("no name parts at all, org repeats the name",
      gate("Vellum Press", "", "", "Vellum Press"), True)
check("whole name in givenName, org repeats it",
      gate("Kestrel Analytics", "Kestrel Analytics", "", "Kestrel Analytics"),
      True)
check("initialism with org repeating the name",
      gate("Q-R", "", "", "Q-R"), True)

# --- real people must NOT be refused ----------------------------------------
# This is the expensive misfire: a refused person is skipped silently.
for nm, gn, fn, og in (
    ("Amara Okonkwo", "Amara", "Okonkwo", "Meta"),
    ("Ryan Holt", "Ryan", "Holt", ""),
    ("Jesse Tanner", "Jesse", "Tanner", ""),
    ("Varun Iyer", "Varun", "Iyer", ""),
    ("Freja Lindqvist", "Freja", "Lindqvist", ""),
    ("Sora Matsuda", "Sora", "Matsuda", ""),
    ("Jiho Bae", "Jiho", "Bae", ""),
    ("Ngo Thi Ha", "Ngo", "Thi Ha", ""),
    ("Wang Wei", "Wang", "Wei", ""),
    ("Rosa Delgado", "", "", ""),
):
    check("person %r not refused" % nm, gate(nm, gn, fn, og), False)

# An employed person's org is their EMPLOYER, never their own name.
check("person with an employer is not refused",
      gate("Nadia Fontaine", "Nadia", "Fontaine", "Orchard Systems"), False)
check("person stored with only a first name and no org",
      gate("Dhruv", "Dhruv", "", ""), False)

# --- word-boundary discipline ------------------------------------------------
# Substring matching on trade words would refuse real surnames.
check("'Grillo' is not 'Grill'", name_looks_like_org("Marco Grillo")[0], False)
check("'Bankston' is not 'Bank'", name_looks_like_org("Ada Bankston")[0], False)
check("'Cooper' is not 'coop'", name_looks_like_org("Jane Cooper")[0], False)
check("'Marketa' is not 'Market'", name_looks_like_org("Marketa Novak")[0], False)
check("'Techa' is not 'Tech'", name_looks_like_org("Lena Techa")[0], False)
check("'Bar & Grill' still matches", name_looks_like_org("Joe's Bar & Grill")[0],
      True)

# --- empties -----------------------------------------------------------------
check("empty record is not refused", gate(""), False)
check("record with no fields is not refused", is_company({})[0], False)

if FAILS:
    print("FAILURES (%d):" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("company_gate: all checks passed")
sys.exit(0)
