#!/usr/bin/env python3
"""
Overnight Weave Contact Enrichment Pipeline

Proper 3-phase enrichment per the Weave SKILL.md Contact Enrichment Lifecycle:
  1. SCOUT PHASE: SearXNG identity-resolved research (Tier 1 public web search)
  2. SIFT PHASE:   Fetch full pages, fall back to Jina Reader for JS-heavy sites
  3. SHERLOCK:     Username/handle expansion (if handles discovered during extraction)

Shared extraction/search/validation logic lives in weave_enrich.py.
This file contains only the batch pipeline orchestration.
"""
import json
import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
SQLITE_DB = AGENT_ROOT / "commons/db/ocas-weave/weave.sqlite"
PROGRESS_FILE = str(AGENT_ROOT / "data/weave-enrichment/progress.jsonl")
STATS_FILE = str(AGENT_ROOT / "data/weave-enrichment/stats.json")

# Pipeline config
SEARCH_DELAY = 3        # Seconds between searches
SYNC_EVERY = 30         # Sync to Google after this many enriched contacts
DEADLINE_HOUR_ET = 8    # Stop at 8am ET
MIN_CONFIDENCE = 0.7

# Shared enrichment logic
sys.path.insert(0, str(Path(__file__).parent))
from weave_enrich import (
    searxng_search, fetch_page, extract_from_content,
    validate_field, is_auth_walled,
    build_scout_queries, SEARXNG_URL, JINA_BASE,
    log, sift_extract_from_pages, enrich_weave_contact,
)

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 overnight_enrichment.py")
    sys.exit(0)



# ─── Weave I/O ──────────────────────────────────────────────────────────────

def get_contacts_needing_enrichment():
    """Query Weave for contacts with gaps, excluding user-provided data."""
    from weave_sqlite import WeaveDB
    weave = WeaveDB(SQLITE_DB)

    rows = weave.execute("""
        SELECT id, name, name_given, name_family, email, phone,
               org, occupation, location_city, location_country,
               source_type, confidence
        FROM persons
        WHERE name IS NOT NULL AND name != ''
          AND (source_type IS NULL OR (source_type != 'user_provided' AND source_type != 'user_correction'))
          AND (confidence IS NULL OR confidence < 0.9)
          AND ((org IS NULL OR org = '')
            OR (occupation IS NULL OR occupation = '')
            OR (location_city IS NULL OR location_city = '')
            OR (email IS NULL OR email = '')
            OR (phone IS NULL OR phone = ''))
          AND NOT (org IS NOT NULL AND org != '' AND occupation IS NOT NULL AND occupation != '')
        ORDER BY record_time DESC
    """)

    contacts = []
    for row in rows:
        contact = dict(row)
        contact["gaps"] = [f for f in ["org", "occupation", "location_city", "email", "phone"] if not contact.get(f)]
        contacts.append(contact)
    return contacts


def _recalculate_enrichability(weave, contact_id):
    """Recalculate enrichability_score after successful writes."""
    new_score = recalculate_enrichability_sqlite(weave, contact_id)
    if new_score is not None:
        log(f"  ↻ Enrichability updated → {new_score}")


