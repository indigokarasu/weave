"""Integrity check that knows which table each edge type points at.

Twice now a check that assumed one target table produced an alarming number:
HasFact edges counted against persons "found" 39,154 orphans, and HasPreference
edges counted against persons "found" 155 dead relations that are in fact all
valid -- the preferences table holds exactly 155 rows. Map each rel_type to the
table it actually references.
"""
import collections
import sqlite3

con = sqlite3.connect("/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
con.row_factory = sqlite3.Row

TARGET_TABLE = {"HasFact": "facts", "HasPreference": "preferences"}
DEFAULT_TARGET = "persons"

print("  edges by rel_type, and whether their target resolves:")
total_bad = 0
for r in con.execute("SELECT rel_type, COUNT(*) n FROM edges GROUP BY rel_type "
                     "ORDER BY n DESC"):
    rt = r["rel_type"]
    tbl = TARGET_TABLE.get(rt, DEFAULT_TARGET)
    bad = con.execute(
        "SELECT COUNT(*) FROM edges WHERE rel_type=? "
        "AND target_id NOT IN (SELECT id FROM %s)" % tbl, (rt,)).fetchone()[0]
    total_bad += bad
    print("     %-16s %6d  -> %-12s unresolved: %d%s"
          % (rt, r["n"], tbl, bad, "   <== dead" if bad else ""))
print("  total unresolved edge targets: %d" % total_bad)

bad_src = con.execute("SELECT COUNT(*) FROM edges WHERE source_id NOT IN "
                      "(SELECT id FROM persons)").fetchone()[0]
print("  edges whose SOURCE is not a person: %d" % bad_src)

print("\n  store summary:")
for label, sql in (
        ("persons", "SELECT COUNT(*) FROM persons"),
        ("live facts", "SELECT COUNT(*) FROM facts WHERE valid_until IS NULL"),
        ("retired facts", "SELECT COUNT(*) FROM facts WHERE valid_until IS NOT NULL"),
        ("edges", "SELECT COUNT(*) FROM edges"),
        ("preferences", "SELECT COUNT(*) FROM preferences"),
        ("duplicate live facts",
         "SELECT COUNT(*) FROM (SELECT e.source_id,f.predicate,f.value,COUNT(*) n "
         "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
         "WHERE f.valid_until IS NULL GROUP BY 1,2,3 HAVING n>1)"),
        ("google ids on >1 row",
         "SELECT COUNT(*) FROM (SELECT google_resource_name,COUNT(*) n FROM persons "
         "WHERE google_resource_name IS NOT NULL AND google_resource_name!='' "
         "GROUP BY 1 HAVING n>1)")):
    print("     %-22s %d" % (label, con.execute(sql).fetchone()[0]))

print("  foreign key violations: %d" % len(con.execute("PRAGMA foreign_key_check").fetchall()))
