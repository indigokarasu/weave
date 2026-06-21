#!/usr/bin/env python3
"""Full Weave → Google Contacts outbound sync. ALL mapped fields per google-field-map.md.

Queries Person nodes, Fact nodes (birthday, linkedin, website, instagram),
and Knows spouse relationships. Builds complete PATCH bodies covering all 13
mapped fields. Never syncs partial data.
"""
import json
import urllib.request
import urllib.parse
import time
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
SQLITE_DB = AGENT_ROOT / "commons/db/ocas-weave/weave.sqlite"
LOG_DIR = str(AGENT_ROOT / "data/weave-google-sync")
os.makedirs(LOG_DIR, exist_ok=True)

# Shared Google API helpers
sys.path.insert(0, str(Path(__file__).parent))
from google_api import get_access_token, PEOPLE_API_BASE


def api_request(method, path, data=None, token=None):
    url = f"{PEOPLE_API_BASE}/{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    return urllib.request.urlopen(req)


def get_people():
    """Fetch people, facts, and spouse relationships from SQLite."""
    from weave_sqlite import WeaveDB
    weave = WeaveDB(SQLITE_DB)

    rows = weave.execute("""
        SELECT id, name, name_given, name_family, email, phone,
               org, occupation, location_city, location_country,
               google_resource_name
        FROM persons
        WHERE google_resource_name IS NOT NULL AND google_resource_name != ''
    """)
    persons = []
    for row in rows:
        person = {}
        for key in row:
            val = row[key]
            if val and str(val).strip():
                person[key] = str(val).strip()
        if person.get("google_resource_name"):
            persons.append(person)

    # Facts: birthday, linkedin, website, instagram
    fact_rows = weave.execute("""
        SELECT p.google_resource_name AS rn, f.predicate AS ftype, f.value AS fvalue
        FROM facts f
        JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact'
        JOIN persons p ON p.id = e.source_id
        WHERE p.google_resource_name IS NOT NULL AND p.google_resource_name != ''
    """)
    facts_by_rn = {}
    for row in fact_rows:
        rn = row["rn"]
        if rn not in facts_by_rn:
            facts_by_rn[rn] = {}
        facts_by_rn[rn][row["ftype"]] = row["fvalue"]

    # Spouse relationships
    spouse_rows = weave.execute("""
        SELECT p.google_resource_name AS rn, s.name AS spouse_name
        FROM edges e
        JOIN persons p ON p.id = e.source_id
        JOIN persons s ON s.id = e.target_id
        WHERE e.rel_type = 'Knows'
          AND e.context = 'spouse'
          AND p.google_resource_name IS NOT NULL AND p.google_resource_name != ''
    """)
    spouses_by_rn = {}
    for row in spouse_rows:
        spouses_by_rn[row["rn"]] = row["spouse_name"]

    return persons, facts_by_rn, spouses_by_rn


def build_update_body(person, facts, spouse):
    """Build PATCH body with ALL mapped fields from references/google-field-map.md."""
    body = {}
    update_fields = []

    has_name = False
    names = {}
    if person.get("name_given"):
        names["givenName"] = person["name_given"]
        has_name = True
    if person.get("name_family"):
        names["familyName"] = person["name_family"]
        has_name = True
    if person.get("name"):
        names["displayName"] = person["name"]
        has_name = True
    if has_name:
        body["names"] = [names]
        update_fields.append("names")

    if person.get("email"):
        body["emailAddresses"] = [{"value": person["email"], "type": "home"}]
        update_fields.append("emailAddresses")

    if person.get("phone"):
        body["phoneNumbers"] = [{"value": person["phone"], "type": "mobile"}]
        update_fields.append("phoneNumbers")

    has_org = False
    org = {}
    if person.get("org"):
        org["name"] = person["org"]
        has_org = True
    if person.get("occupation"):
        org["title"] = person["occupation"]
        has_org = True
    if has_org:
        body["organizations"] = [org]
        update_fields.append("organizations")

    has_addr = False
    addr = {}
    if person.get("location_city"):
        addr["city"] = person["location_city"]
        has_addr = True
    if person.get("location_country"):
        addr["countryCode"] = person["location_country"].upper()
        has_addr = True
    if has_addr:
        body["addresses"] = [addr]
        update_fields.append("addresses")

    if facts.get("birthday"):
        bval = facts["birthday"]
        try:
            parts = bval.replace("/", "-").split("-")
            if len(parts) >= 2:
                m, d = int(parts[0]), int(parts[1])
                body["birthdays"] = [{"date": {"month": m, "day": d}}]
                update_fields.append("birthdays")
        except Exception:
            pass

    urls = []
    if facts.get("linkedin"):
        urls.append({"value": facts["linkedin"], "type": "LinkedIn"})
    if facts.get("website"):
        urls.append({"value": facts["website"], "type": "Website"})
    if facts.get("instagram"):
        urls.append({"value": facts["instagram"], "type": "Instagram"})
    if urls:
        body["urls"] = urls
        update_fields.append("urls")

    if spouse:
        body["relations"] = [{"person": spouse, "type": "spouse"}]
        update_fields.append("relations")

    return body, update_fields


def main():
    token = get_access_token()
    print("Fetching Weave data...")
    persons, facts_by_rn, spouses_by_rn = get_people()
    print(f"Found {len(persons)} contacts with Google resource names")

    pushed = failed = skipped = 0
    errors = []

    for i, p in enumerate(persons):
        rn = p["google_resource_name"]
        facts = facts_by_rn.get(rn, {})
        spouse = spouses_by_rn.get(rn)

        body, update_fields = build_update_body(p, facts, spouse)

        if not update_fields:
            skipped += 1
            continue

        try:
            resp = api_request("PATCH",
                f"{rn}?personFields={','.join(update_fields)}&updatePersonFields={','.join(update_fields)}",
                data=body, token=token)
            resp.read()
            pushed += 1
            if pushed % 25 == 0:
                print(f"  Pushed {pushed}/{len(persons)}...")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            if e.code == 429:
                print(f"  Rate limited at contact {i}, sleeping 10s...")
                time.sleep(10)
                try:
                    resp = api_request("PATCH",
                        f"{rn}?personFields={','.join(update_fields)}&updatePersonFields={','.join(update_fields)}",
                        data=body, token=token)
                    resp.read()
                    pushed += 1
                except Exception as e2:
                    failed += 1
                    errors.append(f"{p.get('name', '?')}: {e2}")
            else:
                failed += 1
                errors.append(f"{p.get('name', '?')}: HTTP {e.code}: {err_body[:200]}")
        except Exception as e:
            failed += 1
            errors.append(f"{p.get('name', '?')}: {str(e)[:200]}")

        time.sleep(0.7)

    print(f"\n{'='*50}")
    print("FULL SYNC COMPLETE")
    print(f"  Pushed: {pushed}")
    print(f"  Skipped (no fields): {skipped}")
    print(f"  Failed: {failed}")
    if errors:
        print(f"\n  First 10 errors:")
        for e in errors[:10]:
            print(f"    {e}")

    log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(persons), "pushed": pushed,
        "skipped": skipped, "failed": failed, "errors": errors[:20],
    }
    with open(os.path.join(LOG_DIR, f"sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"), "w") as f:
        json.dump(log, f, indent=2)

    return pushed, failed


if __name__ == "__main__":
    main()
