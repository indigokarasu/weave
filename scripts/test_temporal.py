#!/usr/bin/env python3
"""Temporal-validity model tests for store_scout_findings.

Additive graph: facts are never deleted. Single-valued predicates (job)
supersede on change — old fact stamped valid_until + superseded_by, new fact
valid. Multi-valued predicates (phone, email, profiles) accumulate, all valid.

Runs entirely on throwaway SQLite DBs — production is never touched.
"""
import os
import sys
import sqlite3
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.environ.get("WEAVE_ENRICH_DIR", HERE)
sys.path.insert(0, HERE)          # weave_sqlite.py (with schema edit)
sys.path.insert(0, FIX)           # weave_enrich.py (with temporal logic)

import weave_sqlite
from weave_sqlite import WeaveDB
import weave_enrich

CID = "test-person-0001"


def _fresh_db(with_columns=True):
    """Create a temp weave DB with one seeded person.
    with_columns=False simulates the OLD production schema (no validity cols),
    exercising the ALTER-TABLE migration path."""
    path = os.path.join(tempfile.mkdtemp(), "weave.sqlite")
    db = WeaveDB(path)  # runs SCHEMA_SQL (fresh schema has the new columns)
    # Production's persons table carries these via later migrations; add them
    # so the test exercises the real production column set.
    for col in ("website", "pronouns"):
        try:
            db.execute_write(f"ALTER TABLE persons ADD COLUMN {col} TEXT")
        except Exception:
            pass
    if not with_columns:
        # Drop and recreate facts WITHOUT the temporal columns to mimic an
        # older DB, so _ensure_fact_validity_columns has to migrate it.
        db.execute_write("DROP TABLE facts")
        db.execute_write(
            "CREATE TABLE facts (id TEXT PRIMARY KEY, predicate TEXT NOT NULL, "
            "value TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.8, "
            "source_type TEXT NOT NULL DEFAULT 'imported', "
            "source_ref TEXT NOT NULL DEFAULT '', "
            "record_time TEXT NOT NULL DEFAULT (datetime('now')))")
    db.execute_write(
        "INSERT INTO persons (id, name, source_type, source_ref, confidence) "
        "VALUES (?,?,?,?,?)", (CID, "Test Person", "imported", "", 0.8))
    return path, db


def _seed_fact(db, predicate, value, source_type="scout_osint"):
    fid = str(uuid.uuid4())
    db.execute_write(
        "INSERT INTO facts (id, predicate, value, confidence, source_type, "
        "source_ref, record_time) VALUES (?,?,?,?,?,?,datetime('now'))",
        (fid, predicate, value, 0.75, source_type, "seed"))
    db.execute_write(
        "INSERT INTO edges (id, source_id, target_id, rel_type, confidence, "
        "record_time) VALUES (?,?,?,?,?,datetime('now'))",
        (str(uuid.uuid4()), CID, fid, "HasFact", 0.75))
    return fid


def _facts(db, predicate):
    return db.execute(
        "SELECT f.id, f.value, f.valid_until, f.superseded_by FROM facts f "
        "JOIN edges e ON e.target_id=f.id WHERE e.source_id=? AND f.predicate=? "
        "ORDER BY f.record_time", (CID, predicate))


def _research(enrichment=None, profiles=None):
    return {"identity": {"level": "high"},
            "enrichment": enrichment or {},
            "profiles": profiles or [],
            "findings": []}


def test_single_valued_job_change_supersedes():
    """org OldCorp -> NewCorp: old stays but invalid, new valid, node updated."""
    path, db = _fresh_db()
    old_fid = _seed_fact(db, "org", "OldCorp")
    db.execute_write("UPDATE persons SET org='OldCorp' WHERE id=?", (CID,))
    # The contact record only accepts an employer something else confirms, so
    # give this one the confirming signal a real job change would carry. Without
    # it the supersede still happens in the graph but stops at the node -- which
    # is the case the next test covers.
    db.execute_write("UPDATE persons SET email='t@newcorp.com' WHERE id=?", (CID,))

    n, lvl, written = weave_enrich.store_scout_findings(
        CID, _research(enrichment={"org": "NewCorp"}),
        person_name="Test Person", db_path=path)

    facts = _facts(db, "org")
    assert len(facts) == 2, f"additive: both facts kept, got {len(facts)}"
    old = [f for f in facts if f["value"] == "OldCorp"][0]
    new = [f for f in facts if f["value"] == "NewCorp"][0]
    assert old["valid_until"] is not None, "old org must be stamped invalid"
    assert old["superseded_by"] == new["id"], "old must point to superseding fact"
    assert new["valid_until"] is None, "new org must be currently valid"
    node = db.execute("SELECT org FROM persons WHERE id=?", (CID,))[0]
    assert node["org"] == "NewCorp", f"node should show current job, got {node['org']}"


