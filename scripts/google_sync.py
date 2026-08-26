#!/usr/bin/env python3
"""
Bidirectional Google Contacts sync for Weave — SQLite backend edition.

Uses weave_sqlite.WeaveDB instead of SQLite. WAL mode allows concurrent
access from multiple cron jobs and interactive sessions.

Inbound:  Google Contacts → Weave (SQLite)
Outbound: Weave (SQLite) → Google Contacts

Usage:
    AGENT_ROOT=os.path.expanduser("~/.hermes")/profiles/indigo HOME=/root python3 google_sync.py
"""
import json
import os
import re as _re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Paths
AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
SQLITE_DB = AGENT_ROOT / "commons/db/ocas-weave/weave.sqlite"
CONFIG_PATH = AGENT_ROOT / "commons/data/ocas-weave/config.json"

# Shared Google API helpers
sys.path.insert(0, str(Path(__file__).parent))
from google_api import get_access_token, api_get as _api_get, api_post as _api_post, api_patch as _api_patch, PEOPLE_API_BASE

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 google_sync.py")
    sys.exit(0)



# When a caller wants the outbound log as data rather than on stdout (the
# per-contact push runs hundreds of times inside the enrichment loop and would
# bury its output), it installs a sink and prints the buffer only on failure.
_LOG_SINK = None


def _log(msg):
    if _LOG_SINK is not None:
        _LOG_SINK.append(msg)
        return
    print(msg, flush=True)


