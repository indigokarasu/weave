#!/usr/bin/env python3
"""Full Weave → Google Contacts outbound sync. ALL mapped fields per google-field-map.md.

Queries Person nodes, Fact nodes (birthday, linkedin, website, instagram),
and Knows spouse relationships. Builds complete PATCH bodies covering all 13
mapped fields. Never syncs partial data.
"""

import json, urllib.request, urllib.parse, time, sys, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
DB = str(AGENT_ROOT / "commons/db/ocas-weave/weave.lbug")
TOKEN_PATH = str(AGENT_ROOT / "google_token.json")
LOG_DIR = str(AGENT_ROOT / "data/weave-google-sync")
os.makedirs(LOG_DIR, exist_ok=True)

def get_token():
    with open(TOKEN_PATH) as f:
        td = json.load(f)
    expiry = td.get("expiry", "")
    if isinstance(expiry, str):
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                refresh_token(td)
        except:
            pass
    return td["token"]

def refresh_token(td):
    resp = urllib.request.urlopen(urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "client_id": td["client_id"],
            "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"],
            "grant_type": "refresh_token"
        }).encode()))
    new = json.loads(resp.read())
    td["token"] = new["access_token"]
    td["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=new["expires_in"])).isoformat()
    with open(TOKEN_PATH, "w") as f:
        json.dump(td, f, indent=2)
    print(f"  Token refreshed, expires: {td['expiry']}")

def api_request(method, path, data=None, token=None):
    url = f"https://people.googleapis.com/v1/{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    return urllib.request.urlopen(req)

def safe_get_next(r):
    """Iterate rows handling LadybugDB quirks (no StopIteration, corrupt UTF-8)."""
    while True:
        try:
            yield r.get_next()
        except Exception as e:
            if "No more tuples" in str(e):
                return
            if "utf-8" in str(e):
                continue
            raise

def get_people():
    from real_ladybug import Database, Connection
    db = Database(DB, read_only=True)
    conn = Connection(db)

    # All Person fields mapped to Google
    r = conn.execute("""
        MATCH (p:Person)
        WHERE p.google_resource_name IS NOT NULL AND p.google_resource_name <> ''
        RETURN p.id, p.name, p.name_given, p.name_family, p.email, p.phone,
               p.org, p.occupation, p.location_city, p.location_country,
               p.google_resource_name
    """)
    cols = list(r.get_column_names())
    persons = []
    for row in safe_get_next(r):
        person = {}
        for ci, cn in enumerate(cols):
            field = cn.split(".", 1)[1] if "." in cn else cn
            val = row[ci]
            if val and str(val).strip():
                person[field] = str(val).strip()
        if person.get("google_resource_name"):
            persons.append(person)

    # Facts: birthday, linkedin, website, instagram
    r2 = conn.execute("""
        MATCH (p:Person)-[:HasFact]->(f:Fact)
        WHERE p.google_resource_name IS NOT NULL AND p.google_resource_name <> ''
        RETURN p.google_resource_name AS rn, f.type AS ftype, f.value AS fvalue
    """)
    cols2 = list(r2.get_column_names())
    rn_idx = cols2.index("p.google_resource_name")
    type_idx = cols2.index("f.type")
    val_idx = cols2.index("f.value")

    facts_by_rn = {}
    for row in safe_get_next(r2):
        rn = str(row[rn_idx]).strip()
        ft = str(row[type_idx]).strip()
        fv = str(row[val_idx]).strip()
        if rn not in facts_by_rn:
            facts_by_rn[rn] = {}
        facts_by_rn[rn][ft] = fv

    # Spouse relationships
    r3 = conn.execute("""
        MATCH (p:Person)-[:Knows {rel_type: 'spouse'}]->(s:Person)
        WHERE p.google_resource_name IS NOT NULL AND p.google_resource_name <> ''
        RETURN p.google_resource_name AS rn, s.name AS spouse_name
    """)
    cols3 = list(r3.get_column_names())
    rn3_idx = cols3.index("p.google_resource_name")
    sn_idx = cols3.index("s.name")

    spouses_by_rn = {}
    for row in safe_get_next(r3):
        rn = str(row[rn3_idx]).strip()
        sn = str(row[sn_idx]).strip()
        spouses_by_rn[rn] = sn

    conn.close()
    db.close()
    return persons, facts_by_rn, spouses_by_rn

def build_update_body(person, facts, spouse):
    """Build PATCH body with ALL mapped fields from references/google-field-map.md."""
    body = {}
    update_fields = []

    # names: displayName, givenName, familyName
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

    # emailAddresses
    if person.get("email"):
        body["emailAddresses"] = [{"value": person["email"], "type": "home"}]
        update_fields.append("emailAddresses")

    # phoneNumbers
    if person.get("phone"):
        body["phoneNumbers"] = [{"value": person["phone"], "type": "mobile"}]
        update_fields.append("phoneNumbers")

    # organizations: name + title
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

    # addresses: city + countryCode
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

    # birthdays (from Fact)
    if facts.get("birthday"):
        bval = facts["birthday"]
        try:
            parts = bval.replace("/", "-").split("-")
            if len(parts) >= 2:
                m, d = int(parts[0]), int(parts[1])
                body["birthdays"] = [{"date": {"month": m, "day": d}}]
                update_fields.append("birthdays")
        except:
            pass

    # urls: LinkedIn, Website, Instagram (from Facts)
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

    # relations: spouse (plain text name, NOT resource ID)
    if spouse:
        body["relations"] = [{"person": spouse, "type": "spouse"}]
        update_fields.append("relations")

    return body, update_fields

def main():
    token = get_token()
    print(f"Fetching Weave data...")
    persons, facts_by_rn, spouses_by_rn = get_people()
    print(f"Found {len(persons)} contacts with Google resource names")

    pushed = 0
    failed = 0
    skipped = 0
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
                    errors.append(f"{p.get('name','?')}: {e2}")
            else:
                failed += 1
                errors.append(f"{p.get('name','?')}: HTTP {e.code}: {err_body[:200]}")
        except Exception as e:
            failed += 1
            errors.append(f"{p.get('name','?')}: {str(e)[:200]}")

        time.sleep(0.7)

    print(f"\n{'='*50}")
    print(f"FULL SYNC COMPLETE")
    print(f"  Pushed: {pushed}")
    print(f"  Skipped (no fields): {skipped}")
    print(f"  Failed: {failed}")
    if errors:
        print(f"\n  First 10 errors:")
        for e in errors[:10]:
            print(f"    {e}")

    # Log
    log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(persons),
        "pushed": pushed,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:20]
    }
    with open(os.path.join(LOG_DIR, f"sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"), "w") as f:
        json.dump(log, f, indent=2)

    return pushed, failed

if __name__ == "__main__":
    main()
