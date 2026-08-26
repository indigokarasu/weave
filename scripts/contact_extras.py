#!/usr/bin/env python3
"""Import the Google Contacts fields weave was fetching and discarding.

sync_inbound already asked the People API for birthdays, relations and
biographies, then only ever read the scalar columns, so 201 birthdays and 38
relations the owner typed by hand never reached weave. Same defect as the URLs
(contact_urls.py): requested, parsed, dropped.

Everything here is additive and fill-only. Curated contact data outranks
anything the enrichment pipeline can infer, but it must never overwrite a value
already in weave -- the owner may have corrected it there.

Relation direction, since it is easy to get backwards: a relation on contact P
naming Q describes Q's role relative to P. So {person: Q, type: 'child'} means Q
is P's child, which is the edge ParentOf(P -> Q).

A related person is only linked when their name resolves to exactly ONE weave
person (accent-insensitively). 25 names in this book are held by more than one
row, and guessing which one would attach a family tie to a stranger. Unresolved
relations are still recorded, as a fact naming the person.
"""
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, f"{_PROF}/skills/ocas-scout/scripts")
from _normalize import fold_accents  # noqa: E402

FACT_SOURCE_TYPE = "google_contacts"
FACT_CONFIDENCE = 0.95

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
_MONTH_NAMES = {v: k.capitalize() for k, v in _MONTHS.items()}

