#!/usr/bin/env python3
"""Merge every weave person group that is a duplicate by construction.

A group is certain when two or more rows carry the SAME google_resource_name:
google holds one contact, weave split it into several. Nothing is inferred from
name similarity here -- a shared name between two real people is exactly the
mistake this pipeline has made before.

Also merges groups of is_pseudo relation stubs that share a name and have no
google contact: those exist only to hang a relation edge on, and two stubs for
one relative just split the edges.

Winner: the row with the most live facts, then the one carrying an email, then
the oldest. The loser's data is merged in fill-empty, so the survivor never ends
up with less than either row had.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
from merge_persons import merge  # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"


def live_facts(con, pid):
    return con.execute(
        "SELECT COUNT(*) FROM facts f JOIN edges e ON e.target_id=f.id "
        "AND e.rel_type='HasFact' WHERE e.source_id=? AND f.valid_until IS NULL",
        (pid,)).fetchone()[0]


def rank(con, r):
    return (live_facts(con, r["id"]),
            1 if (r["email"] or "").strip() else 0,
            1 if (r["phone"] or "").strip() else 0,
            -(len(r["record_time"] or "")),          # older rows sort first on ties
            )


def groups(con, include_pseudo=True):
    out = []
    for g in con.execute(
            "SELECT google_resource_name rn FROM persons "
            "WHERE google_resource_name IS NOT NULL AND google_resource_name != '' "
            "GROUP BY rn HAVING COUNT(*) > 1"):
        rows = con.execute("SELECT * FROM persons WHERE google_resource_name=?",
                           (g["rn"],)).fetchall()
        out.append(("same google contact %s" % g["rn"], rows))
    if include_pseudo:
        for g in con.execute(
                "SELECT LOWER(name) k FROM persons WHERE is_pseudo=1 "
                "AND (google_resource_name IS NULL OR google_resource_name='') "
                "AND name IS NOT NULL AND name != '' "
                "GROUP BY k HAVING COUNT(*) > 1"):
            rows = con.execute(
                "SELECT * FROM persons WHERE LOWER(name)=? AND is_pseudo=1 "
                "AND (google_resource_name IS NULL OR google_resource_name='')",
                (g["k"],)).fetchall()
            out.append(("duplicate relation stub %r" % rows[0]["name"], rows))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA foreign_keys=ON")

    gs = groups(con)
    print("groups to merge: %d covering %d rows\n"
          % (len(gs), sum(len(r) for _w, r in gs)))
    plan = []
    for why, rows in gs:
        ranked = sorted(rows, key=lambda r: rank(con, r), reverse=True)
        w, losers = ranked[0], ranked[1:]
        plan.append((why, w, losers))
        print("  %s" % why)
        print("     KEEP  %s %-26s facts=%d" % (w["id"][:8], (w["name"] or "?")[:26],
                                                live_facts(con, w["id"])))
        for l in losers:
            print("     merge %s %-26s facts=%d" % (l["id"][:8], (l["name"] or "?")[:26],
                                                    live_facts(con, l["id"])))

    if not a.apply:
        print("\ndry run; pass --apply to write")
        return

    done, failed, audits = 0, 0, []
    for why, w, losers in plan:
        for l in losers:
            try:
                audit, _d = merge(con, w["id"], l["id"], apply=True)
                audit["reason"] = why
                audits.append(audit)
                done += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("  FAILED %s <- %s: %s" % (w["id"][:8], l["id"][:8], e))

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    os.makedirs(AUDIT_DIR, exist_ok=True)
    p = os.path.join(AUDIT_DIR, "person-merge-batch-%s.json" % ts)
    json.dump(audits, open(p, "w"), indent=1, default=str)
    print("\nmerged=%d failed=%d\naudit: %s" % (done, failed, p))

    print("VERIFY resource names with >1 row: %d (want 0)" % con.execute(
        "SELECT COUNT(*) FROM (SELECT google_resource_name, COUNT(*) n FROM persons "
        "WHERE google_resource_name IS NOT NULL AND google_resource_name != '' "
        "GROUP BY 1 HAVING n>1)").fetchone()[0])
    print("VERIFY persons total            : %d" % con.execute(
        "SELECT COUNT(*) FROM persons").fetchone()[0])
    orph = 0
    for t, c in (("edges", "source_id"), ("edges", "target_id"),
                 ("node_properties", "node_id"), ("enrichment_meta", "person_id")):
        orph += con.execute(
            "SELECT COUNT(*) FROM %s WHERE %s NOT IN (SELECT id FROM persons)"
            % (t, c)).fetchone()[0]
    print("VERIFY orphaned references      : %d (want 0)" % orph)


if __name__ == "__main__":
    main()
