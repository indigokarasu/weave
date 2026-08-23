#!/usr/bin/env python3
"""Adversarial tests for weave_enrich.

Every fixture here encodes a REAL failure observed on 2026-08-13, when the
previous version attributed nick.com to a person named Nick and extracted
"Ate Bikini Bottom" as an employer. Fixtures are hostile on purpose: share
buttons, citations, unrelated emails, wrong-person pages.

Run: pytest test_weave_enrich.py -v    (or)    python3 test_weave_enrich.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from weave_enrich import (
    build_scout_queries,
    build_scout_queries_for_person,
    clean_person_name,
    email_handle_variants,
    parse_github_api_user,
    extract_links_from_page,
    has_sufficient_anchors,
    normalize_url,
    verify_identity_from_page,
)


# ── Attribution: signals on a page must not be attributed to the subject ──

HOSTILE_PAGE = """
<html><head><title>Some Blog - Tech Musings</title></head><body>
<p>Contact the webmaster at webmaster@example.org for issues.</p>
<a href="https://twitter.com/financialtimes">Follow FT</a>
<a href="https://twitter.com/intent/tweet?url=x">Tweet this</a>
<a href="https://github.com/features/copilot">GitHub Copilot</a>
<a href="https://linkedin.com/in/some-other-person">Share on LinkedIn</a>
<p>This page is about someone else entirely.</p>
</body></html>
"""


def test_hostile_page_yields_no_candidates_for_subject():
    s = extract_links_from_page(HOSTILE_PAGE, "https://blog.example.net", "Priya Venn")
    assert s["candidates"]["linkedin"] == [], s
    assert s["candidates"]["github"] == [], s
    assert s["candidates"]["twitter"] == [], s
    assert s["emails"] == [], s


def test_hostile_page_screen_rejects_when_family_name_absent():
    r = verify_identity_from_page(HOSTILE_PAGE, "Priya Venn", "", "Engineering Leader")
    assert r["verdict"] == "reject", r


def test_no_extraction_confidence_anywhere():
    s = extract_links_from_page(HOSTILE_PAGE, "", "Priya Venn")
    assert "extraction_confidence" not in s
    r = verify_identity_from_page("Priya Venn builds systems", "Priya Venn")
    flat = str(r)
    assert "confidence" not in flat, r


def test_matching_profile_is_kept():
    page = 'See <a href="https://github.com/tomasvega">my GitHub</a> for code.'
    s = extract_links_from_page(page, "", "Tomas Vega")
    assert "tomasvega" in s["candidates"]["github"], s


def test_unrelated_handle_containing_no_name_token_dropped():
    page = 'Follow <a href="https://twitter.com/gm">@GM</a> for updates. Mary Barra spoke.'
    s = extract_links_from_page(page, "", "Mary Barra")
    assert s["candidates"]["twitter"] == [], s


# ── Email / twitter leakage ───────────────────────────────────────────────

def test_email_never_becomes_twitter_handle():
    s = extract_links_from_page("mail me at tomasvega.design@fastmail.com", "", "Tomas Vega")
    assert s["candidates"]["twitter"] == [], s
    assert len(s["emails"]) == 1, s
    assert s["emails"][0]["addr"] == "tomasvega.design@fastmail.com"
    assert s["emails"][0]["name_consistent"] is True


def test_generic_and_junk_emails_dropped():
    page = ("write info@acme.com or webmaster@example.org or "
            "support@foo.io; sprite at icons@site.com/logo.png")
    s = extract_links_from_page(page, "", "Jane Smith")
    assert s["emails"] == [], s


def test_share_intent_urls_not_handles():
    page = ('<a href="https://twitter.com/share?url=z">share</a>'
            '<a href="https://x.com/intent/tweet">tweet</a>')
    s = extract_links_from_page(page, "", "")
    assert s["candidates"]["twitter"] == [], s


def test_github_reserved_paths_not_handles():
    page = ('github.com/features github.com/about github.com/orgs/acme '
            'github.com/topics/ml github.com/pricing')
    s = extract_links_from_page(page, "", "")
    assert s["candidates"]["github"] == [], s


# ── Identity screen ───────────────────────────────────────────────────────

def test_screen_rejects_wrong_person_page():
    page = "Sarah is a Biblical matriarch; Abram was her brother."
    r = verify_identity_from_page(page, "Priya Venn")
    assert r["verdict"] == "reject"


def test_screen_rejects_kidzone_for_owen_castile():
    page = "Kid Zone programming: SpongeBob Ate Bikini Bottom. Watch Nick now!"
    r = verify_identity_from_page(page, "Owen Castile", "Harvard University")
    assert r["verdict"] == "reject", r


def test_screen_candidate_when_family_name_present():
    page = "Wren Keeley spoke at the design conference in Portland."
    r = verify_identity_from_page(page, "Wren Keeley")
    assert r["verdict"] == "candidate"
    assert r["hints"]["family_name_present"] is True


def test_screen_never_confirms():
    page = ("Priya Venn, Engineering Leader at TechCorp in San Francisco. "
            "Priya Venn Priya Venn Priya Venn linkedin github")
    r = verify_identity_from_page(page, "Priya Venn", "TechCorp",
                                  "Engineering Leader", "San Francisco, CA")
    assert r["verdict"] == "candidate"  # not "confirmed" — no such verdict exists
    assert r["hints"]["org_present"] and r["hints"]["city_present"]


def test_screen_case_insensitive():
    r = verify_identity_from_page("PRIYA VENN wrote this.", "Priya Venn")
    assert r["verdict"] == "candidate"


def test_screen_empty_and_single_token():
    assert verify_identity_from_page("", "Priya Venn")["verdict"] == "reject"
    assert verify_identity_from_page("Sarah is here", "Sarah")["verdict"] == "reject"


# ── Anchor gate ───────────────────────────────────────────────────────────

def test_anchor_gate_refuses_parenthetical_single_name():
    ok, reason = has_sufficient_anchors({"name": "Dana (pottery class)"})
    assert ok is False
    assert reason == "single-token name"


def test_anchor_gate_refuses_org_shaped_name():
    ok, reason = has_sufficient_anchors({"name": "Coastal Plumbing Services"})
    assert ok is False
    assert reason == "org-shaped name"


def test_anchor_gate_passes_normal_names():
    assert has_sufficient_anchors({"name": "Wren Keeley"})[0] is True
    assert has_sufficient_anchors({"name": "Owen Castile"})[0] is True
    assert has_sufficient_anchors({"name": "Tomas Vega"})[0] is True


def test_anchor_gate_empty_and_none():
    assert has_sufficient_anchors({"name": ""})[0] is False
    assert has_sufficient_anchors({})[0] is False
    assert has_sufficient_anchors({"name": None})[0] is False


def test_anchor_gate_uses_split_family_name():
    ok, _ = has_sufficient_anchors({"name": "Cher", "name_family": "Sarkisian"})
    assert ok is True


def test_anchor_gate_junk_family_name_does_not_rescue():
    # Real row from weave.sqlite: name_given='Sarah', name_family='(art Class)'
    ok, reason = has_sufficient_anchors({
        "name": "Dana (pottery class)", "name_given": "Sarah",
        "name_family": "(art Class)",
    })
    assert ok is False, "junk parenthetical family name must not rescue the gate"
    assert reason == "single-token name"


# ── Email-anchor OSINT ────────────────────────────────────────────────────

def test_email_handle_distinctive_local():
    # A distinctive single-token local part must pass through verbatim.
    assert email_handle_variants("larkfielding@fastmail.com") == ["larkfielding"]


def test_email_handle_dotted_local_expands():
    v = email_handle_variants("tomasvega.design@fastmail.com")
    assert "tomasvega.design" in v          # verbatim
    assert "tomasvegadesign" in v           # separators squashed
    assert "tomasvega-design" in v          # dotted -> dashed (github style)


def test_email_handle_strips_plus_addressing():
    assert email_handle_variants("larkfielding+news@gmail.com") == ["larkfielding"]


def test_email_handle_rejects_generic_and_short():
    assert email_handle_variants("info@acme.com") == []
    assert email_handle_variants("support@x.com") == []
    assert email_handle_variants("jo@x.com") == []          # too short
    assert email_handle_variants("") == []
    assert email_handle_variants(None) == []


def test_parse_github_api_user_real_shape():
    # The real-world response shape.
    body = ('{"login":"larkfielding","name":"Wren Jane Keeley",'
            '"company":"University of Miami","location":"Miami",'
            '"blog":"https://microlydee.wordpress.com/","bio":"bioinformatics","email":null}')
    f = parse_github_api_user(body)
    assert f["org"] == "University of Miami"
    assert f["location_city"] == "Miami"
    assert f["website"] == "https://microlydee.wordpress.com/"
    assert f["_github_name"] == "Wren Jane Keeley"
    assert "email" not in f  # null email must not become a field


def test_parse_github_api_user_not_found():
    assert parse_github_api_user('{"message":"Not Found"}') == {}
    assert parse_github_api_user("not json") == {}
    assert parse_github_api_user("") == {}


def test_parse_github_api_strips_company_at():
    f = parse_github_api_user('{"login":"x","company":"@acme-labs"}')
    assert f["org"] == "acme-labs"


# ── URL normalization ─────────────────────────────────────────────────────

def test_wikipedia_mobile_desktop_dedupe():
    a = normalize_url("https://en.wikipedia.org/wiki/Elon_Musk")
    b = normalize_url("https://en.m.wikipedia.org/wiki/Elon_Musk")
    assert a == b


def test_tracking_params_and_fragments_stripped():
    a = normalize_url("https://site.com/p?utm_source=x&id=7#frag")
    b = normalize_url("https://site.com/p/?id=7")
    assert a == b


def test_distinct_pages_stay_distinct():
    a = normalize_url("https://en.wikipedia.org/wiki/Sarah")
    b = normalize_url("https://en.wikipedia.org/wiki/Lydia")
    assert a != b


# ── Scout queries (still-valid behavior) ──────────────────────────────────

def test_queries_with_org():
    qs = build_scout_queries("Marisol Danver", org="Acme Corp")
    assert any("site:linkedin.com" in q for q in qs[:3])
    assert any(" AND " in q for q in qs)
    assert "Marisol Danver" in qs


def test_queries_strip_parentheticals():
    qs = build_scout_queries("Dana (pottery class)")
    assert all("(art Class)" not in q for q in qs), qs


def test_queries_use_occupation_and_city_when_org_missing():
    qs = build_scout_queries("Priya Venn", occupation="Engineering Leader",
                             location_city="San Francisco, CA")
    assert any("Engineering Leader" in q for q in qs), qs
    assert any("San Francisco" in q and "CA" not in q for q in qs), qs


def test_queries_dedupe_and_cap():
    qs = build_scout_queries("Test Person", org="Org", occupation="Occupation",
                             location_city="City")
    assert len(qs) == len(set(qs))
    assert len(qs) <= 8


def test_queries_for_person_none_fields():
    qs = build_scout_queries_for_person({
        "name": "Wren Keeley", "name_given": None, "name_family": None,
        "org": None, "occupation": None, "location_city": None,
    })
    assert len(qs) > 0


def test_clean_person_name():
    assert clean_person_name("Dana (pottery class)") == "Dana"
    assert clean_person_name('Robert "Bob" Jones') == "Robert Bob Jones"


# ── Robustness ────────────────────────────────────────────────────────────

def test_empty_and_garbage_inputs_do_not_crash():
    assert extract_links_from_page("", "", "")["links_found"] == 0
    extract_links_from_page("<<<>>>&&&", "", "X Y")
    verify_identity_from_page(None, "A B")
    assert normalize_url("") == ""
    assert build_scout_queries("") == []


def test_large_page_fast():
    import time as _t
    page = ("lorem ipsum dolor sit amet " * 40000) + " Carr appears once here"
    t0 = _t.time()
    extract_links_from_page(page, "", "Priya Venn")
    verify_identity_from_page(page, "Priya Venn")
    assert _t.time() - t0 < 5.0


if __name__ == "__main__":
    failures = 0
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    for n, f in fns:
        try:
            f()
            print(f"PASS {n}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {n}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    sys.exit(1 if failures else 0)
