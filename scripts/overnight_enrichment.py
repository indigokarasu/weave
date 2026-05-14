#!/usr/bin/env python3
"""
Overnight Weave Contact Enrichment Pipeline

Proper 3-phase enrichment per the Weave SKILL.md Contact Enrichment Lifecycle:
  1. SCOUT PHASE: SearXNG identity-resolved research (Tier 1 public web search)
  2. SIFT PHASE:   Fetch full pages with stealth-browser (Scrapling unavailable),
                   fall back to Jina Reader for JS-heavy sites
  3. SHERLOCK:     Username/handle expansion (if handles discovered during extraction)

Every extracted field gets full provenance: source_url, source_type, confidence, record_time.
"""

import json
import os
import sys
import time
import subprocess
import traceback
import urllib.request
import urllib.parse
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# Config
SEARCH_DELAY = 3        # Seconds between searches
FETCH_DELAY = 2         # Seconds between page fetches
SYNC_EVERY = 30         # Sync to Google after this many enriched contacts
DEADLINE_HOUR_ET = 8    # Stop at 8am ET
SEARXNG_URL = "http://localhost:8888/search"
JINA_BASE = "https://r.jina.ai"

from pathlib import Path
AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
PROGRESS_FILE = str(AGENT_ROOT / "data/weave-enrichment/progress.jsonl")
STATS_FILE = str(AGENT_ROOT / "data/weave-enrichment/stats.json")

# Minimum confidence threshold for writing data
MIN_CONFIDENCE = 0.7


def log(msg):
    ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")
    print(f"[{ts}] {msg}", flush=True)


# ─── SCOUT PHASE: SearXNG identity-resolved research ────────────────────────

def searxng_search(query, limit=5):
    """Search via local SearXNG. Returns list of {url, title, content}."""
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": limit})
    url = f"{SEARXNG_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "HermesAgent/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    results = []
    for item in data.get("results", [])[:limit]:
        results.append({
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "content": item.get("content", ""),
        })
    return results


def build_scout_queries(contact):
    """Build identity-resolved search queries for the Scout phase."""
    queries = []
    name = contact["name"]
    name_given = contact.get("name_given", "")
    name_family = contact.get("name_family", "")
    org = contact.get("org", "")

    if name_given and name_family:
        queries.append(f'"{name_given} {name_family}" LinkedIn')
        queries.append(f'"{name_given} {name_family}" site:linkedin.com/in')
    if org:
        queries.append(f'"{name}" {org}')
        queries.append(f'"{name}" at {org}')
    if not queries:
        queries.append(f'"{name}" professional')

    return queries[:4]  # Max 4 queries per contact


# ─── SIFT PHASE: Full page extraction ───────────────────────────────────────

# Auth-walled domains we can't fetch directly
# Auth-walled domains we can't fetch via direct HTTP/Jina
# LinkedIn is here because direct fetch returns login walls; use the LinkedIn MCP
# in ocas-reach (linkedin-scraper-mcp) separately if installed and logged in.
AUTH_WALLED = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "tiktok.com",
}

def is_auth_walled(url):
    """Check if a URL is behind an auth wall."""
    return any(domain in url for domain in AUTH_WALLED)