def test_uncorroborated_org_not_promoted_to_node():
    """An employer nothing confirms is kept as a fact and kept off the record.

    'Heriot-Watt University' entered the address book this way: plausible,
    sourced from a crawl, and false. The graph is the right place for a
    hypothesis; the contact record -- which syncs into the real address book --
    is not.
    """
    path, db = _fresh_db()
    db.execute_write("UPDATE persons SET email='someone@example.com' WHERE id=?", (CID,))

    weave_enrich.store_scout_findings(
        CID, _research(enrichment={"org": "Heriot-Watt University"}),
        person_name="Test Person", db_path=path)

    facts = _facts(db, "org")
    assert len(facts) == 1, "the finding is still recorded, with its source"
    assert facts[0]["value"] == "Heriot-Watt University"
    assert facts[0]["valid_until"] is None, "and it is a current hypothesis"
    node = db.execute("SELECT org FROM persons WHERE id=?", (CID,))[0]
    assert not (node["org"] or ""), \
        f"node must stay empty, got {node['org']!r}"


def test_single_valued_same_value_no_duplicate():
    """org OldCorp -> OldCorp again: no new fact, nothing superseded."""
    path, db = _fresh_db()
    _seed_fact(db, "org", "OldCorp")
    weave_enrich.store_scout_findings(
        CID, _research(enrichment={"org": "OldCorp"}),
        person_name="Test Person", db_path=path)
    facts = _facts(db, "org")
    assert len(facts) == 1, "identical value must not create a second fact"
    assert facts[0]["valid_until"] is None


def test_user_entered_value_not_overwritten():
    """A node org we did NOT source (no matching superseded fact) is preserved."""
    path, db = _fresh_db()
    db.execute_write("UPDATE persons SET org='UserTypedCorp' WHERE id=?", (CID,))
    weave_enrich.store_scout_findings(
        CID, _research(enrichment={"org": "ScoutCorp"}),
        person_name="Test Person", db_path=path)
    node = db.execute("SELECT org FROM persons WHERE id=?", (CID,))[0]
    assert node["org"] == "UserTypedCorp", "user-entered node value must survive"
    # ...but the graph still records what scout found, as the current valid org.
    valid = [f for f in _facts(db, "org") if f["valid_until"] is None]
    assert any(f["value"] == "ScoutCorp" for f in valid)


def test_multi_valued_profiles_coexist():
    """Two different phone facts both stay valid — no supersession."""
    path, db = _fresh_db()
    _seed_fact(db, "phone", "+1-808-111-1111")
    # store phones via findings? phone isn't in enr; use a predicate the writer
    # emits multi-valued: profile_*. Seed one, add another.
    _seed_fact(db, "profile_github", "https://github.com/olduser")
    weave_enrich.store_scout_findings(
        CID, _research(profiles=[{
            "site": "github", "url": "https://github.com/newuser",
            "handle": "newuser", "name_shared_tokens": 2, "family_present": True}]),
        person_name="Test Person", db_path=path)
    facts = _facts(db, "profile_github")
    assert len(facts) == 2, f"both profiles kept, got {len(facts)}"
    assert all(f["valid_until"] is None for f in facts), "both must stay valid"


def test_migration_path_adds_columns():
    """An OLD facts table (no validity cols) is migrated in-place, then works."""
    path, db = _fresh_db(with_columns=False)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(facts)")}
    assert "valid_until" not in cols, "precondition: old schema lacks the column"
    _seed_fact(db, "org", "OldCorp")
    weave_enrich.store_scout_findings(
        CID, _research(enrichment={"org": "NewCorp"}),
        person_name="Test Person", db_path=path)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(facts)")}
    assert "valid_until" in cols and "superseded_by" in cols, "migration must add cols"
    facts = _facts(db, "org")
    assert len(facts) == 2
    old = [f for f in facts if f["value"] == "OldCorp"][0]
    assert old["valid_until"] is not None, "supersession works post-migration"


