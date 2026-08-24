#!/usr/bin/env python3
import os
"""Where is a person's NAME acting as their identifier?

A name is not an identity: two people share one, one person changes theirs, and a
merge has to pick a survivor. Anywhere a name is the key, all three break.
Enumerate the places and show what is already broken because of it.
"""
import collections
import re
import sqlite3

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=" * 76)
print("1. persons.id and the primary keys -- are they name-derived?")
print("=" * 76)
rows = con.execute("SELECT id, name, slug FROM persons LIMIT 6").fetchall()
for r in rows:
    print("   id=%-38s slug=%-24r name=%r" % (r["id"], r["slug"], r["name"]))
uuidish = sum(1 for r in con.execute("SELECT id FROM persons")
              if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                              r"[0-9a-f]{4}-[0-9a-f]{12}", r["id"] or ""))
total = con.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
print("   persons.id that are UUIDs: %d of %d  %s"
      % (uuidish, total, "(good)" if uuidish == total else "(some are NOT uuids)"))

print("\n" + "=" * 76)
print("2. persons.slug -- name-derived, and used as a key?")
print("=" * 76)
slugs = [r for r in con.execute("SELECT id, name, slug FROM persons "
                                "WHERE slug IS NOT NULL AND slug != ''")]
print("   rows with a slug: %d of %d" % (len(slugs), total))
dupe = collections.Counter(r["slug"] for r in slugs)
coll = {s: n for s, n in dupe.items() if n > 1}
print("   slug collisions (two people, one slug): %d" % len(coll))
for s, n in list(coll.items())[:6]:
    who = [r["name"] for r in slugs if r["slug"] == s]
    print("      %-30s x%d  %s" % (s, n, who))
mismatch = [r for r in slugs
            if re.sub(r"[^a-z0-9]", "", (r["slug"] or "").lower())
            != re.sub(r"[^a-z0-9]", "", (r["name"] or "").lower())]
print("   slugs that no longer match the current name: %d" % len(mismatch))
for r in mismatch[:8]:
    print("      %-26s slug=%r" % (str(r["name"])[:26], r["slug"]))

print("\n" + "=" * 76)
print("3. tables keyed by slug rather than by id")
print("=" * 76)
for t in ("book_slug_aliases", "book_contact_redirect"):
    try:
        cols = [c["name"] for c in con.execute("PRAGMA table_info(%s)" % t)]
        n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        print("   %-24s cols=%s rows=%d" % (t, cols, n))
        for r in con.execute("SELECT * FROM %s LIMIT 4" % t):
            print("      %s" % dict(r))
    except sqlite3.OperationalError as e:
        print("   %-24s %s" % (t, e))

print("\n" + "=" * 76)
print("4. aliases pointing at people who no longer exist (broken by merges)")
print("=" * 76)
try:
    dead = con.execute(
        "SELECT * FROM book_slug_aliases WHERE contact_id NOT IN "
        "(SELECT id FROM persons)").fetchall()
    print("   aliases whose contact is gone: %d" % len(dead))
    for r in dead[:6]:
        print("      %s" % dict(r))
    orphan_slug = con.execute(
        "SELECT p.slug, p.name FROM persons p WHERE p.slug IS NOT NULL "
        "AND p.slug != '' AND p.slug NOT IN (SELECT slug FROM book_slug_aliases)"
    ).fetchall()
    print("   live slugs with no alias row (fine, but the alias table is partial): %d"
          % len(orphan_slug))
except sqlite3.OperationalError as e:
    print("   %s" % e)

print("\n" + "=" * 76)
print("5. code paths that resolve a person BY NAME")
print("=" * 76)
print("""   contact_extras.resolve_person   relations name people by name, so a
                                   relation to a duplicated name is refused
   google_sync._norm_name          the create-path dedupe key IS the name; it
                                   already created a duplicate contact when a
                                   rename changed the key
   weave_enrich name matching      scout corroboration is name-based by design""")
amb = con.execute(
    "SELECT LOWER(name) k, COUNT(*) n FROM persons WHERE name IS NOT NULL "
    "AND name != '' GROUP BY k HAVING n > 1").fetchall()
print("   names currently held by more than one person: %d" % len(amb))
for r in amb[:6]:
    print("      %-30s x%d" % (r["k"], r["n"]))
