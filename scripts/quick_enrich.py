#!/usr/bin/env python3
"""
Quick Enrich — Real-time OSINT background research for new contacts.

Usage:
  python3 quick_enrich.py "Full Name" [--org "Company"] [--location "City"]

Follows the proper Scout → Sift → Sherlock pipeline:
  1. Scout: SearXNG identity-resolved research
  2. Sift: Full page extraction (direct HTTP + Jina fallback)
  3. Sherlock: Username/handle expansion (if handles found)
  4. Write: Persist to Weave as Fact nodes with full provenance

Designed for real-time use when you meet someone new and need rapid background.
"""

import sys, json, time, uuid, re, urllib.request, urllib.parse, argparse, subprocess
from datetime import datetime, timezone
from pathlib import Path
import os

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
DB_PATH = str(AGENT_ROOT / "commons/db/ocas-weave/weave.lbug")
SEARXNG_URL = "http://localhost:8888/search"
JINA_BASE = "https://r.jina.ai"

AUTH_WALLED = {"linkedin.com","twitter.com","x.com","facebook.com","instagram.com","tiktok.com"}

def log(msg): print(f"  {msg}", flush=True)

def searxng_search(query, limit=5):
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": limit})
    url = f"{SEARXNG_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "HermesAgent/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return [{"url":i.get("url",""),"title":i.get("title",""),"content":i.get("content","")} for i in data.get("results",[])[:limit]]

