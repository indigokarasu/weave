#!/usr/bin/env python3
import os as _os
_PROF = _os.environ.get("HERMES_HOME") or _os.path.join(
    _os.path.expanduser("~"), ".hermes", "profiles", "indigo")
import os
"""
Classify Google contacts as companies (not people), then:
  1. add them to a Google contact group ("label") named 'company'
  2. mirror that as a weave tag (book_tags / book_contact_tags)
  3. move the company name out of the name fields into the organization field

Dry run by default. Pass --apply to write.

Only contacts with NO familyName are considered, and each must fire at least one
positive rule. Rules are reported per contact so every call is auditable.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS = f"{_PROF}/skills/ocas-weave/scripts"
sys.path.insert(0, SCRIPTS)

WORK = Path(os.path.join(os.path.expanduser("~"), "work", "company-label"))
PEOPLE_CACHE = WORK / "people.json"
DB = f"{_PROF}/commons/db/ocas-weave/weave.sqlite"
GROUP_NAME = "company"

MULTI_TLD = {"ac.uk", "co.uk", "org.uk", "com.au", "co.jp", "co.nz", "co.in",
             "com.br", "co.za", "gov.uk", "ne.jp", "or.jp"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def prim(items, key="value"):
    if not items:
        return ""
    for it in items:
        if (it.get("metadata") or {}).get("primary"):
            return it.get(key) or ""
    return items[0].get(key) or ""


def nm(p):
    return (p.get("names") or [{}])[0]


def disp(p):
    return (nm(p).get("displayName") or "").strip()


def given(p):
    return (nm(p).get("givenName") or "").strip()


def family(p):
    return (nm(p).get("familyName") or "").strip()


def org_name(p):
    return prim(p.get("organizations"), "name").strip()


def org_title(p):
    return prim(p.get("organizations"), "title").strip()


def email(p):
    return prim(p.get("emailAddresses")).strip()


def domain_root(addr):
    if "@" not in (addr or ""):
        return ""
    dom = addr.split("@", 1)[1].lower().strip()
    parts = dom.split(".")
    if len(parts) < 2:
        return ""
    if ".".join(parts[-2:]) in MULTI_TLD and len(parts) >= 3:
        return parts[-3]
    return parts[-2]


def local_part(addr):
    return (addr or "").split("@", 1)[0].lower().strip()


ROLE_LOCALS = {
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply", "info",
    "support", "contact", "hello", "sales", "help", "admin", "service",
    "services", "team", "mail", "notifications", "notification", "alerts",
    "billing", "care", "customer", "orders", "reservations", "bookings",
    "office", "hi", "inquiries", "enquiries", "webmaster", "postmaster",
}

CORP_TOKENS = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "bank", "credit",
    "union", "airlines", "airways", "services", "service", "cleaning",
    "cleaners", "plaza", "parking", "hauling", "salon", "dental", "insurance",
    "realty", "clinic", "hospital", "pharmacy", "restaurant", "cafe",
    "plumbing", "electric", "electrical", "hvac", "roofing", "landscaping",
    "solutions", "systems", "agency", "technologies", "storage", "furniture",
    "framing", "laundry", "veterinary", "florist", "bakery", "garage",
    "towing", "movers", "moving", "repair", "motors", "apartments",
    "properties", "management", "academy", "gmbh", "plc", "coop",
}

TLD_SUFFIX = re.compile(r"\.(com|net|org|io|co|ai|app|inc)$", re.I)

# Brands the operator named in the task as companies already in the data.
OPERATOR_BRANDS = {norm(x) for x in
                   ["PayPal", "Venmo", "Citibank", "Doordash", "OpenTable",
                    "Resy", "AlphaSights", "Toast", "Ramp"]}

# Caught by eyeballing all 53 candidates: obviously not a person, but the record
# carries zero supporting evidence. Never auto-applied -- forced to borderline.
OPERATOR_REVIEW = {norm("Google Voice")}


def classify(p):
    """Return (verdict, rules, notes). verdict in company|person|borderline|skip."""
    rules, notes = [], []
    has_names = bool(p.get("names"))
    g, f, d, o = given(p), family(p), disp(p), org_name(p)
    e = email(p)
    nphones = len(p.get("phoneNumbers") or [])

    if f:
        return "skip", [], ["has familyName"]

    # ---- Population A: no name at all, identity carried by the org field.
    if not has_names:
        if not o:
            return "borderline", [], ["no name and no org -- empty shell"]
        if nphones > 5:
            return "borderline", ["A-many-phones"], [
                "%d phone numbers on one record -- an aggregation, not one business"
                % nphones]
        if e and local_part(e) not in ROLE_LOCALS:
            return "person", ["A-personal-email"], [
                "email %s has a personal local-part -- a person whose name was "
                "never entered" % e]
        return "company", ["A-org-only-no-name"], [
            "already nameless; org=%r carries the identity" % o]

    # ---- Population B: givenName only.
    if not g:
        return "borderline", [], ["names block present but givenName empty"]

    if re.search(r"\s@\s", g):
        return "person", ["B-person-at-business"], [
            "name is '<person> @ <business>' -- a person, not the business"]
    if "@" in g:
        return "person", ["B-name-is-email"], [
            "name field holds an email address -- a person with a mangled name"]

    ng, no_ = norm(g), norm(o)

    if o and no_ == ng:
        rules.append("B-org-equals-name")
    if o and len(no_) >= 4 and len(ng) >= 4 and (no_ in ng or ng in no_) \
            and no_ != ng:
        rules.append("B-org-substring-of-name")
    dr = domain_root(e)
    if dr and len(dr) >= 3 and (dr == ng or dr in ng or ng in dr):
        rules.append("B-email-domain-matches-name")
    if e and local_part(e) in ROLE_LOCALS:
        rules.append("B-role-email")
    toks = {t for t in re.split(r"[^a-z0-9]+", g.lower()) if t}
    hit = toks & CORP_TOKENS
    if hit:
        rules.append("B-corp-keyword(%s)" % ",".join(sorted(hit)))
    if TLD_SUFFIX.search(g):
        rules.append("B-name-has-tld")
    if ng in OPERATOR_BRANDS:
        rules.append("B-operator-brand-list")

    if ng in OPERATOR_REVIEW:
        return "borderline", rules, [
            "flagged by manual review: not a person, but the record carries no "
            "evidence a rule can key on"]

    # A role email alone is weak (people have shared inboxes); needs company.
    strong = [r for r in rules if r != "B-role-email"]
    if strong:
        return "company", rules, []
    if rules:
        return "borderline", rules, ["only a weak signal fired"]
    return "person", [], ["no company evidence in the record"]


# ---------------------------------------------------------------- google io

def _get(url, token, tries=6):
    from google_api import api_get
    backoff = 5.0
    for i in range(tries):
        try:
            return api_get(url, token, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and i < tries - 1:
                print("    HTTP %d -> sleep %.0fs" % (e.code, backoff))
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            raise


def _post(url, token, body, tries=6):
    from google_api import api_post
    backoff = 5.0
    for i in range(tries):
        try:
            return api_post(url, token, body, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and i < tries - 1:
                print("    HTTP %d -> sleep %.0fs" % (e.code, backoff))
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            raise


def _patch(url, token, body, tries=6):
    from google_api import api_patch
    backoff = 5.0
    for i in range(tries):
        try:
            return api_patch(url, token, body, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and i < tries - 1:
                print("    HTTP %d -> sleep %.0fs" % (e.code, backoff))
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            raise


def fresh_people(rns, token, base):
    """Current etag + names/organizations for each resourceName (batchGet, 200/req)."""
    out = {}
    for i in range(0, len(rns), 200):
        chunk = rns[i:i + 200]
        q = "&".join("resourceNames=%s" % urllib.parse.quote(r) for r in chunk)
        url = ("%s/people:batchGet?%s&personFields=names,organizations,memberships"
               % (base, q))
        d = _get(url, token)
        for r in d.get("responses", []):
            p = r.get("person") or {}
            if p.get("resourceName"):
                out[p["resourceName"]] = p
        time.sleep(1.0)
    return out


def ensure_group(token, base, apply_):
    groups = _get("%s/contactGroups?pageSize=200" % base, token).get(
        "contactGroups", [])
    for g in groups:
        if (g.get("name") or "").strip().lower() == GROUP_NAME:
            print("  group %r already exists: %s" % (GROUP_NAME, g["resourceName"]))
            return g["resourceName"]
    print("  group %r does NOT exist -- would create it" % GROUP_NAME)
    if not apply_:
        return None
    g = _post("%s/contactGroups" % base, token,
              {"contactGroup": {"name": GROUP_NAME}})
    print("  created group %s" % g["resourceName"])
    return g["resourceName"]


# ---------------------------------------------------------------- weave

def weave_rows(conn):
    cur = conn.execute(
        "SELECT id, name, name_given, name_family, org, occupation, email,"
        "       google_resource_name, is_pseudo, is_archived, is_deceased,"
        "       valid_until FROM persons")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def ensure_tag(conn, apply_):
    row = conn.execute(
        "SELECT id FROM book_tags WHERE lower(name)=?", (GROUP_NAME,)).fetchone()
    if row:
        print("  weave tag %r exists: %s" % (GROUP_NAME, row[0]))
        return row[0]
    print("  weave tag %r does NOT exist -- would create it" % GROUP_NAME)
    if not apply_:
        return None
    tid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO book_tags(id,name,color_index,is_system,"
                 "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                 (tid, GROUP_NAME, 4, 0, now, now))
    print("  created weave tag %s" % tid)
    return tid


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write")
    ap.add_argument("--limit", type=int, default=0,
                    help="apply to at most N contacts (for a canary run)")
    args = ap.parse_args()

    from google_api import get_access_token, PEOPLE_API_BASE

    people = json.loads(PEOPLE_CACHE.read_text())
    conn = sqlite3.connect(DB)
    wrows = weave_rows(conn)
    by_rn = {}
    for w in wrows:
        rn = (w["google_resource_name"] or "").strip()
        if rn:
            by_rn.setdefault(rn, []).append(w)

    buckets = {"company": [], "person": [], "borderline": [], "skip": []}
    for p in people:
        v, rules, notes = classify(p)
        buckets[v].append((p, rules, notes))

    print("=" * 96)
    print("CLASSIFICATION over %d Google contacts   (candidate pool = the %d with "
          "no familyName)" % (len(people), len(people) - len(buckets["skip"])))
    print("  companies : %d" % len(buckets["company"]))
    print("  people    : %d no-familyName + %d with a familyName (never considered)"
          % (len(buckets["person"]), len(buckets["skip"])))
    print("  borderline: %d" % len(buckets["borderline"]))
    print("=" * 96)

    plan = []
    for p, rules, notes in sorted(buckets["company"],
                                  key=lambda t: (disp(t[0]) or org_name(t[0])).lower()):
        cur_name, cur_org, cur_title = disp(p), org_name(p), org_title(p)
        target_org = cur_org if not cur_name else cur_name
        acts = ["+label"]
        if cur_name:
            acts.append("clear-name")
        if norm(target_org) != norm(cur_org):
            acts.append("set-org")
        if cur_title:
            acts.append("clear-title")
        plan.append({"p": p, "rn": p.get("resourceName"), "cur_name": cur_name,
                     "cur_org": cur_org, "cur_title": cur_title,
                     "target_org": target_org, "acts": acts, "rules": rules,
                     "notes": notes, "weave": by_rn.get(p.get("resourceName"), [])})

    print("\n### (a) COMPANIES: %d\n" % len(plan))
    print("%-3s %-30s %-27s %-27s %-16s %s"
          % ("#", "GOOGLE NAME NOW", "GOOGLE ORG NOW", "ORG AFTER", "TITLE NOW", "ACTIONS"))
    print("-" * 145)
    for i, e in enumerate(plan, 1):
        print("%-3d %-30s %-27s %-27s %-16s %s"
              % (i, (e["cur_name"] or "(no name)")[:30], (e["cur_org"] or "-")[:27],
                 e["target_org"][:27], (e["cur_title"] or "-")[:16],
                 ",".join(e["acts"])))
        print("     rules: %s" % ("; ".join(e["rules"]) or "-"))
        for w in e["weave"]:
            wacts = []
            if (w["name_given"] or w["name_family"]):
                wacts.append("clear name_given/name_family")
            if norm(w["org"] or "") != norm(e["target_org"]):
                wacts.append("org %r -> %r" % (w["org"], e["target_org"]))
            if w["occupation"]:
                wacts.append("clear occupation %r" % w["occupation"])
            wacts.append("tag 'company'")
            print("     weave %s: %s" % (w["id"][:8], "; ".join(wacts)))
        if not e["weave"]:
            print("     weave: NO ROW (this contact was never imported into weave)")

    print("\n\n### (c) BORDERLINE -- NOT touched, decide these\n")
    for p, rules, notes in buckets["borderline"]:
        print("  name=%-22s org=%-22s title=%-20s email=%-26s phones=%d"
              % (repr(disp(p) or "(none)")[:22], repr(org_name(p))[:22],
                 repr(org_title(p))[:20], repr(email(p))[:26],
                 len(p.get("phoneNumbers") or [])))
        print("      %s" % "; ".join(notes))

    print("\n### no-familyName contacts kept as PEOPLE (%d)\n" % len(buckets["person"]))
    for p, rules, notes in sorted(buckets["person"],
                                  key=lambda t: (disp(t[0]) or org_name(t[0])).lower()):
        print("  %-30s org=%-26s title=%-20s %s"
              % ((disp(p) or "(no name)")[:30], (org_name(p) or "-")[:26],
                 (org_title(p) or "-")[:20], "; ".join(notes)))

    # ---- weave-only companies (no Google counterpart)
    wonly = []
    for w in wrows:
        if (w["google_resource_name"] or "").strip():
            continue
        if w["is_pseudo"] or w["is_archived"] or w["is_deceased"]:
            continue
        n = norm(w["name_given"] or w["name"])
        toks = {t for t in re.split(r"[^a-z0-9]+", (w["name"] or "").lower()) if t}
        if n in OPERATOR_BRANDS or (toks & CORP_TOKENS):
            wonly.append(w)
    print("\n\n### WEAVE-ONLY companies (no Google contact exists) -- %d\n" % len(wonly))
    for w in wonly:
        print("  id=%s name=%-20r given=%-16r family=%-8r org=%-14r  -> tag "
              "'company', set org=%r (names left alone: creating these in Google "
              "was not requested)"
              % (w["id"][:8], w["name"], w["name_given"], w["name_family"],
                 w["org"], w["name_given"] or w["name"]))

    json.dump([{k: v for k, v in e.items() if k not in ("p", "weave")}
               | {"weave_ids": [w["id"] for w in e["weave"]]} for e in plan],
              open(WORK / "plan.json", "w"), indent=1)

    # ------------------------------------------------------------ apply
    print("\n" + "=" * 96)
    token = get_access_token()
    base = PEOPLE_API_BASE
    print("GROUP / TAG PRE-FLIGHT")
    gid = ensure_group(token, base, args.apply)
    tid = ensure_tag(conn, args.apply)

    if not args.apply:
        print("\nDRY RUN -- nothing written to Google or to weave.")
        print("Re-run with --apply (optionally --limit N for a canary) to write.")
        conn.close()
        return

    todo = plan[:args.limit] if args.limit else plan
    print("\nAPPLYING to %d contacts" % len(todo))

    # 1. Google: names + organizations, one PATCH per contact (multi-field mask).
    rns = [e["rn"] for e in todo]
    live = fresh_people(rns, token, base)
    ok, failed = 0, []
    for e in todo:
        rn = e["rn"]
        cur = live.get(rn)
        if not cur:
            failed.append((rn, "not returned by batchGet"))
            continue
        body = {"etag": cur.get("etag"),
                "names": [],
                "organizations": [{"name": e["target_org"]}]}
        url = "%s/%s:updateContact?updatePersonFields=names,organizations" % (base, rn)
        try:
            res = _patch(url, token, body)
            ok += 1
            print("  ok  %-28s org=%r" % (e["cur_name"] or e["cur_org"], e["target_org"]))
        except urllib.error.HTTPError as ex:
            detail = ""
            try:
                detail = ex.read().decode()[:300]
            except Exception:
                pass
            if ex.code == 400 and "recondition" in detail:
                try:
                    f2 = fresh_people([rn], token, base)
                    body["etag"] = f2[rn]["etag"]
                    _patch(url, token, body)
                    ok += 1
                    print("  ok(retry) %-22s" % (e["cur_name"] or e["cur_org"]))
                    continue
                except Exception as ex2:
                    detail += " | retry: %s" % ex2
            failed.append((rn, "%s %s" % (ex.code, detail)))
            print("  FAIL %-27s %s %s" % (e["cur_name"] or e["cur_org"], ex.code, detail))
        time.sleep(0.6)

    print("\n  google field updates: %d ok, %d failed" % (ok, len(failed)))

    # 2. Google: group membership (1000 per request).
    for i in range(0, len(rns), 900):
        _post("%s/%s/members:modify" % (base, gid), token,
              {"resourceNamesToAdd": rns[i:i + 900]})
    print("  added %d contacts to %s" % (len(rns), gid))

    # 3. weave mirror.
    now = datetime.now(timezone.utc).isoformat()
    nw = 0
    for e in todo:
        for w in e["weave"]:
            conn.execute(
                "UPDATE persons SET name=?, name_given=NULL, name_family=NULL,"
                " org=?, occupation=NULL WHERE id=?",
                (e["target_org"], e["target_org"], w["id"]))
            conn.execute("INSERT OR IGNORE INTO book_contact_tags(contact_id,"
                         "tag_id,created_at) VALUES(?,?,?)", (w["id"], tid, now))
            nw += 1
    # weave-only rows are a whole-set decision, not part of a canary slice
    for w in (wonly if not args.limit else []):
        conn.execute("UPDATE persons SET org=? WHERE id=?",
                     (w["name_given"] or w["name"], w["id"]))
        conn.execute("INSERT OR IGNORE INTO book_contact_tags(contact_id,tag_id,"
                     "created_at) VALUES(?,?,?)", (w["id"], tid, now))
        nw += 1
    conn.commit()
    print("  weave: %d rows tagged/updated" % nw)
    conn.close()


if __name__ == "__main__":
    main()
