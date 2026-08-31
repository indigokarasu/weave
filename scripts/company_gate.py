#!/usr/bin/env python3
"""Decide whether a contact record is an organisation rather than a person.

A company is never enriched. Person-OSINT asks "which online accounts belong to
the human with this name", and asked about a business it answers with whichever
stranger happens to hold a matching handle -- a bank was given an unrelated
individual's GitHub account, username and a CDN asset path as its homepage.
There is no version of that answer that is right, so the question is not asked.

The gate is deliberately asymmetric. Refusing a real person costs one skipped
enrichment, which the next run can revisit once the record is corrected.
Accepting a company costs a stranger's identity written into the address book
and pushed out to Google. So anything that looks like an organisation is
refused, and callers treat "unsure" as "company".

persons.is_company is authoritative WHEN SET, in both directions, because it
is the reviewed answer. Its three states matter and must not be collapsed:

    NULL  nobody has classified this record yet -> fall through to the
          heuristics below
    1     reviewed, it is an organisation       -> always refused
    0     reviewed, it is a person              -> always admitted, so a
          heuristic false positive cannot keep refusing them

The column must therefore NOT carry a DEFAULT of 0. Adding it with one makes
every unreviewed row read as "reviewed, and a person", which silently disables
the heuristics for the entire store -- eight organisations went straight
through the gate that way before the end-to-end test caught it.
"""

import re

# Words that only ever appear in the name of an organisation. A person is not
# called "Ltd", and a contact whose name ends in one is not a human being.
_LEGAL_SUFFIXES = (
    "inc", "inc.", "llc", "l.l.c.", "ltd", "ltd.", "limited", "corp", "corp.",
    "corporation", "co", "co.", "company", "gmbh", "ag", "nv", "bv", "plc",
    "llp", "lp", "pllc", "pc", "sa", "srl", "spa", "oy", "ab", "as", "aps",
    "kk", "pty", "pvt", "sdn", "bhd", "cic", "cooperative", "coop",
)

# Trade and institution words. A contact called "<something> Clinic" or
# "<something> Credit Union" is a business even when nothing else says so.
_ORG_WORDS = (
    "bank", "credit union", "insurance", "realty", "realtors", "mortgage",
    "capital", "ventures", "holdings", "partners", "associates", "group",
    "agency", "studios", "studio", "salon", "spa", "barbers", "barbershop",
    "clinic", "dental", "dentistry", "orthodontics", "optometry", "veterinary",
    "vet", "hospital", "health", "healthcare", "medical", "pharmacy",
    "laboratory", "labs", "diagnostics", "therapy", "physio", "chiropractic",
    "electrolysis", "dermatology",
    "restaurant", "cafe", "café", "coffee", "bakery", "kitchen", "grill",
    "bistro", "pizzeria", "taqueria", "sushi", "brewing", "brewery", "winery",
    "bar & grill", "catering", "deli", "creamery",
    "hotel", "motel", "inn", "hostel", "resort", "lodge",
    "airlines", "airways", "air lines", "railways", "transit", "taxi",
    "plumbing", "roofing", "hvac", "heating", "electrical", "electric",
    "construction", "contracting", "contractors", "builders", "remodeling",
    "landscaping", "gardening", "cleaning", "cleaners", "laundry", "janitorial",
    "movers", "moving", "storage", "hauling", "towing", "auto body", "autobody",
    "motors", "automotive", "tire", "tires", "garage",
    "framing", "upholstery", "furniture", "flooring", "carpet", "hardware",
    "supply", "supplies", "wholesale", "retail", "market", "grocery",
    "boutique", "outfitters", "apparel", "jewelers", "jewellers", "florist",
    "flowers", "nursery", "pet shop", "petcare",
    "school", "academy", "university", "college", "institute", "conservatory",
    "museum", "library", "foundation", "charity", "church", "temple",
    "synagogue", "mosque", "ministries", "society", "association", "council",
    "committee", "union", "federation", "alliance", "network",
    "services", "solutions", "systems", "technologies", "technology", "tech",
    "software", "digital", "media", "consulting", "consultants", "advisors",
    "advisory", "management", "logistics", "shipping", "freight", "delivery",
    "security", "staffing", "recruiting", "properties", "property", "leasing",
    "rentals", "rental", "plaza", "center", "centre", "gym", "fitness",
    "yoga", "pilates", "crossfit", "printing", "signs", "graphics",
)

# Widely known consumer brands whose names carry no trade word, so no rule
# above would catch them. A generic list of large companies -- not a reflection
# of any particular address book: a store's own organisations are recorded in
# persons.is_company, which outranks everything here.
_KNOWN_BRANDS = (
    "amazon", "google", "apple", "microsoft", "meta", "netflix", "spotify",
    "uber", "lyft", "doordash", "grubhub", "instacart", "opentable",
    "paypal", "venmo", "stripe", "square", "wise", "revolut", "klarna",
    "citibank", "citi", "chase", "wells fargo", "amex", "visa", "mastercard",
    "hsbc", "barclays", "santander", "natwest", "monzo",
    "t-mobile", "verizon", "vodafone", "comcast", "xfinity", "spectrum",
    "cvs", "walgreens", "target", "costco", "safeway", "tesco", "aldi",
    "hertz", "avis", "zipcar", "airbnb", "expedia", "booking.com",
    "fedex", "dhl", "ups", "usps", "royal mail",
    "ikea", "wayfair", "etsy", "ebay", "shopify", "peloton",
)

_PUNCT = re.compile(r"[^a-z0-9&\s\.\-]+")
_WS = re.compile(r"\s+")


def _norm(s):
    s = _PUNCT.sub(" ", str(s or "").lower())
    return _WS.sub(" ", s).strip()


def _tokens(s):
    return [t.strip(".") for t in _norm(s).split(" ") if t.strip(".")]


def name_looks_like_org(name):
    """(True, reason) when the NAME alone marks this as an organisation."""
    n = _norm(name)
    if not n:
        return False, ""
    toks = _tokens(n)
    if toks and toks[-1] in _LEGAL_SUFFIXES:
        return True, "name ends in a legal-entity suffix (%s)" % toks[-1]
    for w in _ORG_WORDS:
        # Word-boundary match so "Grill" hits but "Grillo" does not, and a
        # multi-word marker like "credit union" still matches.
        if re.search(r"(?:^|\s)%s(?:\s|$)" % re.escape(w), n):
            return True, "name contains the trade word %r" % w
    # A brand is often filed under its domain ("Amazon.com"), so compare the
    # bare name too rather than letting the TLD hide it.
    bare = re.sub(r"\.(com|net|org|io|co|ai|app|shop|store)$", "", n)
    for b in _KNOWN_BRANDS:
        for cand in (n, bare):
            if cand == b or cand.startswith(b + " ") or cand.endswith(" " + b):
                return True, "name is the brand %r" % b
    return False, ""


def is_company(contact, tagged_company=False):
    """(True, reason) when this record must never be sent to person-OSINT.

    contact: a mapping or sqlite3.Row with any of is_company, name, name_given,
    name_family, org. tagged_company: whether the record carries the Company tag.
    """
    if hasattr(contact, "keys") and not isinstance(contact, dict):
        get = lambda k, d="": (contact[k] if k in contact.keys() else d)  # noqa: E731
    else:
        contact = contact or {}
        get = contact.get

    # The reviewed answer wins outright, in both directions: a record confirmed
    # to be a person is not second-guessed by a trade word in their surname.
    flag = get("is_company", None)
    if flag is not None and str(flag).strip() not in ("", "None"):
        try:
            if int(flag) == 1:
                return True, "is_company is set on the record"
            return False, ""
        except (TypeError, ValueError):
            pass

    if tagged_company:
        return True, "carries the Company tag"

    name = (get("name", "") or "").strip()
    given = (get("name_given", "") or "").strip()
    family = (get("name_family", "") or "").strip()
    org = (get("org", "") or "").strip()

    org_like, why = name_looks_like_org(name)
    if org_like:
        return True, why

    # A person has a surname. A company record imported from Google has no
    # surname -- the whole business name sits in givenName, or in neither name
    # field -- and its org repeats that same name, because the business IS the
    # organisation. Neither half is sufficient alone: plenty of real people are
    # stored with only a first name, and every employed person has an org that
    # is NOT their own name.
    if not family and org and _norm(org) in (_norm(name), _norm(given)):
        return True, "no surname and the org field repeats the name"

    return False, ""
