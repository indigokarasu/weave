#!/usr/bin/env python3
"""Tests for contact_urls. Includes regressions for the two defects the
adversarial review reproduced: fragment stripping, and duplicate facts under
concurrent writers. No network; temp DBs only."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contact_urls import classify_url, extract_signals, import_all, store_signals, _connect

SCHEMA = """
CREATE TABLE persons (id TEXT PRIMARY KEY, name TEXT, google_resource_name TEXT, website TEXT);
CREATE TABLE facts (id TEXT PRIMARY KEY, predicate TEXT, value TEXT, confidence REAL,
                    source_type TEXT, source_ref TEXT, record_time TEXT);
CREATE TABLE edges (id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, rel_type TEXT,
                    confidence REAL, record_time TEXT);
"""


def mkdb(persons=(("p1", "Rhea Ott", "people/c1", None),)):
    path = os.path.join(tempfile.mkdtemp(), "w.sqlite")
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for row in persons:
        con.execute("INSERT INTO persons (id,name,google_resource_name,website) VALUES (?,?,?,?)", row)
    con.commit(); con.close()
    return path


def facts_of(path, pid="p1"):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    rows = [(r["predicate"], r["value"]) for r in con.execute(
        "SELECT f.predicate,f.value FROM facts f JOIN edges e ON e.target_id=f.id "
        "WHERE e.source_id=?", (pid,))]
    con.close(); return rows


# ── classification ────────────────────────────────────────────────────────

def test_linkedin_personal_profile():
    p, u = classify_url("https://www.linkedin.com/in/rheaott")
    assert (p, u) == ("profile_linkedin", "https://linkedin.com/in/rheaott"), (p, u)


def test_linkedin_company_rejected():
    assert classify_url("https://linkedin.com/company/meta") == (None, None)
    assert classify_url("https://linkedin.com/school/mit") == (None, None)


def test_linkedin_tracking_params_dropped_identity_kept():
    p, u = classify_url("https://www.linkedin.com/in/rheaott?trk=abc")
    assert p == "profile_linkedin", p
    assert u == "https://linkedin.com/in/rheaott", u   # ?trk= is telemetry


def test_github_user_vs_reserved():
    assert classify_url("https://github.com/larkfielding")[0] == "profile_github"
    assert classify_url("https://github.com/features")[0] is None
    assert classify_url("https://github.com/features/copilot")[0] is None


def test_twitter_reserved_rejected():
    assert classify_url("https://twitter.com/intent/tweet")[0] is None
    assert classify_url("https://x.com/i/status/123")[0] is None
    assert classify_url("https://twitter.com/brianpene")[0] == "profile_twitter"


def test_junk_schemes_rejected():
    for bad in ("mailto:a@b.com", "javascript:alert(1)", "tel:+15551234",
                "", None, "notaurl", "https://", "data:text/html,x"):
        assert classify_url(bad) == (None, None), bad


def test_generic_website():
    p, u = classify_url("jacobward.com")
    assert p == "website" and u == "https://jacobward.com", u


def test_credentials_host_normalized():
    p, u = classify_url("https://user:pass@example.com/me")
    assert p == "website" and u == "https://example.com/me", u


# ── REGRESSION: fragment carries identity (rdio bug) ──────────────────────

def test_fragment_preserved_two_people_stay_distinct():
    _, a = classify_url("https://www.rdio.com/#/people/nettatheninja")
    _, b = classify_url("https://www.rdio.com/#/people/someoneelse")
    assert a != b, "fragment-routed profiles must not collapse to one value"
    assert "nettatheninja" in a


# ── extraction ────────────────────────────────────────────────────────────

def test_extract_dedups_and_picks_website():
    sig = extract_signals({"urls": [
        {"value": "https://www.linkedin.com/in/rheaott"},
        {"value": "https://linkedin.com/in/rheaott/"},      # same after canonical
        {"value": "https://rheaott.com"},
        {"value": "mailto:x@y.com"},
    ], "biographies": [{"value": "  "}]})
    assert ("profile_linkedin", "https://linkedin.com/in/rheaott") in sig["facts"]
    assert sum(1 for p, _ in sig["facts"] if p == "profile_linkedin") == 1
    assert sig["website"] == "https://rheaott.com", sig["website"]
    assert not any(p == "bio_summary" for p, _ in sig["facts"]), "blank bio skipped"


def test_extract_keeps_nonblank_bio():
    sig = extract_signals({"biographies": [{"value": "Product designer at Meta"}]})
    assert ("bio_summary", "Product designer at Meta") in sig["facts"]


# ── idempotency ───────────────────────────────────────────────────────────

def test_sequential_import_is_idempotent():
    path = mkdb()
    people = [{"resourceName": "people/c1",
               "urls": [{"value": "https://www.linkedin.com/in/rheaott"}]}]
    t1 = import_all(people, path)
    t2 = import_all(people, path)
    assert t1["written"] == 1 and t2["written"] == 0, (t1, t2)
    assert len(facts_of(path)) == 1


def test_website_never_overwrites_existing():
    path = mkdb(persons=(("p1", "X", "people/c1", "https://user-typed.example"),))
    import_all([{"resourceName": "people/c1",
                 "urls": [{"value": "https://scoutfound.example"}]}], path)
    con = sqlite3.connect(path)
    got = con.execute("SELECT website FROM persons WHERE id='p1'").fetchone()[0]
    con.close()
    assert got == "https://user-typed.example"


def test_dry_run_writes_nothing():
    path = mkdb()
    t = import_all([{"resourceName": "people/c1",
                     "urls": [{"value": "https://linkedin.com/in/rheaott"}]}],
                   path, dry_run=True)
    assert t["written"] == 1
    assert facts_of(path) == []


def test_unmatched_resource_name_skipped():
    path = mkdb()
    t = import_all([{"resourceName": "people/NOPE",
                     "urls": [{"value": "https://linkedin.com/in/x"}]}], path)
    assert t["matched"] == 0 and t["unmatched"] == 1 and facts_of(path) == []


# ── REGRESSION: concurrent writers must not duplicate ─────────────────────

def test_concurrent_import_no_duplicates():
    path = mkdb()
    here = os.path.dirname(os.path.abspath(__file__))
    prog = textwrap.dedent(f"""
        import sys, time, json
        sys.path.insert(0, {here!r})
        from contact_urls import import_all
        start = float(sys.argv[2])
        while time.time() < start:
            pass
        people = [{{"resourceName": "people/c1", "urls": [
            {{"value": "https://www.linkedin.com/in/rheaott"}},
            {{"value": "https://github.com/rheaott"}},
            {{"value": "https://rheaott.com"}}]}}]
        try:
            import_all(people, sys.argv[1])
        except Exception as e:
            print("ERR", e, file=sys.stderr)
    """)
    p = os.path.join(os.path.dirname(path), "worker.py")
    open(p, "w").write(prog)
    start = str(__import__("time").time() + 1.0)
    procs = [subprocess.Popen([sys.executable, p, path, start],
                              stderr=subprocess.PIPE) for _ in range(2)]
    for pr in procs:
        pr.wait(timeout=60)
    got = facts_of(path)
    assert len(got) == len(set(got)), f"duplicate facts under concurrency: {got}"
    assert len(got) == 3, f"expected 3 distinct facts, got {len(got)}: {got}"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f(); print(f"PASS {n}")
        except Exception as e:
            failed += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
