#!/usr/bin/env python3
"""Stop treating a person's NAME as their identifier.

persons.id is a UUID on all 978 rows, so the core identity is sound. Two
name-derived identifiers are not:

  persons.slug          derived from the name, so a rename silently changes a
                        person's address. 55 slugs no longer match their name,
                        including ones my own cleanup renamed
                        (sean-ketchem-phd, boris-chang-jr, laith-ulaby-4f606101).
  the merge trail       book_merge_event and book_contact_redirect exist for
                        exactly this and are EMPTY -- my 25 merges recorded
                        nothing there, so anything holding a merged-away id has
                        no way to find the survivor.

Fixes:
  1. merge_persons.py writes a merge event and a redirect row for every merge.
  2. Backfill both tables from the merge audit files already on disk.
  3. Keep a stale slug as an alias so an old link still resolves to the person.

A slug is an ADDRESS, not an identity: it may change, and every address a person
ever had should keep pointing at them.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

# ---- 1. teach the merge tool to record the trail
P = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts/merge_persons.py")
s = open(P).read()
if "book_contact_redirect" in s:
    print("  1. merge tool already records the trail")
else:
    anchor = """        con.execute("DELETE FROM persons WHERE id=?", (loser_id,))"""
    assert s.count(anchor) == 1, "delete anchor %d" % s.count(anchor)
    s = s.replace(anchor, '''        # Record the trail BEFORE the row goes away. Anything still holding the
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
''' + anchor)
    if a.apply:
        open(P, "w").write(s)
    print("  1. merge tool now writes book_merge_event + book_contact_redirect")

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
now = datetime.now(timezone.utc).isoformat()

# ---- 2. backfill the trail from the audits already written
pairs = {}
for f in sorted(glob.glob(os.path.join(AUDIT_DIR, "person-merge*.json")),
                key=os.path.getmtime):
    try:
        d = json.load(open(f))
    except Exception:  # noqa: BLE001
        continue
    for e in (d if isinstance(d, list) else [d]):
        try:
            pairs[e["loser"]["id"]] = (e["winner"]["id"], e.get("run_at") or now)
        except Exception:  # noqa: BLE001
            continue
# follow chains: a loser whose winner was itself later merged
resolved = {}
for loser, (winner, when) in pairs.items():
    seen, w = {loser}, winner
    while w in pairs and w not in seen:
        seen.add(w)
        w = pairs[w][0]
    resolved[loser] = (w, when)
alive = {r["id"] for r in con.execute("SELECT id FROM persons")}
backfill = [(l, w, t) for l, (w, t) in resolved.items()
            if l not in alive and w in alive]
print("  2. merge pairs recoverable from audits: %d (backfillable: %d)"
      % (len(resolved), len(backfill)))

# ---- 3. stale slugs -> keep as aliases
stale = []
for r in con.execute("SELECT id, name, slug FROM persons "
                     "WHERE slug IS NOT NULL AND slug != ''"):
    want = re.sub(r"[^a-z0-9]+", "-", (r["name"] or "").lower()).strip("-")
    if want and r["slug"] != want:
        stale.append((r["id"], r["name"], r["slug"], want))
have_alias = {r["slug"] for r in con.execute("SELECT slug FROM book_slug_aliases")}
new_alias = [(sid, nm, old, want) for sid, nm, old, want in stale
             if old not in have_alias]
print("  3. slugs that no longer match the name: %d (aliases to add: %d)"
      % (len(stale), len(new_alias)))
for _i, nm, old, want in new_alias[:8]:
    print("       %-26s keep %-34r as an alias; current would be %r"
          % (str(nm)[:26], old, want))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

con.execute("BEGIN IMMEDIATE")
try:
    for loser, winner, when in backfill:
        ev = str(uuid.uuid4())
        con.execute("INSERT INTO book_merge_event (id, destination_contact_id, "
                    "source_contact_ids, merge_reason, confidence, initiated_by, "
                    "created_at) VALUES (?,?,?,?,?,?,?)",
                    (ev, winner, json.dumps([loser]),
                     "duplicate person row (backfilled from audit)", 1.0,
                     "fix_identity.py", when))
        con.execute("INSERT OR REPLACE INTO book_contact_redirect "
                    "(source_contact_id, destination_contact_id, merge_event_id, "
                    "created_at, updated_at) VALUES (?,?,?,?,?)",
                    (loser, winner, ev, when, now))
    for sid, _nm, old, _want in new_alias:
        con.execute("INSERT OR IGNORE INTO book_slug_aliases (slug, contact_id, "
                    "is_primary, created_at) VALUES (?,?,?,?)", (old, sid, 0, now))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("  applied")
print("VERIFY book_merge_event rows    : %d" % con.execute(
    "SELECT COUNT(*) FROM book_merge_event").fetchone()[0])
print("VERIFY book_contact_redirect    : %d" % con.execute(
    "SELECT COUNT(*) FROM book_contact_redirect").fetchone()[0])
print("VERIFY redirects to a live person: %d" % con.execute(
    "SELECT COUNT(*) FROM book_contact_redirect WHERE destination_contact_id IN "
    "(SELECT id FROM persons)").fetchone()[0])
print("VERIFY slug aliases             : %d" % con.execute(
    "SELECT COUNT(*) FROM book_slug_aliases").fetchone()[0])