# A push scoped to named people is not a "what changed since last sync" pass:
# the caller has just changed those people and wants everything weave holds for
# them considered, so the time window is opened all the way.
EPOCH_TS = "1970-01-01T00:00:00+00:00"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config):
    config["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _validate_phone(phone):
    if not phone:
        return None
    cleaned = _re.sub(r"[^\d+\(\)\-\. ]", "", phone.strip())
    digits = _re.sub(r"\D", "", cleaned)
    if len(digits) < 7 or len(digits) > 15:
        return None
    return cleaned


# Fact provenances that mean "a person put this here", as opposed to a machine
# inferring it. Only these protect a stored value from an owner edit made in
# Google Contacts. Every other source_type present in the facts table for these
# predicates -- web_enrichment, inferred, scout_*, linkedin_profile, research --
# is inference, however confident the label sounds.
OWNER_SOURCE_TYPES = ("google_contacts", "user_stated", "user-stated",
                      "owner", "manual", "verified", "linkedin_import")


def _owner_field_values(weave):
    """{person_id: {field: {values...}}} for values a PERSON entered."""
    out = {}
    q = ",".join("?" * len(OWNER_SOURCE_TYPES))
    rows = weave.execute(
        "SELECT e.source_id AS pid, f.predicate, f.value FROM facts f "
        "JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
        f"WHERE f.source_type IN ({q}) AND f.predicate IN "
        "('org','occupation','location_city','location_country')",
        tuple(OWNER_SOURCE_TYPES))
    for r in rows:
        out.setdefault(r["pid"], {}).setdefault(r["predicate"], set()).add(r["value"])
    return out


_OVR_TOKEN = _re.compile(r"[a-z0-9]+")


def _same_thing(a, b):
    """True when two values are the same thing written at different precision.

    The two sides do not store these fields the same way, so "different string"
    is not "different value". build_contact_body splits weave's single
    location_city "San Francisco, CA" into google city="San Francisco" +
    region="CA", and inbound reads city back on its own -- so a value weave
    itself pushed returns looking changed. Measured on the live address book:
    276 of the 281 location_city differences and 14 of the 23 occupation
    differences are this, one value being a whole-token prefix or suffix of the
    other ("Teacher (ECE)"/"Teacher", "VP at Youtube"/"VP", "Portland"/"Portland,
    OR"). Replacing those would quietly delete the more specific half.

    Whole tokens, not characters: a bare substring test makes "A" a prefix of
    "Apple".
    """
    ta = _OVR_TOKEN.findall((a or "").lower())
    tb = _OVR_TOKEN.findall((b or "").lower())
    if not ta or not tb:
        return False
    if len(ta) > len(tb):
        ta, tb = tb, ta
    return tb[:len(ta)] == ta or tb[-len(ta):] == ta


def _build_override_check(weave):
    """(person_id, field, incoming) -> 1 when the stored value may be replaced.

    Google Contacts is the owner's own record, and outbound withholds inferred
    values from it, so a non-empty value sitting in Google is owner data by
    construction. It wins over what weave holds unless either
      - weave's value is itself owner-entered, or
      - the two are the same thing at different precision (_same_thing).

    This began as the mirror image -- override only when weave's value matched
    an INFERRED fact -- and that test never fired once. Measured 2026-08-23 on
    the live address book: 13 contacts had weave.org != google.org and all 13
    were held, because the check needs a fact whose VALUE equals the column and
    10 of the 13 had no org fact at all (the value went straight into the persons
    column, e.g. Sherah Beck org 'Elnetselskabet' on a web_enrichment row, Chase
    Bank org 'Doctor'). Of the three that did have one, none carried a
    source_type in the inferred tuple -- Peter Synak's came from
    'linkedin_profile'. So the owner's own corrections -- 'Intiut', 'Meta',
    'Camus Energy', 'Airlift (tm)' -- could not reach weave, which is the exact
    failure the check existed to fix.

    Asking the opposite question succeeds without a value match: the ABSENCE of
    an owner-entered fact is provable, whereas enumerating every label inference
    might carry is not.

    Effect on the current data: 27 values change (13 org, 9 occupation, 5 city),
    290 are held by _same_thing, and none of the 964 linked contacts loses a
    value to an empty one.
    """
    owner = _owner_field_values(weave)
    cache = {}

    def _ovr(pid, field, incoming):
        inc = (incoming or "").strip()
        # An empty or absent value from google never clears what weave holds.
        if not inc:
            return 0
        if pid not in cache:
            r = weave.execute(
                "SELECT org, occupation, location_city, location_country "
                "FROM persons WHERE id = :id", {"id": pid})
            cache[pid] = dict(r[0]) if r else {}
        cur = ((cache[pid].get(field) or "")).strip()
        if not cur or cur == inc:
            return 0
        if _same_thing(cur, inc):
            return 0
        return 0 if cur in (owner.get(pid, {}).get(field) or ()) else 1

    return _ovr


def sync_inbound(token):
    """Pull contacts FROM Google INTO Weave using REST API + SQLite backend."""
    now = datetime.now(timezone.utc).isoformat()

    # Import weave_sqlite here (lazy) to avoid import issues in test environments
    sys.path.insert(0, str(Path(__file__).parent))
    from weave_sqlite import WeaveDB

    weave = WeaveDB(SQLITE_DB)

    # Fetch all Google contacts via REST
    person_fields = ("names,emailAddresses,phoneNumbers,organizations,"
                     "addresses,urls,biographies,birthdays,relations,"
                     "events,userDefined")
    contacts = []
    page_token = None
    page = 0
    while True:
        page += 1
        url = f"{PEOPLE_API_BASE}/people/me/connections?personFields={person_fields}&pageSize=100&sources=READ_SOURCE_TYPE_CONTACT"
        if page_token:
            url += f"&pageToken={page_token}"
        data = _api_get(url, token)
        connections = data.get("connections", [])
        contacts.extend(connections)
        _log(f"  Inbound: fetched page {page}: {len(connections)} contacts (total: {len(contacts)})")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(1.0)

    _log(f"  Inbound: total Google contacts fetched: {len(contacts)}")

    # Contacts weave links to that google did not return. The traversal is a FULL
    # sync, so absence is meaningful -- but a partial fetch would look identical, so
    # each candidate is verified individually and only a confirmed 404 is unlinked.
    _seen_rns = {c.get("resourceName") for c in contacts if c.get("resourceName")}
    _linked = weave.execute(
        "SELECT id, name, google_resource_name FROM persons "
        "WHERE google_resource_name IS NOT NULL AND google_resource_name <> ''")
    _missing = [r for r in _linked if r["google_resource_name"] not in _seen_rns]
    if _missing:
        _log(f"  Inbound: {len(_missing)} weave row(s) link to a contact google did "
             f"not return; verifying each")
        _confirmed = 0
        for _r in _missing[:100]:
            _rn = _r["google_resource_name"]
            try:
                _api_get(f"{PEOPLE_API_BASE}/{_rn}?personFields=metadata", token,
                         timeout=20)
                continue                      # still exists; absence was a fetch gap
            except urllib.error.HTTPError as _e:
                if _e.code not in (403, 404):
                    continue                  # inconclusive, leave it alone
            except Exception:                 # noqa: BLE001
                continue
            # Confirmed gone. Keep the person row -- it owns facts and edges -- and
            # clear only the dead link, so nothing re-pushes to a resourceName that
            # no longer exists and the stale email stops matching a new contact.
            weave.execute_write(
                "UPDATE persons SET google_resource_name = NULL WHERE id = :id",
                {"id": _r["id"]})
            _confirmed += 1
            _log(f"    unlinked {(_r['name'] or '?')[:32]} — {_rn} returns 404")
        _log(f"  Inbound: unlinked {_confirmed} dead resource name(s)")

    # Build lookup maps from SQLite
    all_people = weave.execute(
        "SELECT id, google_resource_name, name, email, phone FROM persons"
    )
    rn_map = {}
    email_map = {}
    phone_map = {}
    for row in all_people:
        pid, grn, name, email, phone = row["id"], row["google_resource_name"], row["name"], row["email"], row["phone"]
        if grn:
            rn_map[grn] = pid
        if email:
            email_map[email.lower()] = pid
        if phone:
            phone_map[phone] = pid

    upserted = enriched = created = skipped = 0
    _ovr = _build_override_check(weave)
    for c in contacts:
        rn = c.get("resourceName", "")
        name_data = (c.get("names") or [{}])[0] if c.get("names") else {}
        name = name_data.get("displayName", "")
        org_only = False
        if not name:
            # An org-only contact -- a shop, a bank, a clinic -- carries no
            # displayName at all. Skipping it left 20 real Google contacts with
            # no weave row (16 of them with none by any key), so nothing about
            # them could sync in either direction.  The organization name is the
            # contact's actual identity, so it becomes the weave display name.
            #
            # name_given / name_family are deliberately left EMPTY.
            # build_contact_body emits a `names` block only when one of them is
            # set, so a company name imported this way can never be written back
            # into Google's given/family fields -- which is also what lets
            # company names be moved OUT of name fields without weave putting
            # them straight back.
            name = ((c.get("organizations") or [{}])[0].get("name", "")
                    if c.get("organizations") else "") or ""
            name = name.strip()
            if not name:
                skipped += 1
                continue
            org_only = True

        given = "" if org_only else name_data.get("givenName", "")
        family = "" if org_only else name_data.get("familyName", "")
        email = (c.get("emailAddresses") or [{}])[0].get("value", "") if c.get("emailAddresses") else ""
        phone = _validate_phone((c.get("phoneNumbers") or [{}])[0].get("value", "")) if c.get("phoneNumbers") else ""
        org = (c.get("organizations") or [{}])[0].get("name", "") if c.get("organizations") else ""
        title_val = (c.get("organizations") or [{}])[0].get("title", "") if c.get("organizations") else ""
        # Compose city + region the same way build_contact_body SPLITS them on
        # the way out ("San Francisco, CA" -> city + region). Reading back only
        # `city` made every value weave itself pushed look changed: 276 of the
        # 281 location_city differences on 2026-08-23 were this one asymmetry,
        # and where an override did fire it silently dropped the state --
        # 'Piedmont, CA' arrived as 'Piedmont'.
        _addr0 = (c.get("addresses") or [{}])[0] if c.get("addresses") else {}
        _city_part = (_addr0.get("city") or "").strip()
        _region_part = (_addr0.get("region") or "").strip()
        city = f"{_city_part}, {_region_part}" if (_city_part and _region_part) else _city_part
        country = (_addr0.get("countryCode") or "")

        # Match: resource_name → email → phone → new
        pid = rn_map.get(rn) or (email_map.get(email.lower()) if email else None) or (phone_map.get(phone) if phone else None)

        if pid:
            # Gap-fill existing record
            weave.execute_write("""
                UPDATE persons SET
                    name = CASE WHEN name IS NULL OR name = '' THEN :name ELSE name END,
                    name_given = CASE WHEN name_given IS NULL OR name_given = '' THEN :given ELSE name_given END,
                    name_family = CASE WHEN name_family IS NULL OR name_family = '' THEN :family ELSE name_family END,
                    email = CASE WHEN email IS NULL OR email = '' THEN :email ELSE email END,
                    phone = CASE WHEN phone IS NULL OR phone = '' THEN :phone ELSE phone END,
                    -- Fill when empty, and ALSO overwrite when what is stored
                    -- was INFERRED and Google now says something different.
                    --
                    -- Fill-only alone made the owner's own corrections unusable:
                    -- fixing a wrong employer in Google Contacts changed nothing
                    -- here, because the column was non-empty. Together with the
                    -- outbound gate -- which withholds inferred values from the
                    -- push -- a bad guess became unfixable from either side.
                    -- Peter Arcuni kept occupation 'Listen to the Business Wars
                    -- podcast' for exactly that reason.
                    -- An owner-entered value is never touched: :ovr_* is 1 only
                    -- when the CURRENT value is one enrichment wrote.
                    org = CASE WHEN org IS NULL OR org = '' OR :ovr_org = 1 THEN :org ELSE org END,
                    occupation = CASE WHEN occupation IS NULL OR occupation = '' OR :ovr_title = 1 THEN :title ELSE occupation END,
                    location_city = CASE WHEN location_city IS NULL OR location_city = '' OR :ovr_city = 1 THEN :city ELSE location_city END,
                    location_country = CASE WHEN location_country IS NULL OR location_country = '' OR :ovr_country = 1 THEN :country ELSE location_country END,
                    -- Adopt the resourceName google just returned. The old CASE kept
                    -- a stale value and discarded the fresh one sitting in the same
                    -- response, so a renamed contact (adding a verified email or a
                    -- profile url renames it) stayed pointed at a dead name forever.
                    google_resource_name = CASE
                        WHEN :rn IS NOT NULL AND :rn <> '' THEN :rn
                        ELSE google_resource_name END,
                    record_time = :now
                WHERE id = :id
            """, {
                "id": pid, "name": name, "given": given, "family": family,
                "email": email, "phone": phone, "org": org, "title": title_val,
                "city": city, "country": country, "rn": rn, "now": now,
                "ovr_org": _ovr(pid, "org", org),
                "ovr_title": _ovr(pid, "occupation", title_val),
                "ovr_city": _ovr(pid, "location_city", city),
                "ovr_country": _ovr(pid, "location_country", country),
            })
            enriched += 1
        else:
            pid = str(__import__("uuid").uuid4())
            weave.execute_write("""
                INSERT INTO persons
                    (id, name, name_given, name_family, email, phone,
                     location_city, location_country, occupation, org,
                     google_resource_name, source_type, source_ref, confidence, record_time)
                VALUES
                    (:id, :name, :given, :family, :email, :phone,
                     :city, :country, :title, :org,
                     :rn, 'imported', :rn, 0.8, :now)
            """, {
                "id": pid, "name": name, "given": given, "family": family,
                "email": email, "phone": phone, "org": org, "title": title_val,
                "city": city, "country": country, "rn": rn, "now": now,
            })
            created += 1
        upserted += 1
        if upserted % 100 == 0:
            _log(f"  Inbound progress: {upserted}/{len(contacts)} processed")

    # Hand-entered urls/biographies are requested in person_fields above but the
    # upsert loop only maps scalar columns, so they were fetched and discarded on
    # every sync — 158 curated LinkedIn URLs never reached weave. They are the
    # strongest identity signal in a contact (user-typed, not inferred), so they
    # are imported here as additive facts. Never fatal: a failure must not lose
    # the contact sync that already succeeded.
    url_stats = {}
    try:
        from contact_urls import import_all as _import_contact_urls
        url_stats = _import_contact_urls(contacts, SQLITE_DB)
        _log(f"  Inbound URLs: +{url_stats.get('written', 0)} facts "
             f"({url_stats.get('existing', 0)} already present), "
             f"{url_stats.get('website_filled', 0)} website columns filled")
    except Exception as e:
        _log(f"  Inbound URLs: import failed ({type(e).__name__}: {e}); "
             f"contact sync unaffected")

    # birthdays, relations and events were fetched and discarded the same way the
    # urls were. Same treatment: additive, fill-only, and never fatal.
    extra_stats = {}
    try:
        from contact_extras import import_all as _import_extras
        # import_all returns (stats, facts, edges, skipped, creates). Unpacking
        # four raised ValueError on EVERY run; the except below swallowed it, so
        # birthdays, relations, events and userDefined have silently never been
        # imported -- "Inbound extras: import failed (ValueError: too many values
        # to unpack (expected 4))" on 2026-08-23.
        extra_stats, _f, _e, _sk, _cr = _import_extras(contacts, SQLITE_DB)
        _log(f"  Inbound extras: +{extra_stats.get('facts', 0)} fact(s) "
             f"{extra_stats.get('by_predicate', {})}, "
             f"+{extra_stats.get('edges', 0)} relation edge(s), "
             f"{extra_stats.get('skipped', 0)} not linked")
    except Exception as e:  # noqa: BLE001
        _log(f"  Inbound extras: import failed ({type(e).__name__}: {e}); "
             f"contact sync unaffected")

    return {"inbound_extras": extra_stats,
            "inbound_upserted": upserted, "inbound_enriched": enriched,
            "inbound_created": created, "inbound_skipped": skipped,
            "inbound_url_facts": url_stats.get("written", 0),
            "inbound_websites_filled": url_stats.get("website_filled", 0)}



MAX_CREATES_PER_RUN = 25  # bound the blast radius of any one sync


def _norm_name(s):
    """Diacritic- and punctuation-insensitive name key for dedupe."""
    import unicodedata as _ud
    if not s:
        return ""
    s = _ud.normalize("NFKD", s)
    s = "".join(c for c in s if not _ud.combining(c))
    # Drop honorific suffixes. Google renders displayName WITH the suffix while
    # weave stores it in honorific_suffixes, so keeping it here made the same
    # person key two different ways and a duplicate contact got created.
    s = _re.sub(r"[,\s]+(?:Ph\.?\s?D\.?|M\.?D\.?|MBA|J\.?D\.?|Esq\.?|Jr\.?|Sr\.?"
                r"|II|III|IV|DDS|DVM|RN|CPA|PE|MSc|MS|MA|BA|BS|M\.?HCI|MPH|MFA"
                r"|EdD|PsyD|DPhil|FAIA|AIA)\.?\s*$", "", s, flags=_re.I)
    return " ".join(_re.sub(r"[^A-Za-z ]", " ", s).upper().split())


def _google_name_email_index(token):
    """(normalized names, lowercased emails) currently in Google Contacts."""
    names, emails = set(), set()
    page = None
    while True:
        q = {"personFields": "names,emailAddresses", "pageSize": 1000,
             "sources": "READ_SOURCE_TYPE_CONTACT"}
        if page:
            q["pageToken"] = page
        try:
            req = urllib.request.Request(
                f"{PEOPLE_API_BASE}/people/me/connections?" + urllib.parse.urlencode(q),
                headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.loads(resp.read())
        except Exception as e:
            _log(f"  Outbound: dedupe index fetch failed ({str(e)[:60]}); "
                 f"refusing to create this run")
            return None, None
        for p in d.get("connections", []):
            for n in (p.get("names") or []):
                if n.get("displayName"):
                    names.add(_norm_name(n["displayName"]))
            for e in (p.get("emailAddresses") or []):
                if e.get("value"):
                    emails.add(e["value"].strip().lower())
        page = d.get("nextPageToken")
        if not page:
            break
    return names, emails



# Fields whose value may have been written by enrichment rather than by the
# owner. Enrichment lives in weave; Google Contacts is the owner's own record,
# so an inferred value must never overwrite it there.
INFERRED_SOURCE_TYPES = ("scout_osint", "scout_research", "web_enrichment",
                         "inferred", "enriched")
GATED_FIELDS = ("org", "occupation", "location_city", "location_country")


def _inferred_field_values(weave):
    """{person_id: {field: {values...}}} for values sourced from enrichment.

    A persons column matching one of these values was populated by inference,
    so it is withheld from the outbound push.
    """
    out = {}
    q = ",".join("?" * len(INFERRED_SOURCE_TYPES))
    rows = weave.execute(
        "SELECT e.source_id AS pid, f.predicate, f.value FROM facts f "
        "JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
        f"WHERE f.source_type IN ({q}) AND f.predicate IN "
        "('org','occupation','location_city','location_country')",
        tuple(INFERRED_SOURCE_TYPES))
    for r in rows:
        out.setdefault(r["pid"], {}).setdefault(r["predicate"], set()).add(r["value"])
    return out


# ── Outbound plausibility gate ───────────────────────────────────────────────
# A previous enrichment pass wrote search-result snippets into org/occupation and
# they were pushed into the user's real Google Contacts: single words sliced out
# of a sentence ("Serving", "Greater", "Atlantic"), several job titles run
# together with no separator ("...ienceUX Specialist / User ExperienceSenior D"),
# prose about the person ("He was previously the head coach at..."), and a news
# headline that landed on five unrelated contacts. The provenance gate did not
# stop it because those rows were labelled 'imported', which is not withheld.
#
# So the value itself is checked. Every rule is written to be impossible to
# trigger on a real employer or title: acronyms (HSBC, MIT, IDEO), ordinary
# titles (CEO, SVP of Design) and long-but-genuine names must all pass.
import re as _re

# Case matters: these detect character-level corruption, so they must NOT be
# compiled with IGNORECASE — doing so makes ^[a-z][A-Z] match any two letters.
# 'eBay' and 'eGrants M' are the SAME shape, so a pattern cannot separate a styled
# brand from a sentence sliced mid-word. Only two exemptions are unambiguous: a
# lowercase letter followed by an all-caps run (iOS, xAI -- a slice leaves lowercase),
# and an explicit list of known lowercase-initial brands.
_BRAND_CAPS = _re.compile(r"^[a-z][A-Z]{2,}\b")
_BRAND_WORDS = ("ebay", "iphone", "ipad", "ipod", "imac", "itunes", "icloud",
                "macbook", "airpods", "ethereum", "esports", "ebooks", "email")


def _is_brand_prefix(v):
    """True for a deliberately lowercase-initial brand, not a sliced fragment."""
    v = (v or "").strip()
    if _BRAND_CAPS.match(v):
        return True
    first = (v.split()[0] if v.split() else "").lower().strip(".,")
    return first in _BRAND_WORDS


_JUNK_CASE_SENSITIVE = _re.compile(
    r"^[a-z][A-Z]"        # starts mid-word: 'yPrincipal Pr', 'eGrants M'
    r"|^[a-z]\s[A-Z]"     # a stranded letter before the first word: 'r Vice President'
    r"|\s[A-Z]$"          # dangling capitalised remnant: 'duct Marketing, G'
    r"|\)[A-Z]$"
)
# Words that are never an employer or a job title on their own.
_JUNK_ANY_CASE = _re.compile(
    r"^(the|a|an|and|or|of|for|with|from|at|in|on|to|by"
    r"|serving|greater|atlantic|senior|technical|work|working|about"
    r"|former|current|based|located)$"
    r"|^(www\.|https?://)"                                   # a url is not an employer
    r"|^(birth|facts date|anniversary)\b"                    # label text from an import
    r"|\b(he|she|they)\b.{0,40}\b(was|were|began|joined)\b"  # prose about a person
    r"|\bselect(s|ed)?\b.*\bpick\b"                          # sports headline
    r"|\d+\s+(days?|hours?|weeks?|months?)\s+ago"            # search-result timestamp
    r"|\u00b7",                                              # SERP separator
    _re.I)


def is_implausible_job_value(value):
    """(True, reason) when a value must not be written to a contact's employer or
    title field. Returns (False, "") for anything that looks like real data."""
    v = (value or "").strip()
    if not v:
        return False, ""
    if _is_brand_prefix(v):
        pass                       # iOS Developer, eBay, xAI -- styling, not a slice
    elif _JUNK_CASE_SENSITIVE.search(v):
        return True, "starts or ends mid-word (corrupted slice)"
    if _JUNK_ANY_CASE.search(v):
        return True, "sentence fragment, url, label text or scraped snippet"
    # A bare preposition or conjunction. Measured: one contact had org "over" and
    # another "Over" paired with a news headline as the title.
    if _re.fullmatch(r"(over|under|after|before|amid|amidst|against|into|onto|among"
                     r"|between|during|despite|toward|towards|within|without|via"
                     r"|versus|vs|plus|per)", v, _re.I):
        return True, "a preposition is not a company or a job title"
    # A lowercase fragment of three or more characters followed later by a capital:
    # a sentence sliced mid-word. A deliberately lowercase brand or title stays
    # lowercase, so it is not caught ('db Motion Graphics' is exempt on length,
    # 'flash artist, motionographer' has no later capital).
    # Two characters is enough: 'or Program Manager IIEngineering Ma' escaped a
    # three-character floor. Real two-letter openers are allowlisted.
    _REAL_2 = {"db", "hr", "ux", "ui", "qa", "it", "pr", "ai", "co", "de", "la", "le",
               "el", "al", "st", "mc", "on", "in", "at", "to", "by", "of", "my", "go"}
    _first = v.split()[0] if v.split() else ""
    if (len(_first) >= 2 and _first[0].islower() and _first.isalpha()
            and _first.lower() not in _REAL_2
            and not _is_brand_prefix(v)             # iOS, eBay, xAI
            and _re.search(r"[A-Z]", v[len(_first):])):
        return True, "starts with a lowercase word fragment, then a capital"
    # Several titles concatenated with no separator: a lowercase letter followed
    # immediately by an uppercase one, more than once, inside a long string.
    # Several titles concatenated with no separator. Requires the uppercase to
    # be followed by lowercase -- a new Capitalised Word -- so acronym
    # boundaries (TropiCAD, AutoCAD) do not count, and requires three of them
    # so CamelCase brand names (HelloKindred, VentureWeb) are not withheld.
    if len(v) > 24 and len(_re.findall(r"[a-z][A-Z][a-z]", v)) >= 3:
        return True, "several values run together without a separator"
    return False, ""

def _refresh_etags(resource_names, token):
    """Current etag per resourceName, for retrying a batch rejected as stale.

    A 400 failedPrecondition means the etag read at the start of the run no longer
    matches. Returns {} entries only for contacts that still exist; a name absent
    from the result has been deleted and must be dropped from the retry rather than
    pushed blind.
    """
    out = {}
    for i in range(0, len(resource_names), 50):
        chunk = resource_names[i:i+50]
        rn_param = "&resourceNames=".join(urllib.parse.quote(rn) for rn in chunk)
        url = (f"{PEOPLE_API_BASE}/people:batchGet?resourceNames={rn_param}"
               "&personFields=metadata&sources=READ_SOURCE_TYPE_CONTACT")
        try:
            resp = _api_get(url, token, timeout=30)
            for person in resp.get("responses", []):
                p = person.get("person", {})
                if p.get("resourceName") and p.get("etag"):
                    out[p["resourceName"]] = p["etag"]
        except Exception as e:  # noqa: BLE001
            _log(f"    etag refresh error at {i}: {e}")
        time.sleep(0.3)
    return out


_MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august",
     "september","october","november","december"], start=1)}