def recalculate_enrichability_sqlite(weave, contact_id):
    """Recalculate enrichability_score for a contact using SQLite backend."""
    import math
    import uuid as _uuid

    ENRICHABLE_FIELDS = ["org", "occupation", "location_city", "email", "phone"]

    rows = weave.execute("""
        SELECT name_given, name_family, email, phone, org,
               occupation, location_city, source_type
        FROM persons WHERE id = ?
    """, (contact_id,))
    if not rows:
        return None
    c = rows[0]
    has = {
        "name_given": bool(c["name_given"]), "name_family": bool(c["name_family"]),
        "email": bool(c["email"]), "phone": bool(c["phone"]),
        "org": bool(c["org"]), "occupation": bool(c["occupation"]),
        "location_city": bool(c["location_city"]),
    }
    source_type = c["source_type"] or "imported"
    remaining_gaps = [f for f in ENRICHABLE_FIELDS if not has.get(f, False)]

    rows = weave.execute("""
        SELECT COUNT(*) as cnt FROM facts f
        JOIN edges e ON f.id = e.target_id
        WHERE e.source_id = ? AND e.rel_type = 'HasFact'
          AND f.source_type = 'web_enrichment'
    """, (contact_id,))
    already_enriched = rows[0]["cnt"] if rows else 0

    rows = weave.execute("SELECT COUNT(*) as cnt FROM edges WHERE source_id = ? AND rel_type = 'Knows'", (contact_id,))
    n_conn = rows[0]["cnt"] if rows else 0

    src_rel = {
        "imported": 1.0, "scout_research": 0.8, "direct": 0.9,
        "user-stated": 0.3, "web_enrichment": 0.4, "inferred": 0.5,
    }.get(source_type, 0.5)

    rows = weave.execute("""
        SELECT f.value FROM facts f
        JOIN edges e ON f.id = e.target_id
        WHERE e.source_id = ? AND e.rel_type = 'HasFact'
          AND f.predicate = 'data_quality_score'
    """, (contact_id,))
    dqs = 5.0
    if rows:
        try:
            dqs = float(rows[0]["value"])
        except Exception:
            pass

    gap_score = min(len(remaining_gaps) * 1.33, 4.0)
    seed_base = 1.0 if (has["name_given"] and has["name_family"]) else 0.5
    seed_final = min(seed_base, 3.0)
    conn_score = min(math.log2(max(n_conn, 1) + 1) * 0.6, 2.0)
    src_score = src_rel * 1.0
    enrich_pen = already_enriched * 0.5
    complete_pen = (dqs / 10.0) * 1.0

    raw = gap_score + seed_final + conn_score + src_score - enrich_pen - complete_pen
    new_score = max(0.0, min(10.0, round(raw, 1)))

    # Delete old score fact
    weave.execute("""
        DELETE FROM facts WHERE id IN (
            SELECT f.id FROM facts f
            JOIN edges e ON f.id = e.target_id
            WHERE e.source_id = ? AND e.rel_type = 'HasFact'
              AND f.predicate = 'enrichability_score'
        )
    """, (contact_id,))

    # Write new score
    fact_id = str(_uuid.uuid4())
    record_time = datetime.now(timezone.utc).isoformat()
    weave.execute("""
        INSERT INTO facts (id, predicate, value, confidence, source_type, source_ref, record_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (fact_id, "enrichability_score", str(new_score), 0.9, "inferred", "enrichability_recalc", record_time))

    edge_id = str(_uuid.uuid4())
    weave.execute_write("""
        INSERT INTO edges (id, source_id, target_id, rel_type, confidence, record_time)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (edge_id, contact_id, fact_id, "HasFact", 0.9, record_time))

    return new_score


# ─── Progress / Stats ───────────────────────────────────────────────────────

def load_progress():
    """Load already-processed contact IDs to resume on restart."""
    processed = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    if rec.get("id"):
                        processed.add(rec["id"])
                except Exception:
                    pass
    return processed


def save_progress(contact_id, name, fields_enriched, error=None):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "a") as f:
        json.dump({
            "id": contact_id, "name": name,
            "fields": fields_enriched, "error": error,
            "ts": datetime.now(timezone.utc).isoformat(),
        }, f)
        f.write("\n")


