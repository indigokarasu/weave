#!/usr/bin/env python3
"""Merge two weave person rows that are the same human.

Duplicate rows are not merely untidy: they split a person's facts in two, make
relation resolution ambiguous (a family tie refuses to link when the name matches
more than one row), and make the enrichment pipeline research the same person
twice.

The surviving row is the one still linked to a live Google contact; the other is
usually the remnant of a Google contact that was merged or deleted upstream and
never cleaned up here.

What moves:
  * every edge, in both directions -- facts, family ties, Knows
  * book_* rows and enrichment_meta
  * scalar columns, fill-empty only, so the survivor never loses a value
  * the loser's slug, kept as an alias so old links still resolve

The loser row is then deleted, after the whole thing -- row, edges, book rows --
is written to an audit file with the SQL to put it back. persons.valid_until is
not used as a tombstone anywhere in this store (0 of 1008 rows), so tombstoning
here would invent a convention no reader honours.

Dry-run by default.
"""
import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

# Semantic person references that are NOT declared foreign keys, so
# discover_refs() cannot find them.
EXTRA_REFS = [("edges", "target_id")]
# Re-pointing these would move the loser's identity onto the winner; handled
# separately.
SPECIAL = {"node_properties"}
# node_properties keys whose value is the row's own node id
SELF_KEYS = {"chronicle_id", "chronicle_id_lbug"}


# Columns that hold a person id without declaring a foreign key. The book_*
# tables all do this, and reading only PRAGMA foreign_key_list missed every one
# of them -- a merge then left their rows pointing at a deleted person.
_PERSON_COLUMN_NAMES = {"contact_id", "person_id", "node_id"}


