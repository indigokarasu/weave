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

# Derived from this file's own location — <root>/skills/ocas-weave/scripts/ —
# rather than from Path.home(). The old fallback resolved the DATABASE correctly by
# accident (~/.hermes/commons is a symlink into the profile) while reading the
# cooldown history from a different, nearly empty file, so runs looked healthy and
# quietly re-processed contacts that were already done. An explicit AGENT_ROOT still
# takes precedence for the cron wrapper.
_DERIVED_ROOT = Path(__file__).resolve().parents[3]
AGENT_ROOT = Path(os.environ.get("AGENT_ROOT") or _DERIVED_ROOT)
SQLITE_DB = AGENT_ROOT / "commons/db/ocas-weave/weave.sqlite"
PROGRESS_FILE = str(AGENT_ROOT / "data/weave-enrichment/progress.jsonl")
STATS_FILE = str(AGENT_ROOT / "data/weave-enrichment/stats.json")

# Pipeline config
SEARCH_DELAY = 3        # Seconds between searches (legacy; unused by scout path)
SCOUT_TOP_SITES = 300   # maigret site breadth per contact (speed vs coverage)
SYNC_EVERY = 30         # Sync to Google after this many enriched contacts
DEADLINE_HOUR_ET = 8    # Stop at 8am ET
MIN_CONFIDENCE = 0.7
# Bumped whenever the enrichment pipeline gains a capability. A contact that
# failed under an OLDER version is retried immediately rather than serving out
# its cooldown: the cooldown exists to avoid re-asking the same question, not
# to lock a contact out after the question itself has improved.
PIPELINE_VERSION = "2026-08-19.round9"

RETRY_FAILED_DAYS = 7       # nothing found: retry weekly (was 30 — too long to
                            # wait for pipeline fixes to reach failed contacts)
REFRESH_ENRICHED_DAYS = 90  # data found: re-verify quarterly (jobs and cities change)

