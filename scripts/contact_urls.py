#!/usr/bin/env python3
"""Import hand-curated URLs/biographies from Google Contacts into weave.

Google's People API already returns `urls` and `biographies`; google_sync.py
requests them and then never reads them, so 158 LinkedIn URLs the user entered
by hand never reach weave.sqlite. This module recovers them.

Design notes (each earned from a reproduced defect):
  * Idempotency is enforced by BEGIN IMMEDIATE around check-then-insert, so two
    concurrent syncs serialize instead of both inserting. The live store already
    carries 275 duplicate (person,predicate,value) groups from unguarded
    check-then-write elsewhere; this path does not add to them.
  * URL fragments are PRESERVED. Some sites route identity through the fragment
    (rdio.com/#/people/nettatheninja); stripping it deletes the identity and
    collapses two different people onto one value.
  * Nothing is ever deleted, overwritten, or superseded here. Curated contact
    data is additive only. Existing non-empty person columns are left alone.
  * Confidence 0.95: the user typed these by hand, which outranks any OSINT
    inference this pipeline can make.
"""
import json
import re
import sqlite3
import urllib.parse
import uuid
from datetime import datetime, timezone

# one canonical form for every path that reads or writes a contact URL
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from url_norm import canonical_url, dedupe_key as _dedupe_key  # noqa: E402

FACT_SOURCE_TYPE = "google_contacts"
FACT_CONFIDENCE = 0.95

# host suffix -> fact predicate
_PLATFORMS = [
    ("linkedin.com", "profile_linkedin"),
    ("github.com", "profile_github"),
    ("twitter.com", "profile_twitter"),
    ("x.com", "profile_twitter"),
    ("bsky.app", "profile_bluesky"),
    ("instagram.com", "profile_instagram"),
    ("facebook.com", "profile_facebook"),
    ("pinterest.com", "profile_pinterest"),
    ("medium.com", "profile_medium"),
    ("behance.net", "profile_behance"),
    ("dribbble.com", "profile_dribbble"),
    ("youtube.com", "profile_youtube"),
    ("tiktok.com", "profile_tiktok"),
    # Payment handles. These fell through to "website", so a paypal.me link
    # became the contact's personal website -- and `website` is a field that
    # syncs into the real address book. A way to pay someone is not their site.
    ("paypal.com", "profile_paypal"),
    ("paypal.me", "profile_paypal"),
    ("venmo.com", "profile_venmo"),
    ("cash.app", "profile_cashapp"),
]

# Non-profile paths that are site sections, not people.
_GITHUB_RESERVED = {
    "features", "about", "orgs", "topics", "marketplace", "sponsors", "blog",
    "pricing", "site", "search", "login", "join", "contact", "security",
    "explore", "trending", "collections", "events", "apps", "settings",
    "enterprise", "team", "readme", "new", "notifications", "pulls", "issues",
}
_TWITTER_RESERVED = {
    "share", "intent", "home", "search", "hashtag", "i", "status", "login",
    "signup", "explore", "privacy", "tos", "settings", "messages", "compose",
}
_BAD_SCHEMES = ("mailto:", "javascript:", "tel:", "data:", "file:")


def classify_url(raw):
    """(predicate, canonical_url) for a contact URL, or (None, None) to reject.

    predicate is 'linkedin' / 'profile_<platform>' / 'website'.
    Canonicalization lowercases the host and drops 'www.', but PRESERVES path,
    query and fragment — those can carry the identity.
    """
    canonical = canonical_url(raw)
    if not canonical:
        return None, None
    p = urllib.parse.urlsplit(canonical)
    host = p.netloc
    path = p.path.rstrip("/")

    for suffix, predicate in _PLATFORMS:
        if host == suffix or host.endswith("." + suffix):
            segs = [s for s in path.split("/") if s]
            if predicate == "profile_linkedin":
                # Only personal profiles. /company/, /school/, /pub/dir/ are not people.
                if len(segs) >= 2 and segs[0] == "in":
                    return "profile_linkedin", canonical
                return None, None
            if predicate == "profile_github":
                if len(segs) == 1 and segs[0].lower() not in _GITHUB_RESERVED:
                    return predicate, canonical
                return None, None
            if predicate == "profile_twitter":
                if len(segs) == 1 and segs[0].lower() not in _TWITTER_RESERVED:
                    return predicate, canonical
                return None, None
            if not segs:
                return None, None
            return predicate, canonical

    # Anything else is a generic personal/professional website — but not a
    # search engine result or similar noise.
    if host in ("google.com", "bing.com", "duckduckgo.com") and p.query:
        return None, None
    return "website", canonical


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def extract_signals(person):
    """Google person dict -> {'facts': [(predicate, value)], 'website': str|None}."""
    facts, website = [], None
    seen = set()
    for u in (person.get("urls") or []):
        predicate, canonical = classify_url(u.get("value"))
        if not predicate:
            continue
        key = (predicate, canonical)
        if key in seen:
            continue
        seen.add(key)
        facts.append(key)
        if predicate == "website" and website is None:
            website = canonical
    for b in (person.get("biographies") or []):
        text = (b.get("value") or "").strip()
        if text:
            facts.append(("bio_summary", text))
            break
    return {"facts": facts, "website": website}


