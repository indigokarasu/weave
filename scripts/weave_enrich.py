#!/usr/bin/env python3
"""
Shared enrichment extraction, search, and validation for Weave.

Used by both quick_enrich.py (single-contact interactive) and
overnight_enrichment.py (batch pipeline).

Import:
    from weave_enrich import (
        searxng_search, fetch_page, extract_from_content,
        validate_field, is_auth_walled, AUTH_WALLED,
        build_scout_queries, SEARXNG_URL, JINA_BASE,
        log, sift_extract_from_pages, enrich_weave_contact,
    )
"""
import json
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SEARXNG_URL = "http://localhost:8888/search"
JINA_BASE = "https://r.jina.ai"

AUTH_WALLED = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "tiktok.com",
}

TITLE_INDICATORS = [
    "manager", "director", "engineer", "lead", "head", "vp", "senior",
    "junior", "staff", "principal", "analyst", "designer", "developer",
    "architect", "founder", "ceo", "cto", "cfo", "president", "assistant",
    "coordinator", "specialist", "consultant", "instructor", "associate",
    "officer", "representative", "therapist", "producer", "editor",
    "writer", "owner", "partner", "chief", "svp", "evp", "co-founder",
    "advisor",
]

STATIC_CITIES = [
    "San Francisco", "New York", "Los Angeles", "Seattle", "Chicago",
    "Austin", "Boston", "Denver", "Miami", "Portland", "Washington DC",
    "Bay Area", "Silicon Valley", "London", "Tokyo", "Singapore",
    "Toronto", "Vancouver", "Sydney", "Berlin", "Paris",
]

EMAIL_BLACKLIST = [
    "noreply", "no-reply", "support@", "info@", "admin@",
    "example.com", "test@", "@linkedin", "@facebook", "@twitter",
    "@instagram", "sentry.io", "wixpress",
]


def is_auth_walled(url):
    """Check if a URL is behind an auth wall."""
    return any(domain in url for domain in AUTH_WALLED)


def searxng_search(query, limit=5):
    """Search via local SearXNG. Returns list of {url, title, content}."""
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": limit})
    url = f"{SEARXNG_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "HermesAgent/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return [
        {"url": i.get("url", ""), "title": i.get("title", ""), "content": i.get("content", "")}
        for i in data.get("results", [])[:limit]
    ]


def fetch_page(url):
    """
    Fetch a page via direct HTTP, falling back to Jina Reader.
    Returns (text_content, method) or (None, None).
    """
    # Try direct HTTP
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
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
        with urllib.request.urlopen(req, timeout=15) as	resp:
            text = resp.read().decode("utf-8", errors="ignore")
        if len(text) >= 100:
            return text[:5000], "jina"
    except Exception:
        pass

    return None, None


def extract_from_content(name, content, url):
    """
    Extract occupation, org, location_city, email from page content.
    Returns dict of extracted fields with {field}_source keys.
    """
    found = {}
    name_parts = name.lower().split()
    if not any(p in content.lower() for p in name_parts if len(p) > 3):
        return found

    # ── Occupation ──
    for pattern in [
        rf'{re.escape(name)}[,\s]+([A-Z][a-zA-Z\s&,/]+?)(?:\s+at\s+|\s*@|\s+for\s+)([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
        rf'([A-Z][a-zA-Z\s&,/]+?(?:Chief|Senior|Junior|Lead|Principal|Staff|VP|SVP|EVP|President|Director|Manager|Engineer|Designer|Developer|Analyst|Architect|Scientist|Researcher|Founder|Co-Founder|Partner|Officer|Coordinator|Specialist|Consultant|Advisor))\s+(?:at|@|of|for)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
        rf'(?:works?\s+(?:as|at)|is\s+(?:a|an|the)|serves?\s+as|appointed)\s+([A-Z][a-zA-Z\s&,/]+?)\s+(?:at|@|of|for)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
    ]:
        m = re.search(pattern, content)
        if m and m.lastindex >= 2:
            title, company = m.group(1).strip(), m.group(2).strip()
            if (3 < len(title) < 60
                and title.lower() not in ["linkedin", "view", "profile", "search", "the", "this", "that", "about", "contact"]
                and "http" not in title.lower()
                and 1 < len(company) < 80
                and company.lower() not in ["linkedin", "view", "profile", "search"]):
                found["occupation"] = title
                found["occupation_source"] = url
                found["org"] = company
                found["org_source"] = url
                break

    # ── Org (if not already found) ──
    if not found.get("org"):
        for pattern in [
            rf'{re.escape(name)}.*?(?:at|@|of|for|with)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
            rf'(?:company|organization|employer):\s*([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
        ]:
            m = re.search(pattern, content, re.IGNORECASE)
            if m:
                company = m.group(1).strip()
                if 1 < len(company) < 80 and company.lower() not in ["linkedin", "view", "profile", "search"]:
                    found["org"] = company
                    found["org_source"] = url
                    break

    # ── Location ──
    if not found.get("location_city"):
        for pattern in [
            rf'{re.escape(name)}.*?(?:in|based in|located in|from)\s+([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
            rf'(?:Location|Based|Located|Lives?|Area|City):\s*([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
            rf'(?:headquartered|based)\s+(?:in|at)\s+([A-Z][a-zA-Z\s]+,\s*[A-Z]{{2}})',
        ]:
            m = re.search(pattern, content)
            if m:
                loc = m.group(1).strip()
                if 3 < len(loc) < 60:
                    found["location_city"] = loc
                    found["location_city_source"] = url
                    break
        if not found.get("location_city"):
            for city in STATIC_CITIES:
                if city in content:
                    found["location_city"] = city
                    found["location_city_source"] = url
                    break

    # ── Email ──
    if not found.get("email"):
        m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', content)
        if m:
            email = m.group(0).rstrip(".")
            if not any(x in email.lower() for x in EMAIL_BLACKLIST):
                found["email"] = email
                found["email_source"] = url

    return found