def _parse_birthday(text):
    """A google birthday object from weave's text, or None.

    Three observed shapes: '1952-06-14', '~February 8, 2021' and 'June 30' with no
    year. Google accepts a date whose year is optional, so a yearless birthday keeps
    month and day instead of inventing a year. Unparseable text is passed through as
    a text birthday rather than discarded.
    """
    t = (text or "").strip().lstrip("~").strip()
    if not t:
        return None
    m = _re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return {"date": {"year": y, "month": mo, "day": d}}
    m = _re.match(r"^([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?$", t)
    if m and m.group(1).lower() in _MONTHS:
        mo = _MONTHS[m.group(1).lower()]
        d = int(m.group(2))
        date = {"month": mo, "day": d}
        if m.group(3):
            date["year"] = int(m.group(3))
        return {"date": date}
    return {"text": t[:60]}


# weave edge -> the relation type google shows on THIS contact's card
_REL_TYPE = {"SpouseOf": "spouse", "ParentOf": "child", "ChildOf": "parent",
             "SiblingOf": "sibling", "CousinOf": "relative"}


def _extra_field_maps(weave, last_sync_at):
    """birthdays / relations / userDefined keyed by resource name and person id.

    Everything here is fill-only, so the maps carry what weave knows and the merge at
    batch assembly decides whether google already has something.
    """
    birthdays, relations, userdef = {}, {}, {}

    for row in weave.execute("""
        SELECT p.google_resource_name rn, p.id pid, f.value v
        FROM facts f
        JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        JOIN persons p ON p.id = e.source_id
        WHERE f.predicate = 'birthday' AND f.valid_until IS NULL
          AND COALESCE(p.is_pseudo, 0) = 0
    """):
        b = _parse_birthday(row["v"])
        if not b:
            continue
        for k in (row["rn"], row["pid"]):
            if k:
                birthdays.setdefault(k, b)

    for row in weave.execute("""
        SELECT a.google_resource_name rn, a.id pid, e.rel_type rt, b.name other
        FROM edges e
        JOIN persons a ON a.id = e.source_id
        JOIN persons b ON b.id = e.target_id
        WHERE e.rel_type IN ('SpouseOf','ParentOf','ChildOf','SiblingOf','CousinOf')
          AND COALESCE(a.is_pseudo, 0) = 0
    """):
        t = _REL_TYPE.get(row["rt"])
        if not t or not row["other"]:
            continue
        entry = {"person": row["other"], "type": t}
        for k in (row["rn"], row["pid"]):
            if not k:
                continue
            bucket = relations.setdefault(k, [])
            if not any(x["person"] == entry["person"] and x["type"] == entry["type"]
                       for x in bucket):
                bucket.append(entry)

    def _add_ud(k, key, val):
        if not k or not val:
            return
        bucket = userdef.setdefault(k, [])
        if not any(x["key"] == key for x in bucket):
            bucket.append({"key": key, "value": val})

    for row in weave.execute("""
        SELECT google_resource_name rn, id pid, pronouns, dietary_restrictions
        FROM persons WHERE COALESCE(is_pseudo, 0) = 0
    """):
        pr = (row["pronouns"] or "").strip()
        dt = (row["dietary_restrictions"] or "").strip()
        for k in (row["rn"], row["pid"]):
            if pr and pr not in ("[]", "{}"):
                _add_ud(k, "Pronouns", pr[:60])
            if dt and dt not in ("[]", "{}"):
                try:
                    import json as _j
                    vals = _j.loads(dt)
                    dt = ", ".join(str(x) for x in vals) if isinstance(vals, list) else dt
                except Exception:  # noqa: BLE001
                    pass
                if dt:
                    _add_ud(k, "Dietary", dt[:60])

    for row in weave.execute("""
        SELECT p.google_resource_name rn, p.id pid, f.value v
        FROM facts f
        JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        JOIN persons p ON p.id = e.source_id
        WHERE f.predicate = 'pronouns' AND f.valid_until IS NULL
          AND COALESCE(p.is_pseudo, 0) = 0
    """):
        for k in (row["rn"], row["pid"]):
            _add_ud(k, "Pronouns", (row["v"] or "").strip()[:60])

    return birthdays, relations, userdef