# google relation type (lowercased) -> (weave edge, direction, symmetric)
# direction 'down' = ParentOf(contact -> related), 'up' = ParentOf(related -> contact)
_EDGE_MAP = {
    "spouse": ("SpouseOf", None, True),
    "partner": ("SpouseOf", None, True),
    "husband": ("SpouseOf", None, True),
    "wife": ("SpouseOf", None, True),
    "domesticpartner": ("SpouseOf", None, True),
    "child": ("ParentOf", "down", False),
    "son": ("ParentOf", "down", False),
    "daughter": ("ParentOf", "down", False),
    "mother": ("ParentOf", "up", False),
    "father": ("ParentOf", "up", False),
    "parent": ("ParentOf", "up", False),
    "brother": ("SiblingOf", None, True),
    "sister": ("SiblingOf", None, True),
    "sibling": ("SiblingOf", None, True),
}
# recorded as a fact rather than a family edge
_FACT_RELATIONS = {"manager", "assistant", "friend", "referredby", "relative",
                   "dog", "cat", "pet"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def format_birthday(bd):
    """Google birthday dict -> the string weave stores, or None.

    Yearless birthdays are common (64 of google's 201) and must stay yearless:
    inventing a year would be a fact nobody asserted.
    """
    if not isinstance(bd, dict):
        return None
    d = bd.get("date") or {}
    month, day, year = d.get("month"), d.get("day"), d.get("year")
    if month and day:
        if year:
            return "%04d-%02d-%02d" % (int(year), int(month), int(day))
        return "%s %d" % (_MONTH_NAMES.get(int(month), ""), int(day))
    text = (bd.get("text") or "").strip()
    return text or None


def format_event_date(ev):
    d = (ev or {}).get("date") or {}
    if d.get("month") and d.get("day"):
        if d.get("year"):
            return "%04d-%02d-%02d" % (int(d["year"]), int(d["month"]), int(d["day"]))
        return "%s %d" % (_MONTH_NAMES.get(int(d["month"]), ""), int(d["day"]))
    return None


_TRAILING_DATE = re.compile(r"[\s,]+(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$")


def split_trailing_date(s):
    """'Isadora "Izzy" Arcuni 07/31/215' -> ('Isadora "Izzy" Arcuni', '2015-07-31').

    A related person is often typed into the address book with their birthday
    appended, because Google's relation field is a single free-text box. Left
    joined, the date becomes part of the name and no contact can ever match it.

    A three-digit year is a dropped zero -- '215' is 2015, not year 215 -- so it
    is repaired rather than discarded. Anything that still fails to make a real
    date leaves the name trimmed and the birthday unset, so a bad date costs the
    birthday, never the person.
    """
    m = _TRAILING_DATE.search(s or "")
    if not m:
        return (s or "").strip(), None
    name = (s[:m.start()]).strip(" ,;")
    mo, day, ytxt = int(m.group(1)), int(m.group(2)), m.group(3)
    year = int(ytxt)
    if len(ytxt) == 2:
        year = 2000 + year if year <= 30 else 1900 + year
    elif len(ytxt) == 3:
        year = int(ytxt[0] + "0" + ytxt[1:])
    if not (1900 <= year <= 2035 and 1 <= mo <= 12 and 1 <= day <= 31):
        return name, None
    return name, "%04d-%02d-%02d" % (year, mo, day)


def clean_related_name(raw):
    """'Avery Placeholder <someone@example.com>' -> ('Avery Placeholder', 'someone@example.com')."""
    s = (raw or "").strip()
    if not s:
        return None, None
    email = None
    m = re.search(r"<([^>]+@[^>]+)>", s)
    if m:
        email = m.group(1).strip().lower()
        s = s[:m.start()].strip()
    s = s.strip(" ,;")
    if not s or not re.search(r"[A-Za-z]", s):
        return None, email          # '???' and similar placeholders
    return s, email


def _norm_name(s):
    return re.sub(r"\s+", " ", fold_accents(s or "").strip()).lower()


def _ends(s):
    t = [x for x in _norm_name(s).split() if x]
    return (t[0], t[-1]) if len(t) >= 2 else None


def build_indexes(con, people=None):
    """Relations name people the way GOOGLE spells them, which is not always the
    way weave does -- 'Rachel Neurath' has a weave row (found by resourceName)
    under a different name, so resolving against persons.name alone missed her.
    Index google's display names too, and first+last as a last resort so a middle
    name ('Yelena Rubinshteyn Danziger') does not defeat the match."""
    by_rn, by_name, by_email, by_ends = {}, {}, {}, {}
    for r in con.execute("SELECT id, name, google_resource_name, email FROM persons"):
        if r["google_resource_name"]:
            by_rn[r["google_resource_name"]] = r["id"]
        n = _norm_name(r["name"])
        if n:
            by_name.setdefault(n, set()).add(r["id"])
        e = _ends(r["name"])
        if e:
            by_ends.setdefault(e, set()).add(r["id"])
        em = (r["email"] or "").strip().lower()
        if em:
            by_email.setdefault(em, set()).add(r["id"])
    for p in (people or []):
        pid = by_rn.get(p.get("resourceName"))
        if not pid:
            continue
        for nrec in (p.get("names") or []):
            for key in ("displayName", "unstructuredName"):
                gn = _norm_name(nrec.get(key))
                if gn:
                    by_name.setdefault(gn, set()).add(pid)
                    ge = _ends(nrec.get(key))
                    if ge:
                        by_ends.setdefault(ge, set()).add(pid)
        for em in (p.get("emailAddresses") or []):
            v = str(em.get("value") or "").strip().lower()
            if v:
                by_email.setdefault(v, set()).add(pid)
    return by_rn, by_name, by_email, by_ends


def resolve_person(name, email, by_name, by_email, by_ends):
    """A weave person id, but only when the answer is unambiguous."""
    if email:
        ids = by_email.get(email) or set()
        if len(ids) == 1:
            return next(iter(ids)), "email"
    ids = by_name.get(_norm_name(name)) or set()
    if len(ids) == 1:
        return next(iter(ids)), "name"
    if len(ids) > 1:
        return None, "ambiguous"
    e = _ends(name)
    ids = (by_ends.get(e) or set()) if e else set()
    if len(ids) == 1:
        return next(iter(ids)), "first+last"
    if len(ids) > 1:
        return None, "ambiguous"
    return None, "unknown"


def plan(people, con):
    by_rn, by_name, by_email, by_ends = build_indexes(con, people)
    have_fact = {}
    for r in con.execute(
            "SELECT e.source_id AS pid, f.predicate, f.value FROM facts f "
            "JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
            "WHERE f.valid_until IS NULL"):
        have_fact.setdefault(r["pid"], set()).add((r["predicate"], r["value"]))
    have_pred = {pid: {p for p, _v in s} for pid, s in have_fact.items()}
    have_edge = {(r["source_id"], r["target_id"], r["rel_type"])
                 for r in con.execute("SELECT source_id, target_id, rel_type FROM edges")}

    facts, edges, skipped = [], [], []

    creates = []
    for p in people:
        pid = by_rn.get(p.get("resourceName"))
        if not pid:
            continue
        nm = (p.get("names") or [{}])[0].get("displayName", "?")

        for bd in (p.get("birthdays") or []):
            v = format_birthday(bd)
            if not v:
                continue
            if "birthday" in have_pred.get(pid, set()):
                skipped.append((nm, "birthday", v, "weave already has one"))
                break
            facts.append((pid, nm, "birthday", v))
            have_pred.setdefault(pid, set()).add("birthday")
            break

        for ev in (p.get("events") or []):
            v = format_event_date(ev)
            t = (ev.get("type") or ev.get("formattedType") or "event").strip().lower()
            pred = re.sub(r"[^a-z0-9]+", "_", t) or "event"
            if not v or (pred, v) in have_fact.get(pid, set()):
                continue
            facts.append((pid, nm, pred, v))

        for rel in (p.get("relations") or []):
            rtype = (rel.get("type") or rel.get("formattedType") or "").strip()
            key = re.sub(r"[^a-z]", "", rtype.lower())
            who, email = clean_related_name(rel.get("person"))
            if not who:
                skipped.append((nm, rtype, str(rel.get("person")), "no usable name"))
                continue
            if key in _EDGE_MAP:
                edge, direction, symmetric = _EDGE_MAP[key]
                oid, how = resolve_person(who, email, by_name, by_email, by_ends)
                if not oid:
                    # Nobody in the address book matches. Storing the name as a
                    # bare string fact leaves the relationship as text on two
                    # records with no person to hang a birthday, an address or a
                    # later merge on -- which is how a daughter existed only as
                    # the words 'Isadora "Izzy" Arcuni 07/31/215' on her parents.
                    # Create her as a pseudo contact: a real row that relations
                    # can point at, marked is_pseudo so enrichment never
                    # researches her and she is not mistaken for a contact of
                    # the operator's own.
                    who_clean, who_bday = split_trailing_date(who)
                    if not who_clean:
                        skipped.append((nm, rtype, who, "no usable name after date"))
                        continue
                    oid, how = resolve_person(who_clean, email, by_name, by_email, by_ends)
                    if not oid:
                        oid = str(uuid.uuid4())
                        creates.append((oid, who_clean, who_bday, email))
                        by_name.setdefault(_norm_name(who_clean), set()).add(oid)
                        _e2 = _ends(who_clean)
                        if _e2:
                            by_ends.setdefault(_e2, set()).add(oid)
                        if email:
                            by_email.setdefault(email, set()).add(oid)
                        how = "created as pseudo contact"
                    who = who_clean
                    skipped.append((nm, rtype, who, how))
                if oid == pid:
                    skipped.append((nm, rtype, who, "resolves to the contact itself"))
                    continue
                pairs = ([(pid, oid), (oid, pid)] if symmetric else
                         [(pid, oid)] if direction == "down" else [(oid, pid)])
                labels = {pid: nm, oid: who}
                for a, b in pairs:
                    if (a, b, edge) not in have_edge:
                        edges.append((a, b, edge, labels.get(a, "?"),
                                      labels.get(b, "?"), rtype))
                        have_edge.add((a, b, edge))
            elif key in _FACT_RELATIONS or key:
                pred = re.sub(r"[^a-z0-9]+", "_", rtype.lower()) or "relation"
                if (pred, who) not in have_fact.get(pid, set()):
                    facts.append((pid, nm, pred, who))
    return facts, edges, skipped, creates


def import_all(people, db_path, dry_run=False):
    con = _connect(db_path)
    facts, edges, skipped, creates = plan(people, con)
    stats = {"facts": len(facts), "edges": len(edges), "skipped": len(skipped),
             "created_pseudo": len(creates),
             "by_predicate": {}}
    for _pid, _nm, pred, _v in facts:
        stats["by_predicate"][pred] = stats["by_predicate"].get(pred, 0) + 1
    if dry_run:
        con.close()
        return stats, facts, edges, skipped, creates
    now = _now()
    con.execute("BEGIN IMMEDIATE")
    try:
        for new_id, new_name, new_bday, new_email in creates:
            con.execute(
                "INSERT INTO persons (id, name, name_given, name_family, email, "
                "birthday, is_pseudo, source_type, source_ref, confidence, record_time) "
                "VALUES (?,?,?,?,?,?,1,?,?,?,?)",
                (new_id, new_name,
                 (new_name.split()[0] if new_name.split() else ""),
                 (new_name.split()[-1] if len(new_name.split()) > 1 else ""),
                 new_email or "", new_bday, "google_contacts",
                 "relation on a google contact", FACT_CONFIDENCE, now))
        for pid, _nm, pred, val in facts:
            fid = str(uuid.uuid4())
            con.execute("INSERT INTO facts (id, predicate, value, confidence, "
                        "source_type, source_ref, record_time) VALUES (?,?,?,?,?,?,?)",
                        (fid, pred, val, FACT_CONFIDENCE, FACT_SOURCE_TYPE,
                         "google_contacts", now))
            con.execute("INSERT INTO edges (id, source_id, target_id, rel_type, "
                        "confidence, record_time) VALUES (?,?,?,?,?,?)",
                        (str(uuid.uuid4()), pid, fid, "HasFact", FACT_CONFIDENCE, now))
        for a, b, rel, _n1, _n2, _rt in edges:
            con.execute("INSERT INTO edges (id, source_id, target_id, rel_type, "
                        "confidence, record_time) VALUES (?,?,?,?,?,?)",
                        (str(uuid.uuid4()), a, b, rel, FACT_CONFIDENCE, now))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    con.close()
    return stats, facts, edges, skipped, creates


if __name__ == "__main__":
    import argparse
    import urllib.parse
    import urllib.request

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=f"{_PROF}/commons/db/"
                                    "ocas-weave/weave.sqlite")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import google_sync as G

    tok = G.get_access_token()
    people, page = [], None
    while True:
        q = {"personFields": "names,birthdays,relations,events,metadata",
             "pageSize": 1000, "sources": "READ_SOURCE_TYPE_CONTACT"}
        if page:
            q["pageToken"] = page
        rq = urllib.request.Request(
            "https://people.googleapis.com/v1/people/me/connections?"
            + urllib.parse.urlencode(q), headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(rq, timeout=60) as r:
            d = json.loads(r.read())
        people.extend(d.get("connections", []))
        page = d.get("nextPageToken")
        if not page:
            break

    stats, facts, edges, skipped, creates = import_all(
        people, a.db, dry_run=not a.apply)
    print("google contacts: %d" % len(people))
    print("facts to write : %d  %s" % (stats["facts"], stats["by_predicate"]))
    print("edges to write : %d" % stats["edges"])
    print("not linked     : %d" % stats["skipped"])
    for pid, nm, pred, val in facts[:14]:
        print("   fact  %-22s %-14s %s" % (nm[:22], pred, val))
    for a_, b_, rel, n1, n2, rt in edges:
        print("   edge  %-22s --%-10s--> %-22s (google: %s)" % (n1[:22], rel, n2[:22], rt))
    for nm, rt, who, why in skipped:
        print("   skip  %-22s %-10s %-24s %s" % (nm[:22], rt, str(who)[:24], why))
    if not a.apply:
        print("\ndry run; pass --apply to write")
    print("  pseudo contacts created: %d" % len(creates))
    for _c in creates[:12]:
        print("     %-34s birthday=%s" % (_c[1][:34], _c[2]))
import os
_PROF = os.environ.get("HERMES_HOME",
                       os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo"))
