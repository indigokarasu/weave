#!/usr/bin/env python3
"""
Standalone Weave Enrichability Recalculation Script — SQLite backend.

Iterates all Person nodes in the Weave graph and recalculates
enrichability_score for each one. Run nightly at 1am via cron.

Usage:
  python3 recalculate_enrichability.py [--dry-run] [--verbose]
"""

import json
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 recalculate_enrichability.py")
    sys.exit(0)


AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
SQLITE_DB = AGENT_ROOT / "commons/db/ocas-weave/weave.sqlite"

DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def open_db():
    sys.path.insert(0, str(Path(__file__).parent))
    from weave_sqlite import WeaveDB
    return WeaveDB(SQLITE_DB)


def recalculate_enrichability(weave, contact_id):
    """Recalculate enrichability_score for a contact."""
    rows = weave.execute("""
        SELECT name_given, name_family, email, phone, org,
               occupation, location_city, source_type
        FROM persons WHERE id = :id
    """, {"id": contact_id})
    if not rows:
        return None
    c = rows[0]
    has = {
        "name_given": bool(c["name_given"]),
        "name_family": bool(c["name_family"]),
        "email": bool(c["email"]),
        "phone": bool(c["phone"]),
        "org": bool(c["org"]),
        "occupation": bool(c["occupation"]),
        "location_city": bool(c["location_city"]),
    }
    seed_count = sum(has.values())

    if seed_count < 2:
        new_score = 0.0
    else:
        gaps = []
        if not c["email"]: gaps.append("email")
        if not c["phone"]: gaps.append("phone")
        if not c["org"]: gaps.append("org")
        if not c["occupation"]: gaps.append("occupation")
        if not c["location_city"]: gaps.append("location_city")

        if not gaps:
            new_score = 0.0
        else:
            enriched_fields = []
            try:
                er = weave.execute("""
                    SELECT f.predicate
                    FROM facts f
                    JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
                    WHERE e.source_id = :id
                      AND f.source_type = 'web_enrichment'
                      AND f.predicate IN ('org','occupation','location_city','email','phone','location_country')
                """, {"id": contact_id})
                enriched_fields = [r["predicate"] for r in er]
            except Exception:
                pass

            remaining_gaps = [g for g in gaps if g not in enriched_fields]
            already_enriched = len([g for g in gaps if g in enriched_fields])

            if not remaining_gaps:
                new_score = 0.5
            else:
                n_conn = 0
                try:
                    cr = weave.execute("""
                        SELECT count(*) as cnt FROM edges
                        WHERE source_id = :id AND rel_type = 'Knows'
                    """, {"id": contact_id})
                    n_conn = cr[0]["cnt"] if cr else 0
                except Exception:
                    pass

                st = c["source_type"] or ""
                src_rel = {
                    "imported": 1.0, "scout_research": 0.8, "direct": 0.9,
                    "user-stated": 0.3, "web_enrichment": 0.4, "inferred": 0.5,
                }.get(st, 0.5)

                dqs = 5.0
                try:
                    dr = weave.execute("""
                        SELECT f.value
                        FROM facts f
                        JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
                        WHERE e.source_id = :id AND f.predicate = 'data_quality_score'
                    """, {"id": contact_id})
                    if dr:
                        dqs = float(dr[0]["value"])
                except Exception:
                    pass

                gap_score = min(len(remaining_gaps) * 1.33, 4.0)
                seed_base = 1.0 if (has["name_given"] and has["name_family"]) else 0.5
                seed_bonus = min((seed_count - (2 if (has["name_given"] or has["name_family"]) else 0)) * 0.4, 2.0)
                seed_final = min(seed_base + seed_bonus, 3.0)
                conn_score = min(math.log2(max(n_conn, 1) + 1) * 0.6, 2.0)
                src_score = src_rel * 1.0
                enrich_pen = already_enriched * 0.5
                complete_pen = (dqs / 10.0) * 1.0

                raw = gap_score + seed_final + conn_score + src_score - enrich_pen - complete_pen
                new_score = max(0.0, min(10.0, round(raw, 1)))

    if DRY_RUN:
        return new_score

    # Delete old score, write new
    try:
        weave.execute_write("""
            DELETE FROM facts WHERE id IN (
                SELECT f.id FROM facts f
                JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
                WHERE e.source_id = :id AND f.predicate = 'enrichability_score'
            )
        """, {"id": contact_id})
    except Exception:
        pass

    now = datetime.now(timezone.utc).isoformat()
    fid = str(uuid.uuid4())
    weave.execute_write("""
        INSERT INTO facts (id, predicate, value, source_type, source_ref, confidence, record_time)
        VALUES (:fid, 'enrichability_score', :val, 'system', 'enrichability-recalc', 1.0, :rt)
    """, {"fid": fid, "val": str(new_score), "rt": now})
    weave.execute_write("""
        INSERT OR IGNORE INTO edges (id, source_id, target_id, rel_type, record_time)
        VALUES (:eid, :pid, :fid, 'HasFact', :rt)
    """, {"eid": str(uuid.uuid4()), "pid": contact_id, "fid": fid, "rt": now})

    return new_score


def get_all_persons(weave):
    """Get all Person IDs."""
    return weave.execute("SELECT id, name FROM persons ORDER BY name")


def main():
    mode = "DRY RUN" if DRY_RUN else "LIVE"
    log(f"Enrichability Recalculation starting [{mode}]")
    log(f"Database: {SQLITE_DB}")

    if not SQLITE_DB.exists():
        log("ERROR: Database file does not exist. Run migration first.")
        sys.exit(1)

    weave = open_db()
    persons = get_all_persons(weave)
    total = len(persons)
    log(f"Found {total} Person nodes")

    updated = 0
    skipped = 0
    errors = 0
    score_distribution = {}

    for i, row in enumerate(persons):
        contact_id = row["id"]
        name = row["name"] or "(unnamed)"

        try:
            old_score = None
            old_rows = weave.execute("""
                SELECT f.value FROM facts f
                JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
                WHERE e.source_id = :id AND f.predicate = 'enrichability_score'
            """, {"id": contact_id})
            if old_rows:
                old_score = float(old_rows[0]["value"])

            new_score = recalculate_enrichability(weave, contact_id)

            if new_score is None:
                skipped += 1
                continue

            score_bucket = f"{new_score:.1f}"
            score_distribution[score_bucket] = score_distribution.get(score_bucket, 0) + 1

            if VERBOSE or old_score != new_score:
                log(f"  [{i+1}/{total}] {name}: {old_score} → {new_score}")

            updated += 1

        except Exception as e:
            log(f"  [{i+1}/{total}] {name}: ERROR — {e}")
            errors += 1

    log("=" * 60)
    log(f"RECALCULATION COMPLETE [{mode}]")
    log(f"  Total persons:    {total}")
    log(f"  Updated:          {updated}")
    log(f"  Skipped:          {skipped}")
    log(f"  Errors:           {errors}")

    if score_distribution:
        log("  Score distribution:")
        for bucket in sorted(score_distribution.keys(), key=float):
            count = score_distribution[bucket]
            bar = "█" * min(count, 50)
            log(f"    {bucket:>5}: {count:>4}  {bar}")

    log("=" * 60)


if __name__ == "__main__":
    main()