# Shared enrichment logic
sys.path.insert(0, str(Path(__file__).parent))
from weave_enrich import (
    log, scout_research_contact, store_scout_findings,
    has_sufficient_anchors,
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
        -- Richer contacts first: every known field is an identity anchor for the
        -- scout queries and the LLM gate, so data-rich contacts are the LIKELIEST
        -- to match well -- the opposite of the old behavior, which skipped them.
        -- ...but only where search can pay off: a contact whose ONLY gap is
        -- phone has nothing the public web reliably provides, so those sort
        -- last (still one attempt each, never skipped).
        ORDER BY ((org IS NULL OR org = '')
                OR (occupation IS NULL OR occupation = '')
                OR (location_city IS NULL OR location_city = '')
                OR (location_country IS NULL OR location_country = '')
                OR (email IS NULL OR email = '')) DESC,
                ((org IS NOT NULL AND org != '')
                + (occupation IS NOT NULL AND occupation != '')
                + (location_city IS NOT NULL AND location_city != '')
                + (location_country IS NOT NULL AND location_country != '')
                + (email IS NOT NULL AND email != '')
                + (phone IS NOT NULL AND phone != '')) DESC,
                record_time DESC
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
    """Map contact id -> (last attempt time, whether fields were written).

    Drives the cooldown filter: this was previously collected as a lifetime
    done-set (and then not even applied), but contacts must ROTATE -- recently
    attempted ones sit out, everyone else gets a turn, and every contact comes
    back around because people's data changes over time."""
    latest = {}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    cid = rec.get("id")
                    ts = datetime.fromisoformat(rec.get("ts", ""))
                    if cid:
                        prev = latest.get(cid)
                        if prev is None or ts > prev[0]:
                            latest[cid] = (ts, bool(rec.get("fields")),
                                           rec.get("pipeline_version", ""))
                except Exception:
                    pass
    return latest


_IDENTITY_RANK = {"high": 3, "med": 2, "low": 1, "none": 0, "error": 0}


def _live_scout_fact_count(contact_id):
    """How many still-valid scout facts this contact already holds.

    Distinguishes "nothing could be found" from "everything is already here",
    which the skipped counter previously merged.
    """
    try:
        import sqlite3
        con = sqlite3.connect(str(SQLITE_DB))
        try:
            return con.execute(
                "SELECT COUNT(*) FROM facts f "
                "JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
                "WHERE e.source_id = ? AND f.valid_until IS NULL "
                "AND f.source_type LIKE 'scout%'", (contact_id,)).fetchone()[0]
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return 0

def save_progress(contact_id, name, fields_enriched, error=None):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "a") as f:
        json.dump({
            "id": contact_id, "name": name,
            "fields": fields_enriched, "error": error,
            "pipeline_version": PIPELINE_VERSION,
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

    recent = load_progress()
    log(f"Attempt history: {len(recent)} contacts")

    contacts = get_contacts_needing_enrichment()
    log(f"Total contacts needing enrichment: {len(contacts)}")

    now = datetime.now(timezone.utc)

    def off_cooldown(c):
        prev = recent.get(c["id"])
        if prev is None:
            return True
        last_ts, had_fields = prev[0], prev[1]
        prev_version = prev[2] if len(prev) > 2 else ""
        # A failure recorded by an older pipeline is not evidence that the
        # CURRENT pipeline cannot find this person — retry it now.
        if not had_fields and prev_version != PIPELINE_VERSION:
            return True
        wait = REFRESH_ENRICHED_DAYS if had_fields else RETRY_FAILED_DAYS
        return (now - last_ts).days >= wait

    eligible = [c for c in contacts if off_cooldown(c)]
    log(f"Off cooldown and eligible tonight: {len(eligible)} "
        f"(cooling down: {len(contacts) - len(eligible)})")

    # Overridable so a tuning loop can run small rounds; the nightly cron
    # sets nothing and keeps the original 50.
    try:
        _batch = int(os.environ.get("WEAVE_BATCH_SIZE", "50"))
    except ValueError:
        _batch = 50
    to_process = eligible[:max(1, _batch)]
    log(f"Contacts to process this run: {len(to_process)}")

    if not to_process:
        log("No contacts to process. All done!")
        save_stats(0, 0, 0, len(recent), session_start)
        return

    enriched_count = failed_count = skipped_count = 0
    already_current_count = 0

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
            # ── SCOUT PHASE (ocas-scout person OSINT) ──
            # The intended pipeline (contact-enrichment.plan.md): anchor on the
            # contact's email/phone, run maigret/holehe handle-pivot, and only
            # accept data when scout corroborates identity across independent
            # sources. Replaces the old inline SearXNG name-search, which
            # resolved common names to the wrong person.
            # Refuse contacts that cannot be resolved by any amount of search
            # BEFORE spending a full multi-site sweep on them: single-token
            # names and business records burned ~30s each to conclude nothing.
            ok_anchor, anchor_reason = has_sufficient_anchors(contact)
            if not ok_anchor:
                log(f"  Skipped: {anchor_reason} (no sweep attempted)")
                save_progress(contact_id, name, [], error=f"unenrichable: {anchor_reason}")
                skipped_count += 1
                continue

            res = scout_research_contact(contact, top_sites=SCOUT_TOP_SITES)
            level = res.get("identity", {}).get("level", "none")
            reason = res.get("identity", {}).get("reason", "")

            # Store ALL corroborated data onto the actual contact node + graph
            # (fill-empty scalar columns + rich facts). Gated on identity>=med.
            n_written, level, written_fields = store_scout_findings(
                contact_id, res, person_name=name, db_path=SQLITE_DB,
                min_identity="med",
            )

            if n_written == 0:
                # "Nothing written" has two opposite causes and they were counted
                # as one. A contact whose data is already on file is complete, not
                # failed: recording it with an empty field list gave it the short
                # retry cooldown and it returned to do nothing again.
                already = _live_scout_fact_count(contact_id)
                if already and _IDENTITY_RANK.get(level, 0) >= 2:
                    log(f"  ✓ Already current: identity={level}, "
                        f"{already} fact(s) already on file, nothing new to add")
                    already_current_count += 1
                    save_progress(contact_id, name, ["__already_current__"])
                else:
                    log(f"  Scout: identity={level} ({reason}); nothing written")
                    skipped_count += 1
                    save_progress(contact_id, name, [],
                                  error=f"scout_identity_{level}")
                continue

            from weave_sqlite import WeaveDB
            weave = WeaveDB(SQLITE_DB)
            _recalculate_enrichability(weave, contact_id)
            log(f"  ✓ Scout {level}: wrote {n_written} facts/fields "
                f"{sorted(set(written_fields))} "
                f"(sites={res.get('identity', {}).get('corroborating_sites', [])})")
            enriched_count += 1
            save_progress(contact_id, name, written_fields)

            if enriched_count % SYNC_EVERY == 0:
                log(f"  [{enriched_count} enriched so far — syncing to Google]")
                sync_to_google()

        except Exception as e:
            log(f"  ✗ Error: {e}")
            failed_count += 1
            save_progress(contact_id, name, [], error=str(e)[:200])

        time.sleep(0.5)

    # Final sync
    if enriched_count > 0:
        # The site gate self-extends: every run probes sites it has not seen and
        # can newly classify one as answering for any handle. Facts written BEFORE
        # that discovery stay live unless something sweeps them, so the sweep runs
        # here rather than waiting to be noticed. Measured: the blocked set grew
        # from 4 to 13 hosts over three runs, leaving 59 stale facts behind.
        try:
            import subprocess as _sp
            _sw = _sp.run(["/root/hermes-agent/.venv/bin/python",
                           "/root/.hermes/profiles/indigo/skills/ocas-weave/"
                           "scripts/sweep_blocked_sites.py", "--apply"],
                          capture_output=True, text=True, timeout=300)
            for _l in (_sw.stdout or "").strip().splitlines()[-3:]:
                log(f"  site sweep: {_l}")
        except Exception as _e:  # noqa: BLE001
            log(f"  site sweep skipped: {str(_e)[:70]}")
        log("Final Google Contacts sync...")
        sync_to_google()

    total_processed = len(load_progress())
    save_stats(enriched_count, failed_count, skipped_count, total_processed, session_start)

    log("=" * 60)
    log("SESSION COMPLETE")
    log(f"  Enriched: {enriched_count}")
    log(f"  Failed: {failed_count}")
    log(f"  Skipped: {skipped_count}")
    log(f"  Already current: {already_current_count}")
    log(f"  Total processed (all runs): {total_processed}")
    log("=" * 60)


if __name__ == "__main__":
    main()