def store_signals(con, person_id, signals, dry_run=False):
    """Write facts for one person. Idempotent: identical (person,predicate,value)
    is never inserted twice, enforced under a write lock so concurrent syncs
    serialize rather than racing."""
    stats = {"written": 0, "existing": 0, "website_filled": 0}
    if not signals["facts"] and not signals["website"]:
        return stats

    if dry_run:
        existing = {(r["predicate"], r["value"]) for r in con.execute(
            "SELECT f.predicate, f.value FROM facts f JOIN edges e ON e.target_id=f.id "
            "WHERE e.source_id=? AND e.rel_type='HasFact'", (person_id,))}
        for pv in signals["facts"]:
            if pv in existing:
                stats["existing"] += 1
            else:
                stats["written"] += 1
        if signals["website"]:
            row = con.execute("SELECT website FROM persons WHERE id=?", (person_id,)).fetchone()
            if row is not None and not (row["website"] or "").strip():
                stats["website_filled"] = 1
        return stats

    con.execute("BEGIN IMMEDIATE")           # serialize check-then-write
    try:
        existing = {(r["predicate"], r["value"]) for r in con.execute(
            "SELECT f.predicate, f.value FROM facts f JOIN edges e ON e.target_id=f.id "
            "WHERE e.source_id=? AND e.rel_type='HasFact'", (person_id,))}
        # A URL this pipeline pushed to google comes back on the next inbound and
        # would be stored AGAIN as `website`, sourced google_contacts -- relabelling
        # our own guess as owner-curated data. Skip any url already held under a
        # profile_* predicate for this person.
        _held = set()
        for _r in con.execute(
                "SELECT f.value FROM facts f JOIN edges e ON e.target_id=f.id "
                "WHERE e.source_id=? AND e.rel_type='HasFact' "
                "AND f.predicate LIKE 'profile\\_%' ESCAPE '\\'", (person_id,)):
            _k = _dedupe_key(_r["value"])
            if _k:
                _held.add(_k)
        now = _now()
        for predicate, value in signals["facts"]:
            if (predicate, value) in existing:
                stats["existing"] += 1
                continue
            if predicate == "website" and _dedupe_key(value) in _held:
                stats["existing"] += 1      # already held as a profile_* url
                continue
            fid = str(uuid.uuid4())
            con.execute(
                "INSERT INTO facts (id, predicate, value, confidence, source_type, "
                "source_ref, record_time) VALUES (?,?,?,?,?,?,?)",
                (fid, predicate, value, FACT_CONFIDENCE, FACT_SOURCE_TYPE,
                 "google_contacts", now))
            con.execute(
                "INSERT INTO edges (id, source_id, target_id, rel_type, confidence, "
                "record_time) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), person_id, fid, "HasFact", FACT_CONFIDENCE, now))
            existing.add((predicate, value))
            stats["written"] += 1

        if signals["website"]:
            row = con.execute("SELECT website FROM persons WHERE id=?", (person_id,)).fetchone()
            if row is not None and not (row["website"] or "").strip():
                con.execute("UPDATE persons SET website=? WHERE id=?",
                            (signals["website"], person_id))
                stats["website_filled"] = 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return stats


def import_all(people, db_path, dry_run=False):
    """people: list of Google person dicts (needs resourceName, urls, biographies)."""
    con = _connect(db_path)
    by_rn = {r["google_resource_name"]: r["id"] for r in con.execute(
        "SELECT id, google_resource_name FROM persons "
        "WHERE google_resource_name IS NOT NULL AND google_resource_name != ''")}
    totals = {"matched": 0, "unmatched": 0, "written": 0, "existing": 0,
              "website_filled": 0, "by_predicate": {}}
    for p in people:
        pid = by_rn.get(p.get("resourceName"))
        if not pid:
            if p.get("urls") or p.get("biographies"):
                totals["unmatched"] += 1
            continue
        sig = extract_signals(p)
        if not sig["facts"] and not sig["website"]:
            continue
        totals["matched"] += 1
        st = store_signals(con, pid, sig, dry_run=dry_run)
        totals["written"] += st["written"]
        totals["existing"] += st["existing"]
        totals["website_filled"] += st["website_filled"]
        for predicate, _v in sig["facts"]:
            totals["by_predicate"][predicate] = totals["by_predicate"].get(predicate, 0) + 1
    con.close()
    return totals


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--people-json", help="file of cached Google people (else fetch live)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.people_json:
        people = json.load(open(a.people_json))
    else:
        sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
        import urllib.request
        import google_sync as G
        tok = G.get_access_token()
        people, page = [], None
        while True:
            q = {"personFields": "names,urls,biographies", "pageSize": 1000,
                 "sources": "READ_SOURCE_TYPE_CONTACT"}
            if page:
                q["pageToken"] = page
            rq = urllib.request.Request(
                "https://people.googleapis.com/v1/people/me/connections?"
                + urllib.parse.urlencode(q),
                headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(rq, timeout=30) as r:
                d = json.loads(r.read())
            people.extend(d.get("connections", []))
            page = d.get("nextPageToken")
            if not page:
                break

    print(json.dumps(import_all(people, a.db, dry_run=a.dry_run), indent=1))
