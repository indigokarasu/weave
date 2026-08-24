#!/usr/bin/env python3
"""Bring weave's url facts to the same canonical form as google's.

Two facts holding the same link in different spellings are two facts, so the
enrichment pipeline treats them as independent corroboration and the outbound
push sends both. Rewrite each url fact to canonical form; where that makes two
facts identical, retire the later one with valid_until (never DELETE -- consumers
filter on valid_until IS NULL and the audit trail is the only way back).

A url fact whose value is not a URL at all (an email in a website field) is
retired too, and recorded in the audit file.
"""
import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from url_norm import canonical_url, dedupe_key  # noqa: E402

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
# timestamped: a second run has nothing to retire, so a fixed name would
# overwrite the first run' audit and lose the only record of what was retired
AUDIT_DIR = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave")
SQL = ("SELECT f.id, f.predicate, f.value, e.source_id AS pid, f.record_time "
       "FROM facts f JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
       "WHERE f.valid_until IS NULL AND ("
       "f.predicate LIKE 'profile\\_%' ESCAPE '\\' "
       "OR f.predicate IN ('linkedin', 'website')) "
       "ORDER BY f.record_time")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    rows = con.execute(SQL).fetchall()
    print("url facts: %d" % len(rows))

    rewrites, retire_dup, retire_junk = [], [], []
    seen = {}
    for r in rows:
        c = canonical_url(r["value"])
        if not c:
            retire_junk.append(dict(r))
            continue
        key = (r["pid"], r["predicate"], dedupe_key(r["value"]))
        if key in seen:
            retire_dup.append({**dict(r), "duplicate_of": seen[key]})
            continue
        seen[key] = r["id"]
        if c != r["value"]:
            rewrites.append({**dict(r), "canonical": c})

    print("  rewritten to canonical form : %d" % len(rewrites))
    print("  retired as duplicates       : %d" % len(retire_dup))
    print("  retired as not-a-url        : %d" % len(retire_junk))
    for r in rewrites[:8]:
        print("     %-16s %-42s -> %s" % (r["predicate"], r["value"][:42], r["canonical"][:42]))
    for r in retire_junk[:8]:
        print("     junk %-14s %s" % (r["predicate"], r["value"][:52]))

    if not a.apply:
        print("\ndry run; pass --apply to write")
        return

    now = datetime.now(timezone.utc).isoformat()
    con.execute("BEGIN IMMEDIATE")
    try:
        for r in rewrites:
            con.execute("UPDATE facts SET value=? WHERE id=?", (r["canonical"], r["id"]))
        for r in retire_dup + retire_junk:
            con.execute("UPDATE facts SET valid_until=? WHERE id=?", (now, r["id"]))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    AUDIT = os.path.join(AUDIT_DIR, "url-normalisation-%s.json" % now[:19].replace(":", ""))
    os.makedirs(AUDIT_DIR, exist_ok=True)
    json.dump({"run_at": now, "rewritten": rewrites,
               "retired_duplicate": retire_dup, "retired_not_a_url": retire_junk,
               "revert_sql": "UPDATE facts SET valid_until=NULL WHERE id IN (...)"},
              open(AUDIT, "w"), indent=1)
    print("applied; audit at %s" % AUDIT)

    left = [r for r in con.execute(SQL).fetchall() if canonical_url(r["value"]) != r["value"]]
    print("VERIFY non-canonical url facts remaining: %d" % len(left))
    dups = con.execute(
        "SELECT COUNT(*) FROM (SELECT e.source_id, f.predicate, f.value, COUNT(*) n "
        "FROM facts f JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
        "WHERE f.valid_until IS NULL AND (f.predicate LIKE 'profile\\_%' ESCAPE '\\' "
        "OR f.predicate IN ('linkedin','website')) "
        "GROUP BY e.source_id, f.predicate, f.value HAVING n > 1)").fetchone()[0]
    print("VERIFY duplicate (person,predicate,value) groups: %d" % dups)


if __name__ == "__main__":
    main()