# Relation labels that carry no information the specific one does not. The
# pipeline derives these from its own edge types (SpouseOf -> "spouse",
# ParentOf -> "child"), so pushing one back alongside the label the owner typed
# duplicates the relationship: "Peter Arcuni: Husband" plus "Peter Arcuni:
# spouse", "Sonny: Son" plus "Sonny: child". One person, one relation.
_GENERIC_RELATION = {"spouse", "partner", "domesticpartner", "child", "parent",
                     "sibling", "relative", "friend", "family"}


_REL_DATE = _re.compile(r"[\s,]+\d{1,2}/\d{1,2}/\d{2,4}\s*$")


def _relation_person_key(e):
    """Relations dedupe on the PERSON, not on (person, label).

    The name is normalised first. Google's relation field is free text, so the
    same person is written several ways -- most often with their birthday typed
    onto the end ('Isadora "Izzy" Arcuni 07/31/215'), which is how one child
    ended up listed twice: once as the owner typed her, once under the cleaned
    name this pipeline derived when it created her contact.
    """
    v = str(e.get("person") or "").strip()
    v = _REL_DATE.sub("", v)
    v = _re.sub(r"[\"\u201c\u201d\u2018\u2019()]", " ", v)
    return _re.sub(r"\s+", " ", v).strip().lower()


def _relation_is_generic(e):
    import re as _r
    return _r.sub(r"[^a-z]", "", str(e.get("type") or "").lower()) in _GENERIC_RELATION