def fetch_page(url):
    """Fetch full page content. Returns (text, method) or (None, None)."""
    if any(d in url for d in AUTH_WALLED):
        return None, None
    # Try direct HTTP
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        text = re.sub(r'<script[^>]*>.*?</script>',' ',html,flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>',' ',text,flags=re.DOTALL)
        text = re.sub(r'<[^>]+>',' ',text)
        text = re.sub(r'\s+',' ',text).strip()
        if len(text) >= 200:
            return text[:5000], "direct"
    except: pass
    # Jina fallback
    try:
        req = urllib.request.Request(f"{JINA_BASE}/{url}", headers={"User-Agent":"HermesAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        if len(text) >= 100:
            return text[:5000], "jina"
    except: pass
    return None, None

def extract_from_content(name, content, url):
    """Extract structured data from full page content."""
    found = {}
    name_parts = name.lower().split()

    if not any(p in content.lower() for p in name_parts if len(p) > 3):
        return found

    # Occupation
    for pattern in [
        rf'{re.escape(name)}[,\s]+([A-Z][a-zA-Z\s&,/]+?)(?:\s+at\s+|\s*@\s*)\s*([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
        rf'([A-Z][a-zA-Z\s&,/]+?(?:Chief|Senior|Junior|Lead|Principal|Staff|VP|SVP|EVP|President|Director|Manager|Engineer|Designer|Developer|Analyst|Architect|Scientist|Researcher|Founder|Co-Founder|Partner|Officer|Coordinator|Specialist|Consultant|Advisor))\s+(?:at|@|of|for)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
    ]:
        m = re.search(pattern, content)
        if m and m.lastindex >= 2:
            title, company = m.group(1).strip(), m.group(2).strip()
            if (3 < len(title) < 60 and 1 < len(company) < 80
                and title.lower() not in ["linkedin","view","profile","search","the","this","that","about","contact"]
                and "http" not in title.lower()
                and company.lower() not in ["linkedin","view","profile","search"]):
                found["occupation"] = title
                found["occupation_source"] = url
                found["org"] = company
                found["org_source"] = url
                break

    # Location
    for pattern in [
        rf'{re.escape(name)}.*?(?:in|based in|located in|from)\s+([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
        rf'(?:Location|Based|Located|Lives?|Area|City):\s*([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
    ]:
        m = re.search(pattern, content)
        if m:
            loc = m.group(1).strip()
            if 3 < len(loc) < 60:
                found["location_city"] = loc
                found["location_city_source"] = url
                break
    if not found.get("location_city"):
        for city in ["San Francisco","New York","Los Angeles","Seattle","Chicago","Austin","Boston","Denver","Miami","Portland","Washington DC","Bay Area","Silicon Valley","London","Tokyo","Singapore","Toronto","Sydney","Berlin","Paris"]:
            if city in content:
                found["location_city"] = city
                found["location_city_source"] = url
                break

    # Email
    m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', content)
    if m:
        email = m.group(0).rstrip(".")
        if not any(x in email.lower() for x in ["noreply","no-reply","support@","info@","admin@","example.com","@linkedin","@facebook","@twitter","sentry.io"]):
            found["email"] = email
            found["email_source"] = url

    # Handles/usernames for Sherlock
    handles = set()
    for pattern in [rf'@(\w{{3,20}})\b', r'github\.com/(\w+)', r'twitter\.com/(\w+)', r'x\.com/(\w+)']:
        for m in re.finditer(pattern, content):
            h = m.group(1)
            if h.lower() not in ["www","com","org","http","https","linkedin","twitter","facebook","instagram"]:
                handles.add(h)
    if handles:
        found["_handles"] = list(handles)[:5]

    return found

def validate_field(key, value, person_name=""):
    if not value or not isinstance(value, str) or len(value.strip()) < 2: return False
    value = value.strip()
    pn = (person_name or "").lower().strip()
    if key == "occupation":
        if pn and value.lower() == pn: return False
        if len(value) > 60 or len(value) < 3: return False
        if any(g in value.lower() for g in ["log in","sign in","sign up","linkedin view","profile","view more","see more"]): return False
        first = value.split()[0].lower() if value.split() else ""
        if first in ["is","are","was","were","the","a","an","its","it's","been"]: return False
        titles = ["manager","director","engineer","lead","head","vp","senior","junior","staff","principal","analyst","designer","developer","architect","founder","ceo","cto","cfo","president","assistant","coordinator","specialist","consultant","instructor","associate","officer","representative","therapist","producer","editor","writer","owner","partner","chief","svp","evp","co-founder","advisor"]
        if not any(t in value.lower() for t in titles):
            words = value.split()
            if not all(w[0].isupper() for w in words if len(w) > 2): return False
        return True
    elif key == "org":
        if value.lower() in ["the","design","visitor","guest","university","college"]: return False
        if pn and value.lower() == pn: return False
        if len(value) > 80: return False
        if value.lower() in ["engineer","manager","director","president","vp","head","lead","architect","developer","ceo","cto","cfo","principal","analyst","specialist"]: return False
        return True
    elif key == "location_city":
        return len(value) <= 80 and not (value.isupper() and len(value) > 30)
    elif key == "email":
        return "@" in value and "." in value
    return True

def run_sherlock(handles):
    """Run sherlock on discovered handles. Returns list of {platform, url, handle}."""
    results = []
    for handle in handles[:3]:  # Max 3 handles
        try:
            proc = subprocess.run(
                ["sherlock", "--print-found", "--no-color", handle, "--timeout", "30"],
                capture_output=True, text=True, timeout=60
            )
            for line in proc.stdout.split("\n"):
                if "[+]" in line and "http" in line:
                    url = line.split("[+]")[1].strip() if "[+]" in line else ""
                    platform = url.split("/")[2] if "://" in url else "unknown"
                    results.append({"platform": platform, "url": url, "handle": handle})
        except Exception as e:
            log(f"  Sherlock error for @{handle}: {e}")
    return results

def write_to_weave(contact_id, enrichment_data, person_name, confidence=0.7):
    """Write enrichment data to Weave as Fact nodes."""
    import real_ladybug as lb
    db = lb.Database(DB_PATH, read_only=False)
    conn = lb.Connection(db)
    now = datetime.now(timezone.utc).isoformat()
    written = 0

    for key, value in enrichment_data.items():
        if key.startswith("_") or key.endswith("_source"):
            continue
        if key not in ["org","occupation","location_city","location_country","email","phone"]:
            continue
        if not validate_field(key, value, person_name):
            log(f"  ✗ Rejected {key}='{value[:50]}'")
            continue

        fid = str(uuid.uuid4())
        source_url = enrichment_data.get(f"{key}_source", "")
        cypher = (
            "MATCH (p:Person {id: $pid}) "
            "CREATE (f:Fact {id: $fid, predicate: $key, value: $value, "
            "source_type: 'web_enrichment', source_ref: $ref, "
            "confidence: $conf, record_time: $rt}) "
            "CREATE (p)-[:HasFact]->(f) "
            "RETURN f.id"
        )
        r = conn.execute(cypher, {
            "pid": contact_id, "fid": fid, "key": key, "value": value,
            "ref": source_url or "quick_enrich_" + datetime.now(timezone.utc).strftime("%Y%m%d"),
            "conf": confidence, "rt": now,
        })
        if r.get_all():
            written += 1
            log(f"  ✓ {key}='{value[:60]}'")
        r.close()

    conn.close()
    return written

def quick_enrich(name, org=None, location=None):
    """Run the full Scout → Sift → Sherlock → Write pipeline."""
    print(f"\n{'='*60}")
    print(f"QUICK ENRICH: {name}")
    if org: print(f"  Org: {org}")
    if location: print(f"  Location: {location}")
    print(f"{'='*60}")

    # ── SCOUT PHASE ──
    print(f"\n[1/4] SCOUT — SearXNG identity-resolved research")
    parts = name.split()
    queries = []
    if len(parts) >= 2:
        queries.append(f'"{name}" LinkedIn')
        queries.append(f'"{parts[0]} {parts[-1]}" site:linkedin.com/in')
    if org:
        queries.append(f'"{name}" {org}')
    queries.append(f'"{name}" professional background')

    all_results = []
    for q in queries[:4]:
        try:
            results = searxng_search(q, limit=5)
            all_results.extend(results)
            time.sleep(2)
        except Exception as e:
            log(f"  Search error: {e}")

    log(f"  {len(all_results)} search results")

    if not all_results:
        log("  No results found")
        return

    # ── SIFT PHASE ──
    print(f"\n[2/4] SIFT — Full page extraction")
    merged = {}
    handles = set()
    fetched = 0

    for result in all_results:
        url = result.get("url", "")
        if not url or any(d in url for d in AUTH_WALLED):
            continue
        domain = urllib.parse.urlparse(url).netloc
        if fetched >= 3:
            break

        content, method = fetch_page(url)
        fetched += 1
        if not content:
            continue

        log(f"  Fetched {domain} via {method} ({len(content)} chars)")
        extracted = extract_from_content(name, content, url)

        for k, v in extracted.items():
            if k == "_handles":
                handles.update(v)
            elif k not in merged:
                merged[k] = v

    log(f"  Extracted: {list(k for k in merged if not k.endswith('_source'))}")
    if handles:
        log(f"  Handles found: {list(handles)[:5]}")

    # ── SHERLOCK PHASE ──
    sherlock_results = []
    if handles:
        print(f"\n[3/4] SHERLOCK — Username expansion ({len(handles)} handles)")
        sherlock_results = run_sherlock(list(handles))
        for r in sherlock_results[:5]:
            log(f"  [+] {r['platform']}: {r['url']}")
    else:
        print(f"\n[3/4] SHERLOCK — Skipped (no handles found)")

    # ── WRITE PHASE ──
    print(f"\n[4/4] WRITE — Persist to Weave")

    # Check if person exists in Weave
    import real_ladybug as lb
    db = lb.Database(DB_PATH, read_only=True)
    conn = lb.Connection(db)
    r = conn.execute("MATCH (p:Person {name: $name}) RETURN p.id, p.name", {"name": name})
    existing = r.get_all()
    r.close()
    conn.close()

    if existing:
        contact_id = existing[0][0]
        log(f"  Found existing contact: {existing[0][1]} ({contact_id})")
    else:
        # Create new person
        contact_id = str(uuid.uuid4())
        db2 = lb.Database(DB_PATH, read_only=False)
        conn2 = lb.Connection(db2)
        now = datetime.now(timezone.utc).isoformat()
        name_parts = name.split()
        conn2.execute("""
            CREATE (p:Person {
                id: $id, name: $name, name_given: $given, name_family: $family,
                source_type: 'imported', source_ref: 'quick_enrich',
                confidence: 0.7, record_time: $rt
            })
        """, {
            "id": contact_id, "name": name,
            "given": name_parts[0] if name_parts else "",
            "family": name_parts[-1] if len(name_parts) > 1 else "",
            "rt": now,
        })
        conn2.close()
        log(f"  Created new contact: {name} ({contact_id})")

    written = write_to_weave(contact_id, merged, name)
    print(f"\n  Written: {written} fields")

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS FOR: {name}")
    print(f"{'='*60}")
    for key in ["occupation","org","location_city","email","phone"]:
        val = merged.get(key, "—")
        if key + "_source" in merged:
            val += f"  (source: {merged[key+'_source'][:60]})"
        print(f"  {key}: {val}")
    if sherlock_results:
        print(f"  social: {len(sherlock_results)} profiles found")
    print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quick Enrich — Real-time OSINT for new contacts")
    parser.add_argument("name", help="Full name of the person")
    parser.add_argument("--org", help="Known organization/company", default=None)
    parser.add_argument("--location", help="Known location", default=None)
    args = parser.parse_args()
    quick_enrich(args.name, args.org, args.location)
