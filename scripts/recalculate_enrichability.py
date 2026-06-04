#!/usr/bin/env python3
"""
Standalone Weave Enrichability Recalculation Script

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

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
DB_PATH = AGENT_ROOT / "commons/db/ocas-weave/weave.lbug"

DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def open_db():
    import real_ladybug as lb
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = lb.Database(str(DB_PATH), read_only=False)
    conn = lb.Connection(db)
    return db, conn


def recalculate_enrichability(conn, contact_id):
    """Recalculate enrichability_score for a contact."""
    r = conn.execute("""
        MATCH (p:Person {id: $id})
        RETURN p.name_given AS name_given, p.name_family AS name_family,
               p.email AS email, p.phone AS phone, p.org AS org,
               p.occupation AS occupation, p.location_city AS location_city,
               p.source_type AS source_type
    """, {"id": contact_id})
    rows = r.get_all()
    r.close()
    if not rows:
        return None
    c = rows[0]
    has = {f: bool(c[i]) for i, f in enumerate(
        ["name_given", "name_family", "email", "phone", "org", "occupation", "location_city"])}
    seed_count = sum(has.values())

    if seed_count < 2:
        new_score = 0.0
    else:
        gaps = []
        if not c[2]: gaps.append("email")
        if not c[3]: gaps.append("phone")
        if not c[4]: gaps.append("org")
        if not c[5]: gaps.append("occupation")
        if not c[6]: gaps.append("location_city")

        if not gaps:
            new_score = 0.0
        else:
            enriched_fields = []
            try:
                r2 = conn.execute("""
                    MATCH (p:Person {id: $id})-[:HasFact]->(f:Fact {source_type: 'web_enrichment'})
                    WHERE f.predicate IN ['org','occupation','location_city','email','phone','location_country']
                    RETURN collect(DISTINCT f.predicate) AS ef
                """, {"id": contact_id})
                er = r2.get_all()
                r2.close()
                if er:
                    enriched_fields = er[0][0] or []
            except:
                pass

            remaining_gaps = [g for g in gaps if g not in enriched_fields]
            already_enriched = len([g for g in gaps if g in enriched_fields])

            if not remaining_gaps:
                new_score = 0.5
            else:
                n_conn = 0
                try:
                    r3 = conn.execute(
                        "MATCH (p:Person {id: $id})-[r:Knows]-() RETURN count(r) AS cnt",
                        {"id": contact_id}
                    )
                    cr = r3.get_all()
                    r3.close()
                    n_conn = cr[0][0] if cr else 0
                except:
                    pass

                st = c[7] or ""
                src_rel = {
                    "imported": 1.0, "scout_research": 0.8, "direct": 0.9,
                    "user-stated": 0.3, "web_enrichment": 0.4, "inferred": 0.5,
                }.get(st, 0.5)

                dqs = 5.0
                try:
                    r4 = conn.execute(
                        "MATCH (p:Person {id: $id})-[:HasFact]->(f:Fact {predicate: 'data_quality_score'}) RETURN f.value",
                        {"id": contact_id}
                    )
                    dr = r4.get_all()
                    r4.close()
                    if dr:
                        dqs = float(dr[0][0])
                except:
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
        conn.execute(
            "MATCH (p:Person {id: $id})-[:HasFact]->(f:Fact {predicate: 'enrichability_score'}) DETACH DELETE f",
            {"id": contact_id}
        )
    except:
        pass

    now = datetime.now(timezone.utc).isoformat()
    fid = str(uuid.uuid4())
    conn.execute("""
        MATCH (p:Person {id: $pid})
        CREATE (f:Fact {id: $fid, predicate: 'enrichability_score', value: $val,
            source_type: 'system', source_ref: 'enrichability-recalc',
            confidence: 1.0, record_time: $rt})
        CREATE (p)-[:HasFact]->(f)
    """, {"pid": contact_id, "fid": fid, "val": str(new_score), "rt": now})

    return new_score


def get_all_persons(conn):
    """Get all Person node IDs."""
    r = conn.execute("MATCH (p:Person) RETURN p.id AS id, p.name AS name ORDER BY p.name")
    rows = r.get_all()
    r.close()
    return rows


def main():
    mode = "DRY RUN" if DRY_RUN else "LIVE"
    log(f"Enrichability Recalculation starting [{mode}]")
    log(f"Database: {DB_PATH}")

    if not DB_PATH.exists():
        log("ERROR: Database file does not exist. Run weave.init first.")
        sys.exit(1)

    db, conn = open_db()

    persons = get_all_persons(conn)
    total = len(persons)
    log(f"Found {total} Person nodes")

    updated = 0
    skipped = 0
    errors = 0
    score_distribution = {}

    for i, row in enumerate(persons):
        contact_id = row[0]
        name = row[1] or "(unnamed)"

        try:
            old_score = None
            r = conn.execute(
                "MATCH (p:Person {id: $id})-[:HasFact]->(f:Fact {predicate: 'enrichability_score'}) RETURN f.value",
                {"id": contact_id}
            )
            old_rows = r.get_all()
            r.close()
            if old_rows:
                old_score = float(old_rows[0][0])

            new_score = recalculate_enrichability(conn, contact_id)

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

    conn.close()

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