def validate_field(key, value, person_name=""):
    """
    Validate enrichment field value. Returns True if the value looks real.
    Shared by both quick_enrich and overnight_enrichment.
    """
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    if len(value) < 2:
        return False

    pn = (person_name or "").lower().strip()

    if key == "occupation":
        if value.lower() == pn:
            return False
        if pn and value.lower() == pn.split(",")[0].strip():
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
        first = value.split()[0].lower() if value.split() else ""
        if first in ["is", "are", "was", "were", "the", "a", "an", "its", "it's", "been"]:
            return False
        if not any(t in value.lower() for t in TITLE_INDICATORS):
            words = value.split()
            if not all(w[0].isupper() for w in words if len(w) > 2):
                return False
        return True

    elif key == "org":
        if value.lower() in ["the", "design", "visitor", "guest", "university", "college"]:
            return False
        if value.lower() == pn:
            return False
        if len(value) > 80 or len(value) < 2:
            return False
        # Reject single generic words that are not company names
        generic_orgs = [
            "engineer", "manager", "director", "president", "vp",
            "head", "lead", "architect", "developer", "ceo", "cto",
            "cfo", "principal", "analyst", "specialist", "professional",
            "accidents", "newsletter", "employees", "per", "joy",
        ]
        if value.lower() in generic_orgs:
            return False
        # Reject if value is a known city name (likely location, not org)
        if value in STATIC_CITIES:
            return False
        # Reject sentence fragments (contains common non-org words)
        lower = value.lower()
        if any(w in lower for w in ["was", "were", "been", "being", "have", "has", "had", "this", "that", "with"]):
            return False
        # Must have at least one capital letter (company names are proper nouns)
        if not any(c.isupper() for c in value):
            return False
        return True

    elif key == "location_city":
        return len(value) <= 80 and not (value.isupper() and len(value) > 30)

    elif key == "email":
        return "@" in value and "." in value

    return True


def build_scout_queries(name, name_given="", name_family="", org=""):
    """Build identity-resolved search queries for the Scout phase."""
    queries = []
    if name_given and name_family:
        queries.append(f'"{name_given} {name_family}" LinkedIn')
        queries.append(f'"{name_given} {name_family}" site:linkedin.com/in')
    if org:
        queries.append(f'"{name}" {org}')
        queries.append(f'"{name}" at {org}')
    if not queries:
        queries.append(f'"{name}" professional')
    return queries[:4]


# ── Shared utilities ──────────────────────────────────────────────────────

def log(msg):
    """Timestamped log output for overnight enrichment scripts."""
    ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")
    print(f"[{ts}] {msg}", flush=True)


def sift_extract_from_pages(name, search_results, max_pages=3):
    """
    Sift phase: fetch full pages from search results and extract structured data.
    Skips auth-walled domains. Fetches up to max_pages unique domains.
    Returns dict of extracted fields with source URLs + _sources key.
    """
    extracted = {}
    fetched_domains = set()
    sources = []

    for result in search_results:
        url = result.get("url", "")
        if not url or is_auth_walled(url):
            continue
        domain = urllib.parse.urlparse(url).netloc
        if domain in fetched_domains or len(fetched_domains) >= max_pages:
            continue
        fetched_domains.add(domain)

        content, method = fetch_page(url)
        if not content:
            continue

        sources.append({"url": url, "domain": domain, "method": method})
        page_data = extract_from_content(name, content, url)
        for k, v in page_data.items():
            if k not in extracted:
                extracted[k] = v

    extracted["_sources"] = sources
    return extracted


def enrich_weave_contact(contact_id, enrichment_data, confidence=0.7, person_name=""):
    """Write enrichment to the People DB (Choice-2). Accepts a people.db opaque id OR a
    legacy Weave id (resolved via external_refs). Shared by quick_enrich and overnight_enrichment."""
    sys.path.insert(0, str(Path(__file__).parent))
    from people_db import PeopleDB
    from datetime import datetime as _dt, timezone as _tz
    db = PeopleDB()
    if not enrichment_data:
        return False
    pid = contact_id if db.get(contact_id) else db.resolve(external=("weave", contact_id))
    if not pid:
        return False
    _today = _dt.now(_tz.utc).strftime("%Y%m%d")
    written = 0
    for key, value in enrichment_data.items():
        if key.startswith("_") or key.endswith("_source"):
            continue
        if key not in ["org", "occupation", "location_city", "location_country", "email", "phone"]:
            continue
        if not validate_field(key, value, person_name):
            continue
        ref = enrichment_data.get(f"{key}_source", "") or ("enrichment_" + _today)
        if key in ("email", "phone"):
            db.add_identifier(pid, key, value)
        else:
            db.set_attribute(pid, key, value, confidence=confidence, source_type="web_enrichment", source_ref=ref)
        written += 1
    return written > 0