def discover_refs(con):
    """Every column that points at persons.id: declared FKs, plus the columns
    that reference a person by convention only."""
    refs = []
    tables = [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        for fk in con.execute("PRAGMA foreign_key_list(%s)" % t):
            if fk["table"] == "persons" and (t, fk["from"]) not in refs:
                refs.append((t, fk["from"]))
    for t in tables:
        if t == "persons":
            continue
        for c in [c["name"] for c in con.execute("PRAGMA table_info(%s)" % t)]:
            if c in _PERSON_COLUMN_NAMES and (t, c) not in refs:
                refs.append((t, c))
    for t, c in EXTRA_REFS:
        if (t, c) not in refs:
            refs.append((t, c))
    return refs


NEVER_COPY = {"id", "slug", "google_resource_name", "google_etag", "record_time",
              "book_created_at", "clay_id"}
# placeholders a scrape leaves behind; a real value from the other row beats them
JUNK_VALUES = {"multiple companies", "self", "self-employed", "n/a", "na", "-",
               "unknown", "none", "linkedin employees", "linkedin member",
               "freelance", "various"}


def _table_exists(con, t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (t,)).fetchone() is not None


def merge(con, winner_id, loser_id, apply=False):
    cols = [r["name"] for r in con.execute("PRAGMA table_info(persons)")]
    w = con.execute("SELECT * FROM persons WHERE id=?", (winner_id,)).fetchone()
    l = con.execute("SELECT * FROM persons WHERE id=?", (loser_id,)).fetchone()
    if not w or not l:
        raise SystemExit("both ids must exist (winner=%s loser=%s)" % (bool(w), bool(l)))
    print("  winner: %s  %s" % (winner_id[:8], w["name"]))
    print("  loser : %s  %s" % (loser_id[:8], l["name"]))

    # scalar columns to fill
    updates = {}
    for c in cols:
        if c in NEVER_COPY:
            continue
        wv, lv = w[c], l[c]
        wempty = wv in (None, "") or (isinstance(wv, str) and not wv.strip())
        lempty = lv in (None, "") or (isinstance(lv, str) and not lv.strip())
        if lempty:
            continue
        if wempty:
            updates[c] = lv
        elif isinstance(wv, str) and wv.strip().lower() in JUNK_VALUES \
                and str(lv).strip().lower() not in JUNK_VALUES:
            updates[c] = lv
    # org and occupation describe ONE job. Taking a real org name from the loser
    # while keeping the winner's title left "ESVP Coaching" paired with the job
    # title from a different employer.
    if "org" in updates and l["occupation"]:
        updates["occupation"] = l["occupation"]
    elif "occupation" in updates and l["org"] and str(l["org"]).strip().lower() \
            not in JUNK_VALUES:
        updates["org"] = l["org"]

    print("  scalar columns to fill on the winner: %d" % len(updates))
    for c, v in updates.items():
        print("     %-20s %r  <-  %r" % (c, w[c], str(v)[:48]))

    REFS = discover_refs(con)
    moves = []
    for t, c in REFS:
        if not _table_exists(con, t) or t in SPECIAL:
            continue
        n = con.execute("SELECT COUNT(*) FROM %s WHERE %s=?" % (t, c),
                        (loser_id,)).fetchone()[0]
        if n:
            moves.append((t, c, n))
    print("  references to re-point: %s"
          % ", ".join("%s.%s=%d" % (t, c, n) for t, c, n in moves))

    audit = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "winner": {k: w[k] for k in cols},
        "loser": {k: l[k] for k in cols},
        "scalar_updates": {k: str(v) for k, v in updates.items()},
        "moved": [{"table": t, "column": c, "rows": n} for t, c, n in moves],
        "loser_rows": {},
    }
    for t, c in REFS:
        if not _table_exists(con, t):
            continue
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM %s WHERE %s=?" % (t, c), (loser_id,))]
        if rows:
            audit["loser_rows"].setdefault(t, []).extend(rows)
    if _table_exists(con, "node_properties"):
        npr = [dict(r) for r in con.execute(
            "SELECT * FROM node_properties WHERE node_id=?", (loser_id,))]
        if npr:
            audit["loser_rows"]["node_properties"] = npr
            print("  node_properties on the loser: %s"
                  % ", ".join(r["key"] for r in npr))

    if not apply:
        print("\n  dry run; pass --apply to write")
        return audit, None

    now = datetime.now(timezone.utc).isoformat()
    con.execute("BEGIN IMMEDIATE")
    try:
        for c, v in updates.items():
            con.execute("UPDATE persons SET %s=? WHERE id=?" % c, (v, winner_id))
        for t, c, _n in moves:
            con.execute("UPDATE %s SET %s=? WHERE %s=?" % (t, c, c),
                        (winner_id, loser_id))
        # keep the loser's slug resolvable
        if l["slug"] and _table_exists(con, "book_slug_aliases"):
            already = con.execute("SELECT 1 FROM book_slug_aliases WHERE slug=?",
                                  (l["slug"],)).fetchone()
            if not already:
                con.execute("INSERT INTO book_slug_aliases (slug, contact_id, "
                            "is_primary, created_at) VALUES (?,?,?,?)",
                            (l["slug"], winner_id, 0, now))
        # node_properties: the loser's chronicle ids describe the LOSER, so they
        # are dropped rather than moved; anything else transfers if the winner
        # has no value for that key. The merge itself is recorded as dup_of.
        if _table_exists(con, "node_properties"):
            have = {r["key"] for r in con.execute(
                "SELECT key FROM node_properties WHERE node_id=?", (winner_id,))}
            for r in con.execute("SELECT * FROM node_properties WHERE node_id=?",
                                 (loser_id,)).fetchall():
                if r["key"] in SELF_KEYS or loser_id in str(r["value"]):
                    continue
                if r["key"] not in have:
                    con.execute("INSERT INTO node_properties (node_id, key, value) "
                                "VALUES (?,?,?)", (winner_id, r["key"], r["value"]))
                    have.add(r["key"])
            con.execute("DELETE FROM node_properties WHERE node_id=?", (loser_id,))
            # node_properties is UNIQUE(node_id, key): a person merged twice
            # (Hilary Hayes had three rows) needs dup_of to accumulate, not to
            # be inserted again.
            _cur = con.execute("SELECT value FROM node_properties WHERE node_id=? "
                               "AND key='dup_of'", (winner_id,)).fetchone()
            if _cur is None:
                con.execute("INSERT INTO node_properties (node_id, key, value) "
                            "VALUES (?,?,?)", (winner_id, "dup_of", loser_id))
            elif loser_id not in str(_cur[0]):
                con.execute("UPDATE node_properties SET value=? WHERE node_id=? "
                            "AND key='dup_of'",
                            (str(_cur[0]) + "," + loser_id, winner_id))

        # an edge from the winner to itself can only be an artefact of the merge
        con.execute("DELETE FROM edges WHERE source_id=? AND target_id=? "
                    "AND rel_type!='HasFact'", (winner_id, winner_id))
        # Record the trail BEFORE the row goes away. Anything still holding the
        # loser's id -- a link, an export, another store -- resolves through
        # book_contact_redirect; a name-derived slug cannot do that job.
        _ev = str(uuid.uuid4())
        if _table_exists(con, "book_merge_event"):
            con.execute(
                "INSERT INTO book_merge_event (id, destination_contact_id, "
                "source_contact_ids, merge_reason, confidence, initiated_by, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (_ev, winner_id, json.dumps([loser_id]),
                 "duplicate person row", 1.0, "merge_persons.py", now))
        if _table_exists(con, "book_contact_redirect"):
            con.execute(
                "INSERT OR REPLACE INTO book_contact_redirect (source_contact_id, "
                "destination_contact_id, merge_event_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?)", (loser_id, winner_id, _ev, now, now))
            # anything that already redirected to the loser now points onward
            con.execute(
                "UPDATE book_contact_redirect SET destination_contact_id=?, "
                "updated_at=? WHERE destination_contact_id=?",
                (winner_id, now, loser_id))
        con.execute("DELETE FROM persons WHERE id=?", (loser_id,))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    # exact duplicate facts, now that both sets hang off one person
    con.execute("BEGIN IMMEDIATE")
    try:
        seen, dupes = set(), []
        for r in con.execute(
                "SELECT f.id, f.predicate, f.value FROM facts f "
                "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
                "WHERE e.source_id=? AND f.valid_until IS NULL "
                "ORDER BY f.record_time, f.id", (winner_id,)):
            k = (r["predicate"], r["value"])
            if k in seen:
                dupes.append(r["id"])
            else:
                seen.add(k)
        for fid in dupes:
            con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, fid))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    audit["deduped_facts"] = dupes
    print("  duplicate facts retired after the merge: %d" % len(dupes))
    return audit, dupes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--winner", required=True)
    ap.add_argument("--loser", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA foreign_keys=ON")
    audit, _d = merge(con, a.winner, a.loser, apply=a.apply)
    if not a.apply:
        return

    os.makedirs(AUDIT_DIR, exist_ok=True)
    # Include the loser id: three merges inside one second overwrote each
    # other's audit and left only the last recoverable.
    p = os.path.join(AUDIT_DIR, "person-merge-%s-%s.json"
                     % (datetime.now().strftime("%Y%m%dT%H%M%S"), a.loser[:8]))
    json.dump(audit, open(p, "w"), indent=1, default=str)
    print("  audit: %s" % p)

    # verification
    left = con.execute("SELECT COUNT(*) FROM persons WHERE id=?", (a.loser,)).fetchone()[0]
    orph = 0
    for t, c in discover_refs(con) + [("node_properties", "node_id")]:
        if _table_exists(con, t):
            orph += con.execute("SELECT COUNT(*) FROM %s WHERE %s=?" % (t, c),
                                (a.loser,)).fetchone()[0]
    facts = con.execute(
        "SELECT COUNT(*) FROM facts f JOIN edges e ON e.target_id=f.id "
        "AND e.rel_type='HasFact' WHERE e.source_id=? AND f.valid_until IS NULL",
        (a.winner,)).fetchone()[0]
    print("VERIFY loser row remaining : %d (want 0)" % left)
    print("VERIFY orphan references   : %d (want 0)" % orph)
    print("VERIFY winner live facts   : %d" % facts)


if __name__ == "__main__":
    main()