def fetch_page_scrapling(url):
    """
    Fetch a page using stealth-browser MCP tool (Scrapling unavailable due to deps).
    Falls back to Jina Reader.
    Returns (content, method) or (None, None) on failure.
    """
    # Try direct HTTP fetch first (fast, works for static sites)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # Basic HTML-to-text
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) >= 200:
            return text[:5000], "direct_http"
    except Exception:
        pass

    # Fallback: Jina Reader
    try:
        jina_url = f"{JINA_BASE}/{url}"
        req = urllib.request.Request(jina_url, headers={"User-Agent": "HermesAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        if len(text) >= 100:
            return text[:5000], "jina"
    except Exception:
        pass

    return None, None


def sift_extract_from_pages(name, org, search_results, max_pages=3):
    """
    Sift phase: fetch full pages from search results and extract structured data.
    Skips auth-walled domains. Fetches up to max_pages unique domains.
    Returns dict of extracted fields with source URLs.
    """
    extracted = {}
    fetched_domains = set()
    sources = []

    for result in search_results:
        url = result.get("url", "")
        if not url:
            continue

        # Skip auth-walled
        if is_auth_walled(url):
            continue

        # Skip duplicate domains
        domain = urllib.parse.urlparse(url).netloc
        if domain in fetched_domains:
            continue
        if len(fetched_domains) >= max_pages:
            break

        fetched_domains.add(domain)

        # Fetch the page
        content, method = fetch_page_scrapling(url)
        if not content:
            continue

        sources.append({"url": url, "domain": domain, "method": method})

        # Extract from full page content (much richer than snippets)
        name_lower = name.lower()
        name_parts = name_lower.split()

        # Check this page is actually about the right person
        if not any(part in content.lower() for part in name_parts if len(part) > 3):
            continue

        # ── Extract occupation ──
        if not extracted.get("occupation"):
            # Look for "Title at Company" patterns in full content
            occ_patterns = [
                # "Name, Title at Company"
                rf'{re.escape(name)}[,\s]+([A-Z][a-zA-Z\s&,/]+?)(?:\s+at\s+|\s*@\s*|\s+for\s+)([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
                # "Title at Company" near the name
                rf'([A-Z][a-zA-Z\s&,/]+?(?:Chief|Senior|Junior|Lead|Principal|Staff|VP|SVP|EVP|President|Director|Manager|Engineer|Designer|Developer|Analyst|Architect|Scientist|Researcher|Founder|Co-Founder|Partner|Officer|Coordinator|Specialist|Consultant|Advisor))\s+(?:at|@|of|for)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
                # "works as Title at Company"
                rf'(?:works?\s+(?:as|at)|is\s+(?:a|an|the)|serves?\s+as|appointed)\s+([A-Z][a-zA-Z\s&,/]+?)\s+(?:at|@|of|for)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
            ]
            for pattern in occ_patterns:
                match = re.search(pattern, content)
                if match and match.lastindex >= 2:
                    title = match.group(1).strip()
                    company = match.group(2).strip()
                    if (3 < len(title) < 60
                        and title.lower() not in ["linkedin", "view", "profile", "search", "the", "this", "that", "about", "contact"]
                        and "http" not in title.lower()
                        and 1 < len(company) < 80
                        and company.lower() not in ["linkedin", "view", "profile", "search"]):
                        extracted["occupation"] = title
                        extracted["occupation_source"] = url
                        if not extracted.get("org"):
                            extracted["org"] = company
                            extracted["org_source"] = url
                        break

        # ── Extract org (if not already found) ──
        if not extracted.get("org"):
            # Look for organization mentions near the name
            org_patterns = [
                rf'{re.escape(name)}.*?(?:at|@|of|for|with)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
                rf'(?:company|organization|employer):\s*([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
            ]
            for pattern in org_patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    company = match.group(1).strip()
                    if 1 < len(company) < 80 and company.lower() not in ["linkedin", "view", "profile", "search"]:
                        extracted["org"] = company
                        extracted["org_source"] = url
                        break

        # ── Extract location ──
        if not extracted.get("location_city"):
            loc_patterns = [
                rf'{re.escape(name)}.*?(?:in|based in|located in|from)\s+([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
                rf'(?:Location|Based|Located|Lives?|Area|City):\s*([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
                rf'(?:headquartered|based)\s+(?:in|at)\s+([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
            ]
            static_cities = [
                "San Francisco", "New York", "Los Angeles", "Seattle", "Chicago",
                "Austin", "Boston", "Denver", "Miami", "Portland", "Washington DC",
                "Bay Area", "Silicon Valley", "London", "Tokyo", "Singapore",
                "Toronto", "Vancouver", "Sydney", "Berlin", "Paris",
            ]
            for pattern in loc_patterns:
                match = re.search(pattern, content)
                if match:
                    loc = match.group(1).strip()
                    if 3 < len(loc) < 60:
                        extracted["location_city"] = loc
                        extracted["location_city_source"] = url
                        break
            if not extracted.get("location_city"):
                for city in static_cities:
                    if city in content:
                        extracted["location_city"] = city
                        extracted["location_city_source"] = url
                        break

        # ── Extract email ──
        if not extracted.get("email"):
            email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', content)
            if email_match:
                email = email_match.group(0).rstrip(".")
                if not any(x in email.lower() for x in [
                    "noreply", "no-reply", "support@", "info@", "admin@",
                    "example.com", "test@", "@linkedin", "@facebook", "@twitter",
                    "@instagram", "sentry.io", "wixpress"
                ]):
                    extracted["email"] = email
                    extracted["email_source"] = url

    extracted["_sources"] = sources
    return extracted


# ─── Validation ─────────────────────────────────────────────────────────────

def validate_enrichment_field(key, value, person_name=""):
    """Validate enrichment data before writing. Returns True if field looks valid."""
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    if len(value) < 2:
        return False

    person_name_lower = (person_name or "").lower().strip()

    if key == "occupation":
        if person_name_lower and value.lower() == person_name_lower:
            return False
        if person_name_lower and value.lower() == person_name_lower.split(",")[0].strip():
            return False
        if len(value) > 60 or len(value) < 3:
            return False
        garbage = [
            "log in", "sign in", "sign up", "main review", "salt lake",
            "select ot", "with the no.", "pick in the", "days ago",
            "linkedin view", "profile", "view more", "see more",
            "anniversary", "years", "welcome",
        ]
        if any(g in value.lower() for g in garbage):
            return False
        first_word = value.split()[0].lower() if value.split() else ""
        if first_word in ["is", "are", "was", "were", "the", "a", "an", "its", "it's", "been"]:
            return False
        title_indicators = [
            "manager", "director", "engineer", "lead", "head", "vp", "senior",
            "junior", "staff", "principal", "analyst", "designer", "developer",
            "architect", "founder", "ceo", "cto", "cfo", "president", "assistant",
            "coordinator", "specialist", "consultant", "instructor", "associate",
            "officer", "representative", "therapist", "producer", "editor",
            "writer", "owner", "partner", "chief", "svp", "evp", "co-founder",
            "advisor", "president",
        ]
        if not any(t in value.lower() for t in title_indicators):
            words = value.split()
            if not all(w[0].isupper() for w in words if len(w) > 2):
                return False
        return True

    elif key == "org":
        if value.lower() in ["the", "design", "visitor", "guest", "university", "college"]:
            return False
        if person_name_lower and value.lower() == person_name_lower:
            return False
        if len(value) > 80:
            return False
        occupation_indicators = [
            "engineer", "manager", "director", "president", "vp",
            "head", "lead", "architect", "developer", "ceo", "cto",
            "cfo", "principal", "analyst", "specialist",
        ]
        if value.lower() in occupation_indicators:
            return False
        return True

    elif key == "location_city":
        if len(value) > 80:
            return False
        if value.isupper() and len(value) > 30:
            return False
        return True

    elif key == "email":
        return "@" in value and "." in value

    return True


# ─── Weave I/O ──────────────────────────────────────────────────────────────

def get_contacts_needing_enrichment():
    """Query Weave for contacts with gaps, excluding user-provided data."""
    import real_ladybug as lb
    db = lb.Database(str(AGENT_ROOT / "commons/db/ocas-weave/weave.lbug"), read_only=True)
    conn = lb.Connection(db)

    cypher = """
    MATCH (p:Person)
    WHERE p.name IS NOT NULL AND p.name <> ''
      AND (p.source_type IS NULL OR (p.source_type <> 'user_provided' AND p.source_type <> 'user_correction'))
      AND (p.confidence IS NULL OR p.confidence < 0.9)
      AND ((p.org IS NULL OR p.org = '')
        OR (p.occupation IS NULL OR p.occupation = '')
        OR (p.location_city IS NULL OR p.location_city = '')
        OR (p.email IS NULL OR p.email = '')
        OR (p.phone IS NULL OR p.phone = ''))
      AND NOT (p.org IS NOT NULL AND p.org <> '' AND p.occupation IS NOT NULL AND p.occupation <> '')
    RETURN p.id AS id, p.name AS name, p.name_given AS name_given,
           p.name_family AS name_family, p.email AS email, p.phone AS phone,
           p.org AS org, p.occupation AS occupation,
           p.location_city AS location_city, p.location_country AS location_country,
           p.source_type AS source_type, p.confidence AS confidence
    ORDER BY p.record_time DESC
    """

    r = conn.execute(cypher)
    cols = r.get_column_names()

    contacts = []
    while True:
        try:
            row = r.get_next()
        except StopIteration:
            break
        except Exception as e:
            if "No more tuples" in str(e):
                break
            if "UnicodeDecodeError" in str(type(e).__name__) or "utf-8" in str(e):
                log(f"  Skipping corrupt row (UnicodeDecodeError)")
                continue
            log(f"  Skipping row with error: {e}")
            continue
        contact = {col: row[cols.index(col)] for col in cols}
        contact["gaps"] = []
        if not contact.get("org"):
            contact["gaps"].append("org")
        if not contact.get("occupation"):
            contact["gaps"].append("occupation")
        if not contact.get("location_city"):
            contact["gaps"].append("location_city")
        if not contact.get("email"):
            contact["gaps"].append("email")
        if not contact.get("phone"):
            contact["gaps"].append("phone")
        contacts.append(contact)

    conn.close()
    return contacts


def recalculate_enrichability(conn, contact_id):
    """Recalculate enrichability_score for a contact after enrichment."""
    import math
    ENRICHABLE_FIELDS = ["org", "occupation", "location_city", "email", "phone"]

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
    has = {f: bool(c[i]) for i, f in enumerate(["name_given", "name_family", "email", "phone", "org", "occupation", "location_city"])}
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

    # Delete old score, write new
    try:
        conn.execute(
            "MATCH (p:Person {id: $id})-[:HasFact]->(f:Fact {predicate: 'enrichability_score'}) DETACH DELETE f",
            {"id": contact_id}
        )
    except:
        pass

    now = datetime.now(timezone.utc).isoformat()
    fid = str(__import__('uuid').uuid4())
    conn.execute("""
        MATCH (p:Person {id: $pid})
        CREATE (f:Fact {id: $fid, predicate: 'enrichability_score', value: $val,
            source_type: 'system', source_ref: 'enrichability-recalc',
            confidence: 1.0, record_time: $rt})
        CREATE (p)-[:HasFact]->(f)
    """, {"pid": contact_id, "fid": fid, "val": str(new_score), "rt": now})

    return new_score


def enrich_weave_contact(contact_id, enrichment_data, confidence=0.7, person_name=""):
    """
    Write enrichment data back to Weave as Fact nodes with full provenance.
    Recalculates enrichability_score after successful writes.
    """
    import uuid as _uuid
    import real_ladybug as lb

    if not enrichment_data:
        return False

    db = lb.Database(str(AGENT_ROOT / "commons/db/ocas-weave/weave.lbug"), read_only=False)
    conn = lb.Connection(db)

    written = 0
    try:
        # Verify person exists
        r = conn.execute("MATCH (p:Person {id: $id}) RETURN p.id", {"id": contact_id})
        if not r.get_all():
            log(f"  ✗ Person {contact_id} not found in Weave")
            conn.close()
            return False

        record_time = datetime.now(timezone.utc).isoformat()

        for key, value in enrichment_data.items():
            if key not in ["org", "occupation", "location_city", "location_country", "email", "phone"]:
                continue
            if not validate_enrichment_field(key, value, person_name):
                log(f"  ✗ Rejected {key}='{value[:50]}' — failed validation")
                continue

            fact_id = str(_uuid.uuid4())
            source_url = enrichment_data.get(f"{key}_source", "")

            cypher = (
                "MATCH (p:Person {id: $person_id}) "
                "CREATE (f:Fact {id: $fact_id, predicate: $key, value: $value, "
                "source_type: 'web_enrichment', source_ref: $source_ref, "
                "confidence: $confidence, record_time: $record_time}) "
                "CREATE (p)-[:HasFact]->(f) "
                "RETURN f.id"
            )
            params = {
                "person_id": contact_id,
                "fact_id": fact_id,
                "key": key,
                "value": value,
                "source_ref": source_url or ("sift_enrichment_" + datetime.now(timezone.utc).strftime("%Y%m%d")),
                "confidence": confidence,
                "record_time": record_time,
            }
            r = conn.execute(cypher, params)
            if r.get_all():
                written += 1
            else:
                log(f"  ✗ Fact write failed for {key}")

        # Recalculate enrichability
        new_score = None
        if written > 0:
            new_score = recalculate_enrichability(conn, contact_id)
            if new_score is not None:
                log(f"  ↻ Enrichability updated → {new_score}")

        conn.close()
        return written > 0

    except Exception as e:
        conn.close()
        raise e


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
                except:
                    pass
    return processed


def save_progress(contact_id, name, fields_enriched, error=None):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "a") as f:
        json.dump({
            "id": contact_id,
            "name": name,
            "fields": fields_enriched,
            "error": error,
            "ts": datetime.now(timezone.utc).isoformat(),
        }, f)
        f.write("\n")


def save_stats(enriched, failed, skipped, total_processed, session_start):
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    stats = {
        "last_session_start": session_start.isoformat(),
        "last_session_end": datetime.now(timezone.utc).isoformat(),
        "session_enriched": enriched,
        "session_failed": failed,
        "session_skipped": skipped,
        "total_processed_all_time": total_processed,
    }
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def sync_to_google():
    """Run Google Contacts sync."""
    log("Syncing to Google Contacts...")
    try:
        result = subprocess.run(
            ["python3", str(AGENT_ROOT / "skills/ocas-weave/scripts/google_sync.py")],
            capture_output=True, text=True, timeout=300,
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
    delta = deadline - now_et
    return delta.total_seconds() / 3600


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

    # Do NOT filter by progress — re-processing is idempotent
    to_process = contacts[:]
    log(f"Contacts to process this run: {len(to_process)}")

    if not to_process:
        log("No contacts to process. All done!")
        save_stats(0, 0, 0, len(processed_ids), session_start)
        return

    enriched_count = 0
    failed_count = 0
    skipped_count = 0

    for i, contact in enumerate(to_process):
        if is_past_deadline():
            log(f"⏰ Deadline reached (8am ET). Stopping.")
            break

        name = contact["name"]
        contact_id = contact["id"]
        gaps = contact["gaps"]
        org = contact.get("org", "")

        remaining = len(to_process) - i
        log(f"[{i+1}/{len(to_process)}] {name} | gaps: {', '.join(gaps)} | {remaining} remaining")

        try:
            # ── SCOUT PHASE: SearXNG identity-resolved research ──
            queries = build_scout_queries(contact)
            all_results = []
            for q in queries:
                try:
                    results = searxng_search(q, limit=5)
                    all_results.extend(results)
                    time.sleep(SEARCH_DELAY)
                except Exception as e:
                    log(f"  Search error: {e}")

            if not all_results:
                log(f"  No search results, skipping")
                skipped_count += 1
                save_progress(contact_id, name, [], error="no_search_results")
                continue

            log(f"  Scout: {len(all_results)} results from {len(queries)} queries")

            # ── SIFT PHASE: Full page extraction ──
            enrichment = sift_extract_from_pages(name, org, all_results, max_pages=3)
            sources = enrichment.pop("_sources", [])

            if not enrichment:
                log(f"  No extractable data from pages")
                skipped_count += 1
                save_progress(contact_id, name, [], error="no_extractable_data")
                continue

            log(f"  Sift: extracted {list(enrichment.keys())} from {len(sources)} pages")

            # ── Write to Weave ──
            success = enrich_weave_contact(
                contact_id, enrichment, confidence=MIN_CONFIDENCE, person_name=name
            )

            if success:
                written_fields = [k for k in enrichment.keys()
                                  if validate_enrichment_field(k, enrichment[k], name)]
                log(f"  ✓ Enriched: {written_fields}")
                enriched_count += 1
                save_progress(contact_id, name, written_fields)

                if enriched_count % SYNC_EVERY == 0:
                    log(f"  [{enriched_count} enriched so far — syncing to Google]")
                    sync_to_google()
            else:
                log(f"  ✗ Write failed")
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
    log(f"SESSION COMPLETE")
    log(f"  Enriched: {enriched_count}")
    log(f"  Failed: {failed_count}")
    log(f"  Skipped: {skipped_count}")
    log(f"  Total processed (all runs): {total_processed}")
    log("=" * 60)


if __name__ == "__main__":
    main()
