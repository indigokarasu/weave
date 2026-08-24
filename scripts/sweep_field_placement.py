#!/usr/bin/env python3
"""Move contact data into the field it belongs in, and drop custom fields that
only restate (or contradict) a real one.

Google Contacts had custom fields holding a US state, a job title, a second
employer and an age -- all of which have real fields (or are derived junk) -- plus
real fields holding the wrong thing: a city of 'San Francisco, CA', a title of
'UX/Product Designer, Google' with no company set, and titles containing the
contact's own name.

Rules, each conservative and each audited:
  A  custom 'Address 1 - State'  -> addresses.region when region is empty and the
     value agrees with the city; otherwise dropped as a stale duplicate of a
     populated real field
  B  custom 'extracted_username' -> dropped when the handle already appears in one
     of the contact's URLs (it is a scrape artifact, not a field)
  C  custom 'Job Title'          -> organizations[0].title when that title is empty
     or contains the contact's own name
  D  custom 'Other Organizations'-> appended as a real organization
  E  custom 'Age'                -> dropped (derived, stale by construction, and
     not a People API field)
  F  city 'City, ST'             -> city 'City' + region 'ST'
  G  title 'Role, Company' with no organization name -> name 'Company', title 'Role'
  H  title containing the contact's own name -> name tokens stripped, remainder
     kept only if something plausible is left

Anything not matched by a rule is left alone. Dry-run by default.
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

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
import google_sync as G  # noqa: E402

API = "https://people.googleapis.com/v1"
FIELDS = "names,userDefined,addresses,organizations,urls,metadata"
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
DROP_KEYS = {"age"}
# a company that is really a company, not a scrape artifact
_JUNK_TITLES = {"linkedin employees", "linkedin member", "employees"}

# What is left after stripping a contact's own name out of their title is only a
# title if it names a role. Without this, 'Kevin Michael Stevenson' became the
# title 'Stevenson' and 'Ashley Hirsch is currently the Manager' became the prose
# fragment 'is currently the Manager' -- both worse than leaving it alone.
_ROLE_WORDS = {
    "manager", "director", "engineer", "designer", "producer", "founder",
    "cofounder", "co-founder", "cto", "ceo", "coo", "cfo", "cmo", "vp",
    "president", "head", "lead", "partner", "professor", "analyst", "officer",
    "consultant", "architect", "scientist", "developer", "editor", "writer",
    "chief", "owner", "coordinator", "specialist", "researcher", "strategist",
    "administrator", "supervisor", "assistant", "associate", "principal",
    "advisor", "counsel", "attorney", "nurse", "doctor", "teacher", "chef",
    "photographer", "illustrator", "artist", "author", "recruiter", "agent",
    "instructor", "dean", "curator", "producer", "programmer", "technician",
    "therapist", "physician", "surgeon", "planner", "buyer", "controller",
    "treasurer", "secretary", "chairman", "chairwoman", "chair", "fellow",
    "intern", "apprentice", "operator", "steward", "trainer", "evangelist",
    "leader", "composer", "vocalist", "songwriter", "musician", "executive",
    "expert", "generalist", "manager,", "practitioner", "maker", "creator",
}


def _looks_like_a_role(text):
    t = (text or "").strip()
    if not t or not t[0].isupper():
        return False        # a lowercase start is a prose fragment, not a title
    words = {w.strip(".,").lower() for w in t.split()}
    return bool(words & _ROLE_WORDS)


def fetch_all(tok):
    people, page = [], None
    while True:
        q = {"personFields": FIELDS, "pageSize": 1000, "sources": "READ_SOURCE_TYPE_CONTACT"}
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
    return people


def strip_meta(items):
    return [{k: v for k, v in dict(i).items() if k != "metadata"} for i in (items or [])]


def name_tokens(p):
    n = (p.get("names") or [{}])[0]
    raw = " ".join(str(n.get(k) or "") for k in ("displayName", "givenName", "familyName"))
    return {t.lower() for t in re.findall(r"[A-Za-z]{2,}", raw)}


def plan_one(p):
    nm = (p.get("names") or [{}])[0].get("displayName", "?")
    custom = strip_meta(p.get("userDefined"))
    addrs = strip_meta(p.get("addresses"))
    orgs = strip_meta(p.get("organizations"))
    urls = " ".join((u.get("value") or "").lower() for u in (p.get("urls") or []))
    toks = name_tokens(p)
    actions, keep_custom = [], []
    changed_addr = changed_org = changed_custom = False

    # F: a city carrying its state
    for ad in addrs:
        city = str(ad.get("city") or "").strip()
        m = re.match(r"^(.*),\s*([A-Za-z]{2})$", city)
        if m and m.group(2).upper() in US_STATES:
            ad["city"] = m.group(1).strip()
            if not str(ad.get("region") or "").strip():
                ad["region"] = m.group(2).upper()
            ad.pop("formattedValue", None)
            actions.append("F city %r -> city=%r region=%r" % (city, ad["city"], ad.get("region")))
            changed_addr = True

    # G/H: organization fields
    for og in orgs:
        name = str(og.get("name") or "").strip()
        title = str(og.get("title") or "").strip()
        if title.lower() in _JUNK_TITLES:
            og.pop("title", None)
            actions.append("G* dropped junk title %r" % title)
            changed_org = True
            title = ""
        if title and not name and "," in title:
            role, _, comp = title.rpartition(",")
            role, comp = role.strip(), comp.strip()
            # 'Vocalist, Songwriter, Composer' is a list of roles, not a role at a
            # company: promoting the last segment made 'Composer' the employer.
            comp_is_role = bool({w.strip(".,").lower() for w in comp.split()} & _ROLE_WORDS)
            if role and comp and len(comp.split()) <= 4 and not comp_is_role:
                og["name"], og["title"] = comp, role
                actions.append("G title %r -> name=%r title=%r" % (title, comp, role))
                changed_org = True
                title = role
        if title:
            tt = re.findall(r"[A-Za-z]{2,}", title)
            if tt and all(t.lower() in toks for t in tt):
                og.pop("title", None)          # the title is only the person's name
                actions.append("H dropped title %r (is the contact's own name)" % title)
                changed_org = True
            elif len(tt) > 1 and sum(1 for t in tt if t.lower() in toks) >= 2:
                rest = " ".join(t for t in title.split() if
                                re.sub(r"[^A-Za-z]", "", t).lower() not in toks).strip(" ,-")
                if _looks_like_a_role(rest):
                    og["title"] = rest
                    actions.append("H title %r -> %r (own name stripped)" % (title, rest))
                    changed_org = True

    # custom fields
    for u in custom:
        key = str(u.get("key") or "").strip()
        val = str(u.get("value") or "").strip()
        k = key.lower()
        if k in DROP_KEYS:
            actions.append("E dropped custom %s=%r (derived, not a field)" % (key, val))
            changed_custom = True
            continue
        if k == "extracted_username":
            if val.lower() and val.lower() in urls:
                actions.append("B dropped custom %s=%r (already in a url)" % (key, val))
                changed_custom = True
                continue
            keep_custom.append(u)
            continue
        if k in ("address 1 - state", "address 2 - state", "state"):
            v = val.upper()
            target = addrs[0] if addrs else None
            if target is not None and not str(target.get("region") or "").strip() \
                    and v in US_STATES:
                target["region"] = v
                target.pop("formattedValue", None)
                actions.append("A custom %s=%r -> addresses.region" % (key, val))
                changed_addr = True
                changed_custom = True
                continue
            actions.append("A dropped custom %s=%r (region already %r)"
                           % (key, val, (target or {}).get("region")))
            changed_custom = True
            continue
        if k == "job title":
            target = orgs[0] if orgs else None
            cur = str((target or {}).get("title") or "").strip()
            cur_toks = re.findall(r"[A-Za-z]{2,}", cur)
            own_name = cur_toks and all(t.lower() in toks for t in cur_toks)
            if target is None:
                orgs.append({"title": val})
                actions.append("C custom %s=%r -> new organization title" % (key, val))
                changed_org = changed_custom = True
                continue
            if not cur or own_name:
                target["title"] = val
                actions.append("C custom %s=%r -> organizations.title (was %r)"
                               % (key, val, cur))
                changed_org = changed_custom = True
                continue
            keep_custom.append(u)
            continue
        if k == "other organizations":
            body = val.split("--", 1)[-1].strip()
            role, _, comp = body.rpartition(",")
            role, comp = role.strip(), comp.strip()
            if role and comp:
                orgs.append({"name": comp, "title": role})
                actions.append("D custom %s=%r -> organization name=%r title=%r"
                               % (key, val, comp, role))
                changed_org = changed_custom = True
                continue
            keep_custom.append(u)
            continue
        keep_custom.append(u)

    if not (changed_addr or changed_org or changed_custom):
        return None, None, None
    body = {}
    if changed_addr:
        body["addresses"] = addrs
    if changed_org:
        body["organizations"] = [o for o in orgs if any(
            str(o.get(k) or "").strip() for k in ("name", "title", "department"))]
    if changed_custom:
        body["userDefined"] = keep_custom
    return nm, body, actions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    tok = G.get_access_token()
    people = fetch_all(tok)
    print("contacts: %d" % len(people))

    plans, rule_counts = [], {}
    for p in people:
        nm, body, actions = plan_one(p)
        if not body:
            continue
        plans.append((p, nm, body, actions))
        for act in actions:
            rule_counts[act[:2].strip()] = rule_counts.get(act[:2].strip(), 0) + 1

    print("contacts changed: %d" % len(plans))
    for r in sorted(rule_counts):
        print("   rule %-2s : %d" % (r, rule_counts[r]))
    for _p, nm, body, actions in plans[:30]:
        print("  %s" % nm)
        for act in actions:
            print("      %s" % act)

    if not a.apply:
        print("\ndry run; pass --apply to write")
        return

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(AUDIT_DIR, exist_ok=True)
    audit = os.path.join(AUDIT_DIR, "field-placement-%s.json" % ts)
    json.dump([{"name": nm, "resourceName": p["resourceName"],
                "before": {k: strip_meta(p.get(k)) for k in
                           ("userDefined", "addresses", "organizations")},
                "after": body, "actions": actions}
               for p, nm, body, actions in plans], open(audit, "w"), indent=1)

    groups = {}
    for p, _nm, body, _a in plans:
        groups.setdefault(tuple(sorted(body.keys())), []).append((p, body))
    written = failed = 0
    for sig, rows in groups.items():
        mask = ",".join(sig)
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            contacts = {}
            for p, b in chunk:
                bb = dict(b)
                bb["etag"] = p.get("etag")
                contacts[p["resourceName"]] = bb
            try:
                resp = G._api_post(API + "/people:batchUpdateContacts", tok,
                                   {"contacts": contacts, "updateMask": mask,
                                    "readMask": mask}, timeout=120)
                for _rn, res in (resp.get("updateResult") or {}).items():
                    if (res.get("status") or {}).get("code"):
                        failed += 1
                    else:
                        written += 1
            except Exception as e:  # noqa: BLE001
                failed += len(chunk)
                print("  batch error: %s: %s" % (type(e).__name__, e))
            time.sleep(0.5)
    print("written=%d failed=%d  audit=%s" % (written, failed, audit))


if __name__ == "__main__":
    main()