def test_boilerplate_bio_filtered():
    path, db = _fresh_db()
    weave_enrich.store_scout_findings(
        CID, _research(profiles=[{
            "site": "calendly", "url": "https://calendly.com/x", "handle": "x",
            "bio": "Welcome to my scheduling page. Please follow the instructions.",
            "name_shared_tokens": 2, "family_present": True}]),
        person_name="Test Person", db_path=path)
    bios = _facts(db, "bio_summary")
    assert len(bios) == 0, "calendly boilerplate must not become a bio fact"


# ---------------------------------------------------------------------------
# Field-targeted enrichment: what the store path may and may not persist.
# ---------------------------------------------------------------------------

def _research_with_findings(enrichment=None, profiles=None, findings=None):
    return {"identity": {"level": "high"},
            "enrichment": enrichment or {},
            "profiles": profiles or [],
            "findings": findings or []}


def test_account_on_is_never_written():
    """account_on was 84% of this pipeline's entire output and is not contact
    data: it says an address is registered somewhere, never whose account it is
    -- the exact signal behind the earlier false attributions."""
    path, db = _fresh_db()
    res = _research_with_findings(findings=[{
        "finding_id": "H001",
        "claim": "Email is registered on: example.test, other.test.",
        "confidence": "med",
        "source_refs": [{"url": "holehe:a@example.test",
                         "quote": "example.test, other.test"}]}])
    weave_enrich.store_scout_findings(CID, res, person_name="Test Person",
                                      db_path=path)
    rows = db.execute(
        "SELECT f.id FROM facts f JOIN edges e ON e.target_id=f.id "
        "WHERE e.source_id=? AND f.predicate='account_on'", (CID,))
    assert rows == [], f"account_on must not be persisted, got {len(rows)}"


def test_phone_from_enrichment_is_written_and_normalised():
    path, db = _fresh_db()
    weave_enrich.store_scout_findings(
        CID, _research_with_findings(enrichment={"phone": "(202) 555-0143"}),
        person_name="Test Person", db_path=path)
    vals = [f["value"] for f in _facts(db, "phone")]
    assert vals == ["+12025550143"], f"expected E.164, got {vals}"


def test_invalid_phone_is_refused():
    path, db = _fresh_db()
    weave_enrich.store_scout_findings(
        CID, _research_with_findings(enrichment={"phone": "2019 03 14"}),
        person_name="Test Person", db_path=path)
    assert _facts(db, "phone") == [], "a non-number must not reach the record"


def test_phone_accumulates_rather_than_superseding():
    """Multi-valued: a second number never invalidates the first."""
    path, db = _fresh_db()
    _seed_fact(db, "phone", "+12025550100")
    weave_enrich.store_scout_findings(
        CID, _research_with_findings(enrichment={"phone": "+1 202 555 0143"}),
        person_name="Test Person", db_path=path)
    facts = _facts(db, "phone")
    assert len(facts) == 2, f"both numbers kept, got {len(facts)}"
    assert all(f["valid_until"] is None for f in facts), "neither superseded"


def test_linkedin_url_is_written_as_profile_linkedin():
    path, db = _fresh_db()
    weave_enrich.store_scout_findings(
        CID, _research_with_findings(enrichment={
            "linkedin_url": "https://www.linkedin.com/in/zephyrine-quintbadger",
            "linkedin_url_confidence": 0.75}),
        person_name="Zephyrine Quintbadger", db_path=path)
    vals = [f["value"] for f in _facts(db, "profile_linkedin")]
    assert len(vals) == 1 and "zephyrine-quintbadger" in vals[0], vals


def test_contact_missing_fields_reads_the_record():
    have_all = {"id": CID, "email": "a@example.test", "phone": "+12025550143",
                "location_city": "Springfield, OR",
                "website": "https://averyplaceholder.test"}
    missing = weave_enrich.contact_missing_fields(have_all)
    assert missing == ["linkedin"], missing
    empty = weave_enrich.contact_missing_fields({"id": CID})
    assert set(empty) == {"linkedin", "phone", "site", "email", "city"}, empty


def test_contact_missing_fields_ignores_a_social_url_as_a_website():
    # A LinkedIn or Medium URL in the website column is somebody else's
    # platform, not the contact's own site.
    c = {"id": CID, "website": "https://www.linkedin.com/in/avery-placeholder"}
    missing = weave_enrich.contact_missing_fields(c)
    assert "site" in missing, "a linkedin URL is not a personal site"
    assert "linkedin" not in missing, "but it does satisfy the linkedin field"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {n}: {e}")
        except Exception as e:
            import traceback
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