def save_stats(enriched, failed, skipped, total_processed, session_start):
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    stats = {
        "last_session_start": session_start.isoformat(),
        "last_session_end": datetime.now(timezone.utc).isoformat(),
        "session_enriched": enriched, "session_failed": failed,
        "session_skipped": skipped, "total_processed_all_time": total_processed,
    }
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def sync_to_google():
    """Run Google Contacts sync."""
    log("Syncing to Google Contacts...")
    try:
        env = os.environ.copy()
        env.setdefault("AGENT_ROOT", str(AGENT_ROOT))
        env.setdefault("HOME", "/root")
        result = subprocess.run(
            ["python3", str(AGENT_ROOT / "skills/ocas-weave/scripts/google_sync.py")],
            capture_output=True, text=True, timeout=300, env=env,
        )
        if result.returncode == 0:
            log("Google sync completed successfully")
        else:
            log(f"Google sync returned {result.returncode}: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        log("Google sync timed out (5min)")
    except Exception as e:
        log(f"Google sync error: {e}")


def is_past_deadline():
    """Check if we've passed 8am ET."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    deadline = now_et.replace(hour=DEADLINE_HOUR_ET, minute=0, second=0, microsecond=0)
    if now_et.hour >= DEADLINE_HOUR_ET:
        deadline += timedelta(days=1)
    return now_et >= deadline


def hours_until_deadline():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    deadline = now_et.replace(hour=DEADLINE_HOUR_ET, minute=0, second=0, microsecond=0)
    if now_et.hour >= DEADLINE_HOUR_ET:
        deadline += timedelta(days=1)
    return (deadline - now_et).total_seconds() / 3600


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    session_start = datetime.now(timezone.utc)

    log("=" * 60)
    log("OVERNIGHT WEAVE ENRICHMENT STARTING")
    log(f"Hours until 8am ET deadline: {hours_until_deadline():.1f}")
    log("=" * 60)

    processed_ids = load_progress()
    log(f"Previously processed: {len(processed_ids)} contacts")

    contacts = get_contacts_needing_enrichment()
    log(f"Total contacts needing enrichment: {len(contacts)}")

    to_process = contacts[:50]
    log(f"Contacts to process this run: {len(to_process)}")

    if not to_process:
        log("No contacts to process. All done!")
        save_stats(0, 0, 0, len(processed_ids), session_start)
        return

    enriched_count = failed_count = skipped_count = 0

    for i, contact in enumerate(to_process):
        if is_past_deadline():
            log("⏰ Deadline reached (8am ET). Stopping.")
            break

        name = contact["name"]
        contact_id = contact["id"]
        gaps = contact["gaps"]
        org = contact.get("org", "")

        log(f"[{i+1}/{len(to_process)}] {name} | gaps: {', '.join(gaps)} | {len(to_process) - i} remaining")

        try:
            # ── SCOUT PHASE ──
            queries = build_scout_queries(
                name,
                name_given=contact.get("name_given", ""),
                name_family=contact.get("name_family", ""),
                org=org,
            )
            all_results = []
            for q in queries:
                try:
                    results = searxng_search(q, limit=5)
                    all_results.extend(results)
                    time.sleep(SEARCH_DELAY)
                except Exception as e:
                    log(f"  Search error: {e}")

            if not all_results:
                log("  No search results, skipping")
                skipped_count += 1
                save_progress(contact_id, name, [], error="no_search_results")
                continue

            log(f"  Scout: {len(all_results)} results from {len(queries)} queries")

            # ── SIFT PHASE ──
            enrichment = sift_extract_from_pages(name, all_results, max_pages=3)
            sources = enrichment.pop("_sources", [])

            if not enrichment:
                log("  No extractable data from pages")
                skipped_count += 1
                save_progress(contact_id, name, [], error="no_extractable_data")
                continue

            log(f"  Sift: extracted {list(enrichment.keys())} from {len(sources)} pages")

            # ── Write to Weave ──
            from weave_sqlite import WeaveDB
            weave = WeaveDB(SQLITE_DB)
            success = enrich_weave_contact(contact_id, enrichment, confidence=MIN_CONFIDENCE, person_name=name)
            if success:
                _recalculate_enrichability(weave, contact_id)
                written_fields = [k for k in enrichment.keys() if validate_field(k, enrichment[k], name)]
                log(f"  ✓ Enriched: {written_fields}")
                enriched_count += 1
                save_progress(contact_id, name, written_fields)

                if enriched_count % SYNC_EVERY == 0:
                    log(f"  [{enriched_count} enriched so far — syncing to Google]")
                    sync_to_google()
            else:
                log("  ✗ Write failed")
                failed_count += 1
                save_progress(contact_id, name, [], error="write_failed")

        except Exception as e:
            log(f"  ✗ Error: {e}")
            failed_count += 1
            save_progress(contact_id, name, [], error=str(e)[:200])

        time.sleep(0.5)

    # Final sync
    if enriched_count > 0:
        log("Final Google Contacts sync...")
        sync_to_google()

    total_processed = len(load_progress())
    save_stats(enriched_count, failed_count, skipped_count, total_processed, session_start)

    log("=" * 60)
    log("SESSION COMPLETE")
    log(f"  Enriched: {enriched_count}")
    log(f"  Failed: {failed_count}")
    log(f"  Skipped: {skipped_count}")
    log(f"  Total processed (all runs): {total_processed}")
    log("=" * 60)


if __name__ == "__main__":
    main()