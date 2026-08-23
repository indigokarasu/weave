#!/usr/bin/env python3
"""Move degree suffixes and pronouns out of the family-name field.

Google Contacts has honorificSuffix for 'PhD' and the People API models pronouns
separately, but several contacts carry them inside familyName:

    familyName = 'Vuong, PhD'
    familyName = 'Ulaby, Ph.D.'
    familyName = 'Boddicker, M.HCI (she/her)'

which makes the name unsortable, unmatchable, and wrong in every place the family
name is used on its own. Same rule as the custom fields holding a US state: data
belongs in the field that models it.

Pronouns found this way are reported, not written -- the People API has no
pronouns field, and weave already stores them as a fact, so inventing a custom
field here would break the "no custom field for real data" rule in the other
direction.

Dry-run by default.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402

API = "https://people.googleapis.com/v1"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

SUFFIX = re.compile(
    r"[,\s]+((?:Ph\.?\s?D\.?|M\.?D\.?|M\.?B\.?A\.?|J\.?D\.?|Esq\.?|Jr\.?|Sr\.?|"
    r"I{2,3}|IV|D\.?D\.?S\.?|D\.?V\.?M\.?|R\.?N\.?|C\.?P\.?A\.?|P\.?E\.?|"
    r"M\.?Sc\.?|M\.?S\.?|M\.?A\.?|B\.?A\.?|B\.?S\.?|M\.?HCI|LCSW|LMFT|PMP|"
    r"MPH|MFA|EdD|PsyD|DPhil|FAIA|AIA))\s*$", re.I)
PRONOUNS = re.compile(r"\s*\(\s*(she|he|they|ze|xe)\s*/\s*[^)]*\)\s*", re.I)

# Only titles that are never also surnames. Bishop/Chief/Judge/Coach are, and a
# wider list renamed 'Anna Bishop Rehrig' to 'Anna Rehrig'.
PREFIX = re.compile(r"^\s*((?:Dr|Mr|Mrs|Ms|Mx|Prof|Professor|Rev|Revd)\.?)\s+(?=\S)", re.I)

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF"
    "\U00002300-\U000023FF\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F"
    "\U0001F1E6-\U0001F1FF\U000024C2\U0000200D]+")

# A company contact keeps its emoji; a person does not.
COMPANY_HINT = re.compile(
    r"\b(inc|llc|ltd|corp|co|company|gmbh|plc|group|labs?|studios?|agency|bank|"
    r"hotel|cafe|shop|store|market|clinic|school|university|foundation|institute|"
    r"team|support|services?|solutions|systems|media|press|records|design|"
    r"consulting|capital|ventures|partners|holdings|airlines?|restaurant)\b", re.I)


def is_person(person, display):
    """People have birthdays, relations and job titles; companies rarely do."""
    if person.get("birthdays") or person.get("relations"):
        return True
    if any((o.get("title") or "").strip() for o in (person.get("organizations") or [])):
        return True
    plain = EMOJI.sub("", display or "").strip()
    if COMPANY_HINT.search(plain):
        return False
    n = (person.get("names") or [{}])[0]
    if (n.get("givenName") or "").strip() and (n.get("familyName") or "").strip():
        return True
    return len([w for w in re.findall(r"[A-Za-z]+", plain)]) >= 2


def split_name_part(value, strip_emoji=False):
    """-> (clean, suffix, prefix, pronouns) for one name field."""
    s = (value or "").strip()
    pref = ""
    if strip_emoji:
        s = EMOJI.sub(" ", s).strip()
    m = PREFIX.match(s)
    if m:
        pref = m.group(1).strip()
        s = s[m.end():].strip()
    pron = ""
    m = PRONOUNS.search(s)
    if m:
        pron = m.group(0).strip().strip("()")
        s = PRONOUNS.sub(" ", s).strip()
    suf = []
    while True:
        m = SUFFIX.search(s)
        if not m:
            break
        suf.insert(0, m.group(1).strip())
        s = s[:m.start()].strip().rstrip(",").strip()
    return re.sub(r"\s{2,}", " ", s).strip(), ", ".join(suf), pref, pron


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    tok = G.get_access_token()
    people, page = [], None
    while True:
        q = {"personFields": "names,metadata", "pageSize": 1000,
             "sources": "READ_SOURCE_TYPE_CONTACT"}
        if page:
            q["pageToken"] = page
        rq = urllib.request.Request(API + "/people/me/connections?" + urllib.parse.urlencode(q),
                                    headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(rq, timeout=60) as r:
            d = json.loads(r.read())
        people.extend(d.get("connections", []))
        page = d.get("nextPageToken")
        if not page:
            break

    plans, pronoun_notes = [], []
    for p in people:
        names = p.get("names") or []
        if not names:
            continue
        # unstructuredName is dropped, not carried: google re-parses it and
        # overrides whatever structured parts are sent alongside it.
        n = {k: v for k, v in names[0].items()
             if k not in ("metadata", "displayName", "displayNameLastFirst",
                          "unstructuredName")}
        changed = False
        got_suffix, got_pron, got_pref = [], [], []
        _display = names[0].get("displayName") or ""
        _strip_emoji = bool(EMOJI.search(
            "".join(str(n.get(f) or "") for f in
                    ("givenName", "familyName", "middleName")))) and is_person(p, _display)
        for field in ("familyName", "givenName", "middleName"):
            val = n.get(field)
            if not val:
                continue
            clean, suf, pref, pron = split_name_part(val, strip_emoji=_strip_emoji)
            if pref:
                got_pref.append(pref)
            if suf or pron or pref or clean != (val or "").strip():
                if not clean:
                    continue           # would empty the name; leave it alone
                n[field] = clean
                if suf:
                    got_suffix.append(suf)
                if pron:
                    got_pron.append(pron)
                changed = True
        if not changed:
            continue
        if got_suffix:
            have = (n.get("honorificSuffix") or "").strip()
            n["honorificSuffix"] = ", ".join([x for x in [have] + got_suffix if x])
        if got_pref:
            have = (n.get("honorificPrefix") or "").strip()
            n["honorificPrefix"] = ", ".join([x for x in [have] + got_pref if x])
        display = (names[0].get("displayName") or "?")
        plans.append((p, display, n, got_suffix + got_pref, got_pron))
        if got_pron:
            pronoun_notes.append((display, got_pron))

    print("  contacts with a suffix/pronoun inside a name field: %d" % len(plans))
    for _p, disp, n, suf, pron in plans:
        print("     %-30s -> given=%-12r family=%-14r suffix=%-10r %s"
              % (disp[:30], n.get("givenName"), n.get("familyName"),
                 n.get("honorificSuffix"), ("pronouns=%s" % pron) if pron else ""))
    if pronoun_notes:
        print("\n  pronouns found in a name field (removed from the name, NOT written")
        print("  to a custom field -- the People API has no pronouns field and weave")
        print("  already stores them as a fact):")
        for disp, pron in pronoun_notes:
            print("     %-30s %s" % (disp[:30], pron))

    if not a.apply:
        print("\ndry run; pass --apply to write")
        return

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    os.makedirs(AUDIT_DIR, exist_ok=True)
    audit = os.path.join(AUDIT_DIR, "name-suffix-%s.json" % ts)
    json.dump([{"resourceName": p["resourceName"], "display": disp,
                "before": {k: v for k, v in (p.get("names") or [{}])[0].items()
                           if k != "metadata"},
                "after": n} for p, disp, n, _s, _pr in plans],
              open(audit, "w"), indent=1)

    written = failed = 0
    for i in range(0, len(plans), 200):
        chunk = plans[i:i + 200]
        contacts = {p["resourceName"]: {"etag": p.get("etag"), "names": [n]}
                    for p, _d, n, _s, _pr in chunk}
        try:
            resp = G._api_post(API + "/people:batchUpdateContacts", tok,
                               {"contacts": contacts, "updateMask": "names",
                                "readMask": "names"}, timeout=120)
            for _rn, res in (resp.get("updateResult") or {}).items():
                if (res.get("status") or {}).get("code"):
                    failed += 1
                else:
                    written += 1
        except Exception as e:  # noqa: BLE001
            failed += len(chunk)
            print("  batch error: %s: %s" % (type(e).__name__, e))
        time.sleep(0.5)
    print("updated=%d failed=%d  audit=%s" % (written, failed, audit))


if __name__ == "__main__":
    main()