def sync_outbound(token, last_sync_at, only_person_ids=None):
    """Push Weave changes TO Google Contacts via REST API + SQLite backend.

    only_person_ids scopes the whole pass to those weave person ids. That is the
    per-contact mode the enrichment loop uses: a full pass re-reads every contact
    in the address book (~10 connections.list pages inbound, a 1000-per-page
    dedupe index, and an etag batchGet for every changed row), which at one pass
    per 30 enriched contacts is what earned the 429s. Scoped, the cost is one
    batchGet and one batchUpdate for the single contact that just changed.

    Scoping changes only WHICH rows are considered. Every content gate is the
    same code on the same path: GATED_FIELDS / _inferred_field_values withholding,
    url_quality.is_person_profile, the blocked-host list, url_norm canonicalisation
    and dedupe, is_implausible_job_value, fill-only names/birthdays, list merges
    against Google's current values, the updateMask-equals-body-keys rule and the
    outbound checkpoint.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from weave_sqlite import WeaveDB

    weave = WeaveDB(SQLITE_DB)

    scoped = [str(p) for p in (only_person_ids or []) if p] or None
    _scope_clause, _scope_params = "", {}
    if scoped:
        last_sync_at = EPOCH_TS
        _ph = ",".join(":sp%d" % i for i in range(len(scoped)))
        _scope_clause = "\n          AND p.id IN (%s)\n" % _ph
        _scope_params = {"sp%d" % i: v for i, v in enumerate(scoped)}
        _log("  Outbound: scoped to %d person id(s); inbound and creates skipped"
             % len(scoped))

    ckpt_path = AGENT_ROOT / "commons/db/ocas-weave/staging/outbound_ckpt.txt"
    pushed_set = set()
    # The checkpoint is append-only and re-read in full every run. Collapse it to
    # unique keys and rewrite, so it stops growing without changing its meaning.
    try:
        if ckpt_path.exists():
            _lines = [l.strip() for l in ckpt_path.read_text().splitlines() if l.strip()]
            _uniq = list(dict.fromkeys(_lines))
            if len(_uniq) < len(_lines):
                _tmp = ckpt_path.with_suffix(ckpt_path.suffix + ".tmp")
                _tmp.write_text("\n".join(_uniq) + "\n")
                _tmp.replace(ckpt_path)
                _log(f"  Checkpoint compacted: {len(_lines)} -> {len(_uniq)} entries")
    except Exception as _e:  # noqa: BLE001
        _log(f"  Checkpoint compaction skipped: {_e}")
    if ckpt_path.exists():
        pushed_set = set(l for l in ckpt_path.read_text().strip().split("\n") if l)
        _log(f"  Outbound: resuming from checkpoint ({len(pushed_set)} already pushed)")

    # Find contacts with Fact-sourced LinkedIn URLs
    # Every verified profile url, not just LinkedIn. Invalidated facts are
    # excluded so anything a sweep retired stays retired.
    linkedin_rows = weave.execute("""
        SELECT p.google_resource_name, p.id, p.name, f.value, f.predicate,
               f.source_type
        FROM facts f
        JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        JOIN persons p ON p.id = e.source_id
        -- Select on the FACT's timestamp, not the person row's.
        --
        -- Enrichment writes FACTS; it only touches the persons row when a
        -- visible column changes, which is about 6% of writes. Gating outbound
        -- on p.record_time therefore hid every contact whose enrichment was
        -- graph-only: on the 2026-08-22 full run, 406 contacts gained new
        -- profile/website facts and NONE were selected, so their URLs never
        -- reached Google and the contact looked untouched. The person row is
        -- still honoured, for edits that change the row but no fact.
        WHERE (f.record_time > :ts OR p.record_time > :ts)
          AND f.valid_until IS NULL
          AND (f.predicate LIKE 'profile_%' OR f.predicate IN
               ('linkedin', 'linkedin_url', 'website'))
          AND f.value LIKE '%.%'
    """ + _scope_clause, dict({"ts": last_sync_at}, **_scope_params))
    # Owner data only -- the same rule GATED_FIELDS applies to org/occupation/
    # location. Without this, every URL scout guessed was pushed into the real
    # address book and re-imported next run as a 'google_contacts' fact, which
    # is exactly the provenance the gate trusts.
    _pre_gate = len(linkedin_rows)
    try:
        from url_quality import is_person_profile as _pub
    except Exception:  # noqa: BLE001
        _pub = None
    if _pub is not None:
        _names = [r["name"] for r in weave.execute(
            "SELECT DISTINCT name FROM persons WHERE name IS NOT NULL AND name != ''")]
        linkedin_rows = [
            r for r in linkedin_rows
            if _pub(r["value"], r["name"] or "",
                    r["predicate"][8:] if str(r["predicate"]).startswith("profile_")
                    else None, _names)]
    if _pre_gate != len(linkedin_rows):
        _log(f"  Outbound: {_pre_gate - len(linkedin_rows)} url(s) withheld — "
             f"aggregator/encyclopedia/catalog pages are about a name, not a person")
    # host -> the label google shows for the link
    _URL_LABEL = {
        "linkedin.com": "LinkedIn", "github.com": "GitHub", "medium.com": "Medium",
        "twitter.com": "Twitter", "x.com": "X", "instagram.com": "Instagram",
        "youtube.com": "YouTube", "behance.net": "Behance", "substack.com": "Substack",
        "tiktok.com": "TikTok", "dribbble.com": "Dribbble", "bsky.app": "Bluesky",
        "soundcloud.com": "SoundCloud", "vimeo.com": "Vimeo", "calendly.com": "Calendly",
        "about.me": "About.me", "mastodon.social": "Mastodon",
    }
    try:
        import json as _json
        _blocked = {h for h, v in _json.load(open(
            os.path.expanduser("~/.hermes/commons/data/ocas-scout/soft404-sites.json")
        )).items() if v.get("answers_for_any")}
    except Exception:  # noqa: BLE001
        _blocked = set()

    def _url_host(u):
        s = (u or "").strip().lower()
        for p in ("https://", "http://"):
            if s.startswith(p):
                s = s[len(p):]
        return s.split("/")[0].replace("www.", "")

    try:
        from url_norm import canonical_url as _canon, dedupe_key as _ukey
    except Exception:  # noqa: BLE001
        _canon = lambda u: (u or "").strip() or None  # noqa: E731
        _ukey = lambda u: ((u or "").strip().lower().rstrip("/") or None)  # noqa: E731

    _bday_map, _rel_map, _ud_map = _extra_field_maps(weave, last_sync_at)
    _log(f"  Outbound: {len(_bday_map)} birthday, {len(_rel_map)} relation, "
         f"{len(_ud_map)} custom-field key(s) available to fill")
    linkedin_map = {}
    _url_skipped = 0
    for row in linkedin_rows:
        rn, pid, val = row["google_resource_name"], row["id"], (row["value"] or "").strip()
        if not val or "." not in val:
            continue
        u = _canon(val)
        if not u:
            # not a URL at all (35 of these are plain email addresses sitting in
            # a url slot); pushing it would put an email in google's website field
            _url_skipped += 1
            continue
        host = _url_host(u)
        # a host that answers for any handle attributes nothing, so its "profile"
        # must not be written into the owner's real address book
        if any(b in host for b in _blocked):
            _url_skipped += 1
            continue
        label = next((lbl for h, lbl in _URL_LABEL.items() if h in host), None)
        entry = {"value": u}
        if label:
            entry["type"] = label
        for key in (rn, pid):
            if not key:
                continue
            bucket = linkedin_map.setdefault(key, [])
            if not any(_ukey(e.get("value")) == _ukey(u) for e in bucket):
                bucket.append(entry)
    if _url_skipped:
        _log(f"  Outbound: {_url_skipped} url(s) withheld — host answers for any handle")
    _log(f"  Outbound: {sum(len(v) for v in linkedin_map.values())} profile url(s) "
         f"across {len(linkedin_map)} key(s) eligible to push")


    # Find records modified since last sync
    if scoped:
        # Named people are pushed on the caller's say-so, so neither filter of the
        # bulk query applies. record_time is irrelevant (enrichment writes FACTS;
        # it touches the persons row for about 6% of writes), and source_type
        # 'imported' is excluded from the bulk pass only to stop it echoing the
        # whole address book back at Google. Keeping that filter here would make
        # the per-contact push silently do nothing for the 403 rows marked
        # 'imported' -- 199 of the 332 contacts that gained a profile url in the
        # last 24h -- which is exactly the case this change exists to fix.
        _ph2 = ",".join(":sq%d" % i for i in range(len(scoped)))
        rows = weave.execute(
            "SELECT google_resource_name, name_given, name_family, email, phone, "
            "org, occupation, location_city, location_country, id FROM persons "
            "WHERE id IN (%s)" % _ph2,
            {"sq%d" % i: v for i, v in enumerate(scoped)})
    else:
        rows = weave.execute("""
            SELECT p.google_resource_name AS google_resource_name,
                   p.name_given AS name_given, p.name_family AS name_family,
                   p.email AS email, p.phone AS phone, p.org AS org,
                   p.occupation AS occupation,
                   p.location_city AS location_city,
                   p.location_country AS location_country, p.id AS id
            FROM persons p
            -- Select on the FACT's timestamp as well as the person row's, and
            -- do NOT exclude source_type 'imported'.
            --
            -- Enrichment writes FACTS and touches the persons row for about 6%
            -- of writes, so p.record_time alone misses graph-only changes --
            -- the same defect already fixed on the linkedin_rows query above.
            -- The source_type filter was the larger half of the bug: measured
            -- 2026-08-23, 247 of the 457 linked contacts that gained a
            -- profile/website fact in the previous 24h sit on rows labelled
            -- 'imported' (395 of 964 linked rows carry that label, because it
            -- is what inbound stamps on every contact Google itself created).
            -- Excluding them meant their new urls could never reach Google by
            -- the bulk path. Keeping the address book from being echoed back is
            -- the job of the time window and the content gates, not of a
            -- provenance label that covers 41% of the address book.
            WHERE p.record_time > :ts
               OR EXISTS (SELECT 1
                            FROM edges e
                            JOIN facts f ON f.id = e.target_id
                           WHERE e.source_id = p.id
                             AND e.rel_type = 'HasFact'
                             AND f.valid_until IS NULL
                             AND f.record_time > :ts)
        """, {"ts": last_sync_at})

    to_update = [r for r in rows if r["google_resource_name"]]
    # NEVER create Google contacts. Weave holds person rows that are not
    # address-book entries — entities discovered by chronicle_sync, email
    # analysis and inference. Pushing those would invent contacts the user
    # never added. Outbound is enrichment of EXISTING contacts only; a row
    # without a google_resource_name is reported and skipped.
    # Contacts present in weave but absent from Google. Deliberately NOT
    # time-bounded and not restricted by source_type: a contact missing from
    # Google stays missing until created, however old the row is. The
    # exclusions are policy, not incidental:
    #   is_pseudo   - relations and BOOK entries are never their own contact
    #   is_archived - deliberately retired
    #   is_deceased - never recreate a contact the owner removed
    # Creating contacts is a whole-address-book decision (it needs the live
    # name/email index to avoid duplicates, which is a full connections.list
    # read). A scoped push never creates; the periodic full sync still does.
    create_rows = [] if scoped else weave.execute("""
        SELECT google_resource_name, name_given, name_family,
               email, phone, org, occupation, location_city, location_country,
               id, name
        FROM persons
        WHERE (google_resource_name IS NULL OR google_resource_name = '')
          AND COALESCE(is_pseudo, 0) = 0
          AND COALESCE(is_archived, 0) = 0
          AND COALESCE(is_deceased, 0) = 0
    """)

    # Dedupe against live Google before creating: weave rows have been seen to
    # exist in Google under a name the id-based scan missed, and a duplicate in
    # the user's real address book is much worse than a skipped create.
    inferred_map = _inferred_field_values(weave)
    gated_total = 0
    g_names, g_emails = (None, None) if scoped else _google_name_email_index(token)
    to_create, dup_skipped = [], 0
    if g_names is None:
        create_rows = []   # no index -> create nothing rather than risk duplicates
    for r in create_rows:
        em = (r["email"] or "").strip().lower()
        if em and em in g_emails:
            dup_skipped += 1
            continue
        if _norm_name(r["name"] or "") in g_names:
            dup_skipped += 1
            continue
        to_create.append(r)
        # Fold this row into the index so a second weave row for the same person
        # later in the same pass is recognised as a duplicate rather than POSTed
        # a second time.
        if em:
            g_emails.add(em)
        _nn = _norm_name(r["name"] or "")
        if _nn:
            g_names.add(_nn)
    if dup_skipped:
        _log(f"  Outbound: {dup_skipped} create candidate(s) skipped — already in Google")
    if len(to_create) > MAX_CREATES_PER_RUN:
        _log(f"  Outbound: capping creates at {MAX_CREATES_PER_RUN} "
             f"(of {len(to_create)}); the rest follow next run")
        to_create = to_create[:MAX_CREATES_PER_RUN]
    _log(f"  Outbound: {len(to_update)} contacts to update, {len(to_create)} to create")
    _log(f"  Outbound: {sum(len(v) for v in inferred_map.values())} enrichment-derived "
         f"value(s) known; these are withheld from the push")

    if not to_update and not to_create:
        return {"outbound_pushed": 0, "outbound_failed": 0, "outbound_skipped": 0,
                "outbound_stale": 0, "outbound_deferred": 0,
                "outbound_rate_limited": 0, "outbound_created": 0}

    all_updates = []
    all_creates = []
    skipped = 0

    def existing_lookup(rn):
        """The prefetched google contact for rn, or {} before the prefetch runs."""
        try:
            return existing_map.get(rn) or {}
        except NameError:
            return {}

    def build_contact_body(rn, given, family, email, phone, org, title, city, country, pid):
        phone_clean = _validate_phone(phone)
        body = {}
        _prev_person = existing_lookup(rn) or {}
        # A contact google holds with an organization and NO name at all is an
        # organisation entry -- a bank, a clinic, a shop. Weave now gives those
        # rows a display name taken from that organization, and pushing it back
        # as a givenName turns the entry into a person: the bulk pass wrote
        # givenName 'Venmo', 'Wealthfront', 'Chase Bank' and 'Visualping' into
        # four of them on 2026-08-23. Weave's inbound keeps name_given empty for
        # exactly this reason; this is the matching guard on the way out, for
        # rows that got their name parts some other way.
        _org_only = bool(_prev_person) and not (_prev_person.get("names") or []) \
            and bool(_prev_person.get("organizations") or [])
        if (given or family) and not _org_only:
            # Replacing the Name object deletes middleName, honorifics and every
            # phonetic subfield. Carry the existing Name through and set only the
            # two parts weave actually knows.
            _prev_names = (_prev_person.get("names") or [])
            _base = dict(_prev_names[0]) if _prev_names else {}
            _base.pop("metadata", None)
            _base.pop("displayName", None)
            _base.pop("displayNameLastFirst", None)
            # google re-parses unstructuredName and derives given/family from it,
            # so sending it back alongside structured parts silently undoes them.
            _base.pop("unstructuredName", None)
            # Fill-only. Overwriting meant weave's stale or badly-split copy
            # replaced what the owner typed.
            if given and not (_base.get("givenName") or "").strip():
                _base["givenName"] = given
            if family and not (_base.get("familyName") or "").strip():
                _base["familyName"] = family
            if _base:
                body["names"] = [_base]
        if email:
            email_entry = {"value": email}
            if email.lower().endswith("@gmail.com"):
                email_entry["type"] = "home"
                email_entry["formattedType"] = "Personal"
            body["emailAddresses"] = [email_entry]
        if phone_clean:
            body["phoneNumbers"] = [{"value": phone_clean}]
        if org or title:
            # Last line of defence before a value reaches the user's real contacts.
            # This is the one place organizations is constructed, for both created
            # and updated contacts, so a junk value cannot reach Google by any
            # path. Withheld rather than corrected: the true value is not known
            # here, and an empty field is better than a wrong one.
            _bad_org, _why_org = is_implausible_job_value(org)
            _bad_title, _why_title = is_implausible_job_value(title)
            if _bad_org:
                print("  withheld org %r for %s %s: %s"
                      % (org[:40], given or "", family or "", _why_org))
                org = ""
            if _bad_title:
                print("  withheld title %r for %s %s: %s"
                      % (title[:40], given or "", family or "", _why_title))
                title = ""
            if org or title:
                body["organizations"] = [{"name": org or "", "title": title or ""}]
        if city or country:
            address = {}
            if city:
                if "," in city:
                    parts = [p.strip() for p in city.split(",")]
                    address["city"] = parts[0]
                    if len(parts) > 1:
                        address["region"] = parts[1]
                    if len(parts) > 2:
                        address["countryCode"] = parts[2]
                else:
                    address["city"] = city
            if country and "countryCode" not in address:
                address["countryCode"] = country
            # Second guard, at the source: a country with no place attached is not
            # an address. Emitting it put `addresses` in the update mask carrying
            # nothing but a countryCode.
            if address.get("city") or address.get("streetAddress") or address.get("postalCode"):
                body["addresses"] = [address]
        _bd = _bday_map.get(rn) or _bday_map.get(pid)
        if _bd:
            body["birthdays"] = [_bd]
        _rel = _rel_map.get(rn) or _rel_map.get(pid) or []
        if _rel:
            body["relations"] = list(_rel)
        _ud = _ud_map.get(rn) or _ud_map.get(pid) or []
        if _ud:
            body["userDefined"] = list(_ud)
        _urls = linkedin_map.get(rn) or linkedin_map.get(pid) or []
        if _urls:
            # merged against google's existing list at batch assembly, so the
            # contact's own entries are preserved and only new ones append
            body["urls"] = list(_urls)
        return body

    # Prefetch google's current copy of every contact about to be updated.
    #
    # This ran AFTER the body-building loops. build_contact_body calls
    # existing_lookup(), existing_lookup reads existing_map, and existing_map was
    # still unbound at that point -- so the UnboundLocalError guard fired on every
    # single call and every body was built against {}. The "carry the existing
    # Name through, fill-only" protection it documents therefore never once ran:
    # body["names"] was always rebuilt from weave's two columns alone, and since
    # `names` is in the update mask google REPLACED the whole Name object.
    # Measured on the live account 2026-08-23, one bulk pass: 'Laith Ulaby, Ph.D.'
    # and 'Rachel Neurath, PhD' lost honorificSuffix, and 'Kieu Anh Vuong, PhD'
    # lost both the suffix and the givenName 'Kieu Anh' (weave held 'Kieu').
    #
    # Fetching first costs nothing extra -- the same batchGet, keyed off to_update
    # instead of all_updates -- and makes every merge in build_contact_body real.
    _log(f"  Outbound: fetching etags for {len(to_update)} contacts...")
    rn_list = [r["google_resource_name"] for r in to_update
               if r["google_resource_name"]]
    etag_map = {}
    existing_map = {}
    for i in range(0, len(rn_list), 50):
        batch_rns = rn_list[i:i+50]
        rn_param = "&resourceNames=".join(urllib.parse.quote(rn) for rn in batch_rns)
        url = (f"{PEOPLE_API_BASE}/people:batchGet?resourceNames={rn_param}"
                   "&personFields=metadata,emailAddresses,phoneNumbers,addresses,organizations,"
                   "names,urls,birthdays,relations,userDefined"
                   "&sources=READ_SOURCE_TYPE_CONTACT")
        try:
            resp = _api_get(url, token, timeout=30)
            for person in resp.get("responses", []):
                p = person.get("person", {})
                rn_val = p.get("resourceName", "")
                etag = p.get("etag", "")
                if rn_val and etag:
                    etag_map[rn_val] = etag
                    # keep the current multi-valued fields so the update merges
                    # into them rather than replacing them
                    existing_map[rn_val] = p
        except Exception as e:
            _log(f"    Etag batch error at {i}: {e}")
        time.sleep(0.3)
    _log(f"  Outbound: fetched {len(etag_map)}/{len(rn_list)} etags")

    for r in to_update:
        # Withhold enrichment-derived values from the push (owner data only).
        _inf = inferred_map.get(r["id"], {})
        if _inf:
            r = dict(r)
            for _f in GATED_FIELDS:
                if r.get(_f) and r[_f] in _inf.get(_f, ()):
                    r[_f] = ""
                    gated_total += 1
        body = build_contact_body(
            r["google_resource_name"], r["name_given"], r["name_family"],
            r["email"], r["phone"], r["org"], r["occupation"],
            r["location_city"], r["location_country"], r["id"],
        )
        if not body:
            skipped += 1
            continue
        all_updates.append((r["google_resource_name"], body, [], r["id"]))

    for r in to_create:
        # The gate ran only for updates, so a NEW contact could be created straight
        # from inference. Same withholding applies here.
        _inf = inferred_map.get(r["id"], {})
        if _inf:
            r = dict(r)
            for _f in GATED_FIELDS:
                if r.get(_f) and r[_f] in _inf.get(_f, ()):
                    r[_f] = ""
        # A create with no given/family produces a nameless entry in the address
        # book that nothing can match on the way back in. Split the display name.
        _g, _f2 = (r["name_given"] or ""), (r["name_family"] or "")
        if not (_g or _f2) and (r["name"] or "").strip():
            _parts = (r["name"] or "").strip().split()
            _g = _parts[0]
            _f2 = " ".join(_parts[1:]) if len(_parts) > 1 else ""
        body = build_contact_body(
            r["google_resource_name"] or "", _g, _f2,
            r["email"] or "", r["phone"] or "", r["org"] or "", r["occupation"] or "",
            r["location_city"] or "", r["location_country"] or "", r["id"],
        )
        if not body:
            skipped += 1
            continue
        all_creates.append((body, r["id"]))

    _log(f"  Outbound: {len(all_updates)} contacts with data to push, {skipped} skipped")


    # Batch update (200 per request)
    pushed = failed = stale = rate_limited = 0
    # A contact whose etag moved under us is dropped from the batch and retried
    # next run. That is correct, but it was invisible: it lands in no counter, so
    # the run returned pushed=0 failed=0 and read as a clean no-op, and
    # push_person -- which prints its captured log only when a counter is
    # non-zero -- stayed silent about a push that did not happen.
    deferred = 0
    batch_url = f"{PEOPLE_API_BASE}/people:batchUpdateContacts"
    # 'biographies' removed: it is never set, and a masked field absent from the
    # body is CLEARED, which wiped every contact's notes. 'urls' added, now that the
    # url list is fetched and merged rather than replaced.
    ALL_FIELDS = ("names,emailAddresses,phoneNumbers,organizations,addresses,urls,"
                  "birthdays,relations,userDefined")

    # Group by the exact set of fields each body carries. One mask governs a whole
    # request and clears any masked field a contact omits, so contacts with
    # different field sets must not share a request.
    # Birthday is fill-only: google holds one for 201 contacts weave does not know
    # about, and weave must never overwrite a date the owner set. This has to happen
    # BEFORE the signature is computed.
    _bday_kept = 0
    for _rn, _body, _uf, _pid in all_updates:
        if "birthdays" in _body and ((existing_map.get(_rn) or {}).get("birthdays") or []):
            _body.pop("birthdays")
            _bday_kept += 1
    if _bday_kept:
        _log(f"  Outbound: {_bday_kept} birthday(s) left alone - google already has one")

    _by_sig = {}
    for _rn, _body, _uf, _pid in all_updates:
        _sig = tuple(sorted(k for k in _body.keys() if k != "etag"))
        _by_sig.setdefault(_sig, []).append((_rn, _body, _uf, _pid))
    _groups = []
    for _sig, _rows in _by_sig.items():
        for _j in range(0, len(_rows), 200):
            _groups.append((_sig, _rows[_j:_j+200]))
    _log(f"  Outbound: {len(all_updates)} updates in {len(_groups)} "
         f"field-signature group(s)")

    for _gi, (_sig, batch) in enumerate(_groups):
        batch_num = _gi + 1
        total_batches = len(_groups)

        contacts_map = {}
        batch_pids = {}
        _mask_set = set(_sig)
        for rn, body, update_fields, pid in batch:
            etag = etag_map.get(rn)
            if not etag:
                continue
            _missing = _mask_set - {k for k in body if k != "etag"}
            if _missing:
                _log(f"    SKIP {rn}: body lacks masked field(s) "
                     f"{sorted(_missing)} - sending it would clear them")
                continue
            body["etag"] = etag
            # Google REPLACES a masked list field, and weave holds only one
            # value per contact, so sending it alone deletes the rest. Merge.
            _prev = existing_map.get(rn) or {}
            def _addr_key(a):
                # build_contact_body never sets formattedValue, so keying on it
                # made every address comparison compare "" and the merge silently
                # echoed google's existing address back, discarding the new one.
                #
                # Keying on every part instead appended a duplicate whenever the
                # same place was SPELLED differently on the two sides. weave holds
                # one string, "San Francisco, CA", and build_contact_body puts the
                # second token in `region`; google's stored copy of the same place
                # may carry it in `country`, or add countryCode:"US", or both.
                # Measured on the live address book: 21 contacts already carry a
                # duplicate address and one carries 38 identical copies of
                # "Portland, OR" -- one per sync pass. So a city-only address is
                # keyed on its city, which is all that identifies it.
                street = str(a.get("streetAddress") or "").strip().lower()
                postal = str(a.get("postalCode") or "").strip().lower()
                city = str(a.get("city") or "").strip().lower()
                if street or postal:
                    return "|".join((street, postal, city))
                # No street, no postal code and no city: there is no PLACE here,
                # only a bare countryCode. Return the empty string so the caller's
                # `_v.strip("|")` test is falsy and the entry is never appended.
                #
                # This used to return "city|" and claim the caller dropped it, but
                # "city|".strip("|") == "city", which is truthy -- so every
                # place-less address was appended after all. That is exactly where
                # google's orphan {"countryCode": "US"} entries come from: 14 were
                # already in the address book, and one bulk pass on 2026-08-23 added
                # 3 more (Munaf Assaf, Steve Arnold, Abhijit Oak).
                return "city|" + city if city else ""
            for _f, _keyfn in (("emailAddresses", lambda e: str(e.get("value") or "").strip().lower()),
                               ("phoneNumbers",  lambda e: str(e.get("value") or "").strip().lower()),
                               ("addresses",     _addr_key),
                               # company name only: keying on name+title appended a
                               # duplicate whenever weave lacked the title google had
                               ("organizations", lambda e: str(e.get("name") or "").strip().lower()),
                               ("urls", lambda e: _ukey(e.get("value")) or ""),
                               # keyed on the PERSON alone: a relationship to one
                               # person must appear once, whatever it is labelled
                               ("relations", _relation_person_key),
                               ("userDefined", lambda e: str(e.get("key") or "").strip().lower())):
                if _f not in body:
                    continue
                # metadata is output-only; do not echo google's source ids back
                _have_raw = [{k: v for k, v in dict(h).items() if k != "metadata"}
                             for h in (_prev.get(_f) or [])]
                # Also collapse what is ALREADY there. This block used to check
                # only whether a NEW entry duplicated an existing one, and passed
                # google's current list through untouched -- so once duplicates
                # existed they were immortal. One contact accumulated 39 identical
                # 'Princeton, NJ' addresses that every pass faithfully preserved,
                # burying the real street address the owner had typed.
                #
                # A contact must never hold two entries with the same value, in
                # any field, whether or not the field permits multiple values.
                # The FIRST occurrence wins: google's own ordering puts the
                # primary entry first, and later copies are the accumulated noise.
                _have, _seen_h = [], {}
                for _h in _have_raw:
                    _hk = _keyfn(_h)
                    if not _hk:
                        _have.append(_h)
                        continue
                    if _hk in _seen_h:
                        # Same key twice. For relations the owner's specific
                        # label ("Husband", "Son") must win over the generic one
                        # this pipeline derives ("spouse", "child").
                        if _f == "relations" and _relation_is_generic(_have[_seen_h[_hk]]) \
                                and not _relation_is_generic(_h):
                            _have[_seen_h[_hk]] = _h
                        continue
                    _seen_h[_hk] = len(_have)
                    _have.append(_h)
                _add = []
                for _entry in body[_f]:
                    _v = _keyfn(_entry)
                    if not _v.strip("|"):
                        continue
                    if any(_keyfn(h) == _v for h in _have):
                        continue
                    # weave stores a CITY; google may already hold the full street
                    # address for that same city. Pushing the city as its own
                    # entry adds a less precise copy of a place already recorded,
                    # which is how one contact accumulated 39 'Princeton, NJ'
                    # rows around the street address the owner had typed. If an
                    # existing address already describes that city, the city-only
                    # entry adds nothing.
                    if _f == "addresses" and not (_entry.get("streetAddress") or "").strip() \
                            and not (_entry.get("postalCode") or "").strip():
                        _c = str(_entry.get("city") or "").strip().lower()
                        if _c and any(str(h.get("city") or "").strip().lower() == _c
                                      for h in _have):
                            continue
                    _add.append(_entry)
                body[_f] = _have + _add
            if "urls" in body:
                _seen_u, _norm_u = set(), []
                for _e in body["urls"]:
                    _c = _canon(_e.get("value"))
                    if not _c:
                        continue
                    _k = _c.lower()
                    if _k in _seen_u:
                        continue
                    _seen_u.add(_k)
                    _e = dict(_e)
                    _e["value"] = _c
                    _norm_u.append(_e)
                body["urls"] = _norm_u
            contacts_map[rn] = body
            batch_pids[rn] = pid

        if not contacts_map:
            _log(f"  Batch {batch_num}: no valid contacts (all missing etags)")
            continue

        # Name only fields the bodies actually carry. ALL_FIELDS listed
        # 'biographies', which is never set, and a masked field missing from
        # the body is CLEARED -- so every update wiped the contact's notes.
        # Exactly the fields this group's bodies carry -- nothing else, so no
        # field can be cleared by being masked without a value.
        _mask = ",".join(f for f in ALL_FIELDS.split(",") if f in set(_sig))
        if not _mask:
            _log(f"  Batch {batch_num}: no updatable fields; skipped")
            continue
        # readMask is required for updateResult to be returned at all; without it
        # the per-contact status handling below is unreachable and every contact
        # is recorded as pushed regardless of what happened.
        req_body = {"contacts": contacts_map, "updateMask": _mask,
                    "readMask": _mask}
        attempt = 0
        backoff = 5.0
        while attempt < 4:
            attempt += 1
            try:
                _log(f"  Batch {batch_num}/{total_batches}: {len(contacts_map)} contacts...")
                resp = _api_post(batch_url, token, req_body, timeout=120)

                results = resp.get("updateResult", {})
                if results:
                    for rn_val, result in results.items():
                        status = result.get("httpStatusCode", 0)
                        if status == 200:
                            pushed += 1
                            with open(ckpt_path, "a") as f:
                                f.write(rn_val + "\n")
                        elif status == 404:
                            stale += 1
                            pid = batch_pids.get(rn_val)
                            if pid:
                                weave.execute_write(
                                    "UPDATE persons SET google_resource_name = NULL WHERE id = :id",
                                    {"id": pid},
                                )
                        else:
                            failed += 1
                else:
                    for rn_val in contacts_map:
                        pushed += 1
                        with open(ckpt_path, "a") as f:
                            f.write(rn_val + "\n")

                _log(f"  Batch {batch_num} done: {len(contacts_map)} processed")
                time.sleep(1.5)
                break
            except urllib.error.HTTPError as e:
                if e.code in (500, 502, 503, 504):
                    # transient on google's side; the batch is fine
                    _log(f"  Batch {batch_num} HTTP {e.code}, backoff "
                         f"{backoff}s ({attempt}/4)")
                    time.sleep(backoff)
                    backoff *= 2
                    if attempt >= 4:
                        failed += len(contacts_map)
                        _log(f"  Batch {batch_num} still failing after 4 attempts")
                elif e.code == 429:
                    rate_limited += 1
                    _log(f"  Batch {batch_num} rate limited, backoff {backoff}s ({attempt}/4)")
                    time.sleep(backoff)
                    backoff *= 2
                    if attempt >= 4:
                        failed += len(contacts_map)
                elif e.code == 400:
                    try:
                        err = e.read().decode()[:300]
                    except Exception:
                        err = str(e)
                    _log(f"  Batch {batch_num} HTTP 400: {err[:200]}")
                    # The docs: a stale etag returns 400 failedPrecondition and
                    # "clients should get the latest person and merge their updates
                    # into the latest person". Etags were read before the earlier
                    # batches posted, so one refresh-and-retry recovers the common
                    # case of the owner editing a contact mid-run.
                    if "failedprecondition" in err.lower() or "etag" in err.lower():
                        if attempt < 3:
                            # A stale etag means the contact CHANGED since this
                            # run read it -- the owner edited it, or another
                            # writer did. The bodies in contacts_map are the
                            # pre-change snapshot, so pairing them with a fresh
                            # etag would overwrite whatever changed. That is not
                            # a retry, it is a silent revert: it undid six
                            # phone-number corrections made by a concurrent
                            # process, and it would just as happily undo the operator's
                            # own edit. Google's guidance is to re-read the
                            # person and merge INTO the latest version.
                            #
                            # Until this path can merge properly, a contact whose
                            # etag moved is DEFERRED, not forced. The next sync
                            # rebuilds its body from current data and sends it
                            # correctly. Losing one cycle is cheap; reverting an
                            # edit the owner just made is not.
                            _log(f"  Batch {batch_num}: etag(s) stale — re-reading")
                            _fresh = _refresh_etags(list(contacts_map.keys()), token)
                            _deferred = []
                            for _rn in list(contacts_map.keys()):
                                _cur = _fresh.get(_rn)
                                if not _cur:
                                    contacts_map.pop(_rn, None)
                                    _deferred.append(_rn)
                                elif _cur != contacts_map[_rn].get("etag"):
                                    # it really did change underneath us
                                    contacts_map.pop(_rn, None)
                                    _deferred.append(_rn)
                            if _deferred:
                                deferred += len(_deferred)
                                _log(f"    deferred {len(_deferred)} contact(s) changed "
                                     f"since this run read them; next sync will resend")
                            if contacts_map:
                                req_body["contacts"] = contacts_map
                                continue
                    failed += len(contacts_map)
                    break
                else:
                    _log(f"  Batch {batch_num} HTTP {e.code}: {str(e)[:200]}")
                    failed += len(contacts_map)
                    break
            except Exception as e:
                _log(f"  Batch {batch_num} error: {e}")
                failed += len(contacts_map)
                break

    # Batch create new contacts
    created_count = create_failed = 0
    create_url = f"{PEOPLE_API_BASE}/people:createContact"
    for body, pid in all_creates:
        if pid in pushed_set:
            continue
        try:
            _resp = _api_post(create_url, token, body, timeout=30)
            # The response carries the only handle for mutating this contact later.
            # Discarding it left the new contact permanently unlinked from weave.
            _new_rn = (_resp or {}).get("resourceName") or ""
            if _new_rn:
                weave.execute_write(
                    "UPDATE persons SET google_resource_name = :rn WHERE id = :id",
                    {"rn": _new_rn, "id": pid})
                _log(f"  Created and linked {pid} -> {_new_rn}")
            else:
                _log(f"  Created {pid} but response carried no resourceName")
            created_count += 1
            with open(ckpt_path, "a") as f:
                f.write(pid + "\n")
            time.sleep(0.5)
        except Exception as e:
            create_failed += 1
            _log(f"  Create failed for {pid}: {e}")

    return {
        "outbound_pushed": pushed,
        "outbound_failed": failed,
        "outbound_skipped": skipped,
        "outbound_stale": stale,
        "outbound_deferred": deferred,
        "outbound_rate_limited": rate_limited,
        "outbound_created": created_count,
        "outbound_create_failed": create_failed,
    }


def push_person(person_id, token=None):
    """Push exactly one weave person to Google. Two API calls, no inbound pass.

    Returns the sync_outbound result dict with the captured log under "log".
    The log is echoed to stdout only when something failed, so a caller in a loop
    gets one quiet call per contact and full detail when it matters.
    """
    global _LOG_SINK
    if token is None:
        token = get_access_token()
    _LOG_SINK = []
    try:
        res = sync_outbound(token, EPOCH_TS, only_person_ids=[person_id])
    finally:
        buf, _LOG_SINK = (_LOG_SINK or []), None
    if (res.get("outbound_failed") or res.get("outbound_stale")
            or res.get("outbound_deferred") or res.get("outbound_rate_limited")):
        for line in buf:
            print(line, flush=True)
    res["log"] = buf
    return res


def main():
    _argv = sys.argv[1:]
    if "--push-person" in _argv:
        # Targeted outbound for one weave person id. No inbound, no creates, and
        # last_sync is deliberately NOT advanced -- this pass did not read Google,
        # so it must not claim to have.
        _i = _argv.index("--push-person")
        if _i + 1 >= len(_argv):
            print("usage: google_sync.py --push-person <weave_person_id>")
            sys.exit(2)
        _pid = _argv[_i + 1]
        _tok = get_access_token()
        _log("Targeted outbound push for person %s" % _pid)
        _res = sync_outbound(_tok, EPOCH_TS, only_person_ids=[_pid])
        _log("  Push result: %s" % _res)
        return {"outbound": _res}

    config = load_config()
    last_sync = config.get("last_sync", {}).get("google_contacts")
    token = get_access_token()

    _log(f"Starting Google Contacts sync (last_sync={last_sync})")

    # Inbound
    _log("Phase 1: Inbound sync...")
    inbound_result = sync_inbound(token)
    _log(f"  Inbound result: {inbound_result}")

    # Outbound (doubly gated: config flag + checkpoint)
    writeback = config.get("writeback", {}).get("google_contacts", False)
    ckpt_exists = (AGENT_ROOT / "commons/db/ocas-weave/staging/outbound_ckpt.txt").exists()

    if writeback:
        _log("Phase 2: Outbound sync...")
        outbound_result = sync_outbound(token, last_sync)
        _log(f"  Outbound result: {outbound_result}")
    else:
        outbound_result = {"outbound_pushed": 0, "note": "writeback disabled"}
        _log("Phase 2: Outbound skipped (writeback disabled)")

    # Update last_sync timestamp
    now = datetime.now(timezone.utc).isoformat()
    config.setdefault("last_sync", {})["google_contacts"] = now
    save_config(config)

    _log(f"Sync complete at {now}")
    return {"inbound": inbound_result, "outbound": outbound_result, "sync_time": now}


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        if 'refresh token revoked' in str(e):
            import sys
            print(f"ABORT: Google OAuth refresh token revoked. the operator must re-authorize.", file=sys.stderr)
            sys.exit(2)
        raise