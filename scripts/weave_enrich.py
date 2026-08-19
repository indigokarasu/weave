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

Design contract (2026-08-14 rewrite):
  - Heuristics may cheaply REJECT a page (subject's family name absent) and may
    produce CANDIDATES (handles, emails found on a page). They never confirm
    identity and never emit an identity-confidence number.
  - Only llm_verify_extract() may confirm identity or attribute a field to the
    contact. No LLM backend reachable -> nothing is written (fail closed).
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 weave_enrich.py")
    sys.exit(0)


PROFILE = os.environ.get("HERMES_PROFILE", "indigo")

# Base URLs for providers that don't carry one in config. Purely a lookup
# table — the profile config decides which (if any) is ever used.
_PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "nous": "https://inference-api.nousresearch.com/v1",
    "ollama": "http://localhost:11434/v1",
}

SEARXNG_URL = "http://localhost:8888/search"
JINA_BASE = "https://r.jina.ai"

AUTH_WALLED = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "tiktok.com",
}

# Domains to skip entirely (noise). Auth-walled domains are NOT here: their
# search-result titles/snippets carry signal and are kept as snippet-only
# candidates. Matching is host-suffix based, not substring.
SKIP_DOMAINS = {
    "youtube.com", "tiktok.com", "reddit.com", "quora.com",
    "pinterest.com", "britannica.com", "education.gov",
}

PREFERRED_DOMAINS = {
    "linkedin": 0.9,
    "crunchbase.com": 0.9,
    "bloomberg.com": 0.9,
    "twitter.com": 0.85,
    ".com": 0.7,
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

# Generic mailbox local-parts that are never a person's own address.
GENERIC_EMAIL_LOCALS = {
    "info", "support", "webmaster", "admin", "hello", "contact",
    "noreply", "no-reply", "privacy", "sales", "press", "office",
    "mail", "team", "help", "jobs", "careers", "abuse", "postmaster",
    "security", "billing", "service", "feedback", "newsletter",
}

# Domains whose emails are never a contact's personal address.
JUNK_EMAIL_DOMAIN_RE = re.compile(
    r"(^|\.)(example\.(com|org|net)|sentry\.io|wixpress\.com|schema\.org|"
    r"sentry\.wixpress\.com|w3\.org|domain\.com|email\.com|yourdomain\.com)$",
    re.I,
)

ASSET_EXT_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|ico|woff2?)$", re.I)

# github.com/<seg> paths that are site sections, not user profiles.
GITHUB_RESERVED = {
    "features", "about", "orgs", "topics", "marketplace", "sponsors",
    "blog", "pricing", "site", "search", "login", "join", "contact",
    "security", "explore", "trending", "collections", "events", "apps",
    "settings", "enterprise", "team", "customer-stories", "readme",
    "new", "notifications", "stars", "issues", "pulls", "pulls-requests",
    "site-map", "git-guides", "resources", "solutions", "premium-support",
}

# twitter.com/x.com first path segments that are not profiles.
TWITTER_RESERVED = {
    "share", "intent", "home", "search", "hashtag", "i", "status",
    "login", "signup", "explore", "privacy", "tos", "settings",
    "notifications", "messages", "compose", "download", "en", "about",
}

_LINKEDIN_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9\-_%.]+)", re.I)
_GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9-]+)(?=[\"'/\s?#)\],]|$)", re.I)
_TWITTER_RE = re.compile(
    r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})(?=[\"'/\s?#)\],]|$)", re.I
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_ORG_SHAPED_LAST_TOKENS = {
    "services", "service", "inc", "llc", "corp", "ltd", "company", "group",
}


def _host_of(url):
    """Lowercased host of a URL, tolerant of scheme-less input."""
    try:
        netloc = urllib.parse.urlparse(url if "://" in url else "http://" + url).netloc
    except Exception:
        return ""
    return netloc.lower().split(":")[0]


def _host_matches(host, domain):
    return host == domain or host.endswith("." + domain)


def is_auth_walled(url):
    """Check if a URL is behind an auth wall."""
    host = _host_of(url)
    return any(_host_matches(host, d) for d in AUTH_WALLED)


def should_skip_domain(url):
    """Return True if domain is pure noise for enrichment."""
    host = _host_of(url)
    return any(_host_matches(host, d) for d in SKIP_DOMAINS)


def clean_person_name(name):
    """Strip parenthesized annotations and quotes from a contact name.

    'Sarah (art Class)' -> 'Sarah'; 'Robert "Bob" Jones' -> 'Robert Bob Jones'.
    """
    cleaned = re.sub(r"\([^)]*\)", " ", name or "")
    cleaned = cleaned.replace('"', " ").replace("'", " ")
    return " ".join(cleaned.split())


def _name_tokens(name):
    """Alphabetic tokens (len >= 2) of a cleaned name, lowercased."""
    return [t for t in re.findall(r"[A-Za-z]+", clean_person_name(name)) if len(t) >= 2]


def _anchor_name_tokens(name):
    """Tokens for the ENRICHABILITY decision, counting initials.

    A given name written as initials still identifies a person when paired with a
    surname, so it must not be mistaken for a lone first name. _name_tokens drops
    single characters and is left alone: its other callers compare tokens against
    profile text, where a one-character token matches almost anything.
    """
    import re as _re
    return [t for t in _re.findall(r"[A-Za-z]+", clean_person_name(name)) if len(t) >= 1]

def has_sufficient_anchors(person):
    """Decide whether a contact is enrichable by web search at all.

    Takes a dict (or sqlite3.Row-like) with name / name_given / name_family.
    Returns (ok: bool, reason: str). A single-token name ('Sarah') or an
    org-shaped name ('Coastal Plumbing Services') can never be safely
    disambiguated -> refuse to search rather than guess.
    """
    if hasattr(person, "keys") and not isinstance(person, dict):
        person = {k: person[k] for k in person.keys()}
    name = (person.get("name") or "").strip()
    if not name:
        given = (person.get("name_given") or "").strip()
        family = (person.get("name_family") or "").strip()
        name = f"{given} {family}".strip()

    # Counting initials here: "<I>.<I>. <Family>" is a searchable name, and dropping
    # the initials made it look like a lone given name.
    tokens = _anchor_name_tokens(name)
    if len(tokens) < 2:
        # A REAL family name stored separately counts as a second token —
        # but only after cleaning: contacts store junk like "(art Class)"
        # in name_family, which must not rescue an unenrichable name.
        family_tokens = _anchor_name_tokens(person.get("name_family") or "")
        tokens.extend(
            t for t in family_tokens
            if not tokens or t.lower() != tokens[0].lower()
        )
        if len(tokens) < 2:
            return False, "single-token name"

    if tokens[-1].lower() in _ORG_SHAPED_LAST_TOKENS:
        return False, "org-shaped name"

    return True, "ok"


def searxng_search(query, limit=5, searxng_url=None, retries=2):
    """Search via local SearXNG. Returns list of {url, title, content}.

    Retries transient failures with backoff — the local SearXNG container
    drops connections (and occasionally restarts) under bursty query load.
    Raises on final failure so callers can distinguish "search infrastructure
    down" from "no results found".
    """
    base = (searxng_url or SEARXNG_URL).rstrip("/")
    if not base.endswith("/search"):
        base = base + "/search"
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": limit})
    url = f"{base}?{params}"
    last_err = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(1.5 * (2 ** (attempt - 1)))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HermesAgent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            return [
                {"url": i.get("url", ""), "title": i.get("title", ""), "content": i.get("content", "")}
                for i in data.get("results", [])[:limit]
            ]
        except Exception as e:
            last_err = e
    raise last_err


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

    # Fallback: Jina Reader (JINA_BASE has no trailing slash; the joining
    # slash lives here and only here).
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


def extract_from_content(name, content, url):
    """
    Extract occupation, org, location_city, email from page content.
    Returns dict of extracted fields with {field}_source and {field}_confidence keys.
    Confidence: 0.9=near name, 0.7=in context, 0.5=isolated, 0.3=uncertain.

    NOTE: regex extraction is candidate material for the LLM gate only.
    sift_extract_from_pages never writes these fields without LLM confirmation.
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
                found["occupation_confidence"] = 0.9 if name.lower() in content[max(0, m.start()-50):m.end()+50].lower() else 0.7
                found["org"] = company
                found["org_source"] = url
                found["org_confidence"] = 0.9 if name.lower() in content[max(0, m.start()-50):m.end()+50].lower() else 0.7
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
                context_window = content[max(0, m.start()-200):m.end()+200].lower()
                has_name = any(p in context_window for p in name_parts if len(p) > 3)
                has_org = found.get("org", "").lower() in context_window if found.get("org") else False
                if has_name and has_org:
                    conf = 0.95
                elif has_name:
                    conf = 0.85
                elif found.get("occupation") or found.get("org"):
                    conf = 0.75
                else:
                    conf = 0.6
                found["email_confidence"] = conf

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
        # Reject bracketed placeholders (e.g. "[LinkedIn: handle]") — data, not prose
        if value.startswith("[") or value.endswith("]"):
            return False
        generic_orgs = [
            "engineer", "manager", "director", "president", "vp",
            "head", "lead", "architect", "developer", "ceo", "cto",
            "cfo", "principal", "analyst", "specialist", "professional",
            "accidents", "newsletter", "employees", "per", "joy",
        ]
        if value.lower() in generic_orgs:
            return False
        if value in STATIC_CITIES:
            return False
        lower = value.lower()
        if any(w in lower for w in ["was", "were", "been", "being", "have", "has", "had", "this", "that", "with"]):
            return False
        if not any(c.isupper() for c in value):
            return False
        return True

    elif key == "location_city":
        return len(value) <= 80 and not (value.isupper() and len(value) > 30)

    elif key == "email":
        return "@" in value and "." in value

    return True


# ── URL normalization / dedup ─────────────────────────────────────────────

_TRACKING_PARAM_RE = re.compile(r"^(utm_|fbclid|gclid|mc_cid|mc_eid|ref_)", re.I)


def normalize_url(url):
    """Canonical form for dedup: collapse www./m./en.m. hosts, drop tracking
    params and fragments, strip trailing slash. Distinct pages stay distinct."""
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url if "://" in url else "http://" + url)
    except Exception:
        return url.lower()
    host = parts.netloc.lower().split(":")[0]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    host = host.replace(".m.", ".")
    path = parts.path.rstrip("/")
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING_PARAM_RE.match(k)
    ]
    query = urllib.parse.urlencode(kept)
    return f"{host}{path}" + (f"?{query}" if query else "")


# ── Email-anchor OSINT helpers ────────────────────────────────────────────
# A personal email in the contact row is the strongest anchor we hold: its
# local part is a handle people reuse across platforms, and the address
# itself keys services like Gravatar. Name searches disambiguate poorly;
# handle pivots identify by construction.

def _email_local(email):
    """Lowercased local part of an email, with plus-addressing stripped."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[0].split("+", 1)[0]


def email_handle_variants(email):
    """Distinctive handle candidates derived from an email's local part.

    'wrenkeeley@fastmail.com' -> ['wrenkeeley']
    'tomasvega.design@fastmail.com' -> ['tomasvega.design',
                                       'tomasvegadesign',
                                       'tomasvega-design']
    Generic mailboxes and short locals yield [] — probing 'info' or 'jo'
    across platforms only manufactures wrong people.
    """
    local = _email_local(email)
    if not local or len(local) < 5 or local in GENERIC_EMAIL_LOCALS:
        return []
    variants = [local]
    squashed = re.sub(r"[._-]", "", local)
    if squashed != local and len(squashed) >= 5:
        variants.append(squashed)
    dashed = re.sub(r"[._]", "-", local)
    if dashed not in variants:
        variants.append(dashed)
    return variants


def _http_get(url, timeout=10):
    """GET returning body text only on a real 200 — None on any error.
    Unlike fetch_page(), this never falls back to Jina: a 404 must stay a
    miss, not become a readable 'Not Found' page."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


# ── Candidate signal extraction (no confidence numbers, ever) ─────────────

def _slug_matches_name(slug, tokens):
    flat = re.sub(r"[^a-z0-9]", "", (slug or "").lower())
    return any(t in flat for t in tokens)


def extract_links_from_page(html_content, page_url="", subject_name=""):
    """Collect candidate social handles and emails from page content.

    Returns:
        {"candidates": {"linkedin": [..], "github": [..], "twitter": [..]},
         "emails": [{"addr": .., "name_consistent": bool}, ..],
         "links_found": int, "notes": str}

    Candidates are leads for the LLM gate, NOT attributions. This function
    deliberately emits no confidence number: presence of links on a page says
    nothing about whose links they are.
    """
    out = {
        "candidates": {"linkedin": [], "github": [], "twitter": []},
        "emails": [],
        "links_found": 0,
        "notes": "",
    }
    if not html_content:
        return out

    tokens = [t.lower() for t in _name_tokens(subject_name) if len(t) >= 3]
    notes = []
    if not tokens:
        notes.append("no subject name given; handle candidates unfiltered")

    def _keep(slug):
        return _slug_matches_name(slug, tokens) if tokens else True

    seen = set()
    for m in _LINKEDIN_RE.finditer(html_content):
        slug = m.group(1).rstrip(".-_%")
        key = ("linkedin", slug.lower())
        if key in seen or not slug:
            continue
        seen.add(key)
        if _keep(slug):
            out["candidates"]["linkedin"].append(slug.lower())

    for m in _GITHUB_RE.finditer(html_content):
        slug = m.group(1)
        if slug.lower() in GITHUB_RESERVED:
            continue
        key = ("github", slug.lower())
        if key in seen:
            continue
        seen.add(key)
        if _keep(slug):
            out["candidates"]["github"].append(slug.lower())

    for m in _TWITTER_RE.finditer(html_content):
        slug = m.group(1)
        if slug.lower() in TWITTER_RESERVED:
            continue
        key = ("twitter", slug.lower())
        if key in seen:
            continue
        seen.add(key)
        if _keep(slug):
            out["candidates"]["twitter"].append(slug.lower())

    for m in _EMAIL_RE.finditer(html_content):
        addr = m.group(0).lower().rstrip(".")
        if addr in {e["addr"] for e in out["emails"]}:
            continue
        # An "email" immediately followed by '/' is a URL path segment or
        # asset reference (icons@site.com/logo.png), not a mailbox.
        end = m.end()
        if end < len(html_content) and html_content[end] == "/":
            continue
        local, _, domain = addr.partition("@")
        if local in GENERIC_EMAIL_LOCALS:
            continue
        if JUNK_EMAIL_DOMAIN_RE.search(domain):
            continue
        if ASSET_EXT_RE.search(addr):
            continue
        name_consistent = bool(tokens) and any(t in local for t in tokens)
        out["emails"].append({"addr": addr, "name_consistent": name_consistent})

    out["links_found"] = (
        len(out["candidates"]["linkedin"])
        + len(out["candidates"]["github"])
        + len(out["candidates"]["twitter"])
        + len(out["emails"])
    )
    out["notes"] = "; ".join(notes)
    return out


# ── Cheap identity screen (reject-only; can never confirm) ────────────────

def verify_identity_from_page(page_content, search_name, context_org="",
                              context_occ="", context_location=""):
    """Cheap screen: is this page even POSSIBLY about the subject?

    Returns {"verdict": "reject"|"candidate", "reasons": [..], "hints": {..}}.
    Rejects when the subject's family name is absent from the text. Anything
    that survives is only ever a CANDIDATE — confirmation is the LLM's job.
    Emits no numbers by design.
    """
    result = {
        "verdict": "reject",
        "reasons": [],
        "hints": {
            "family_name_present": False,
            "org_present": False,
            "city_present": False,
            "occupation_terms_present": False,
        },
    }
    if not page_content:
        result["reasons"].append("empty page content")
        return result

    tokens = _name_tokens(search_name)
    if len(tokens) < 2:
        result["reasons"].append("no family name to verify against")
        return result

    text = page_content.lower()
    family = tokens[-1].lower()
    if family not in text:
        result["reasons"].append(f"family name '{family}' absent from page")
        return result

    result["verdict"] = "candidate"
    result["hints"]["family_name_present"] = True
    if context_org and context_org.strip().lower() in text:
        result["hints"]["org_present"] = True
    if context_location:
        city = context_location.split(",")[0].strip().lower()
        if city and city in text:
            result["hints"]["city_present"] = True
    if context_occ:
        occ_words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", context_occ)]
        if any(w in text for w in occ_words):
            result["hints"]["occupation_terms_present"] = True
    return result


# ── Discovery: search + fetch + screen (produces candidates for judging) ──

def fetch_search_results_with_links(search_name, queries, context_org="",
                                    context_occ="", context_location="",
                                    searxng_url=None, jina_base=None,
                                    max_fetches=8, results_per_query=5,
                                    stats=None):
    """Run Scout queries, dedupe result URLs, fetch and screen pages, and
    return surviving candidate pages with extracted signal candidates.

    Returns a list of dicts:
        {url, title, snippet, fetched, excerpt, signals, screen}
    Ordering is by evidence hints (org/city co-occurrence, fetched first) —
    deliberately NOT by a fabricated numeric confidence.
    jina_base is accepted for backward compatibility and ignored; all
    fetching goes through fetch_page(), which owns the Jina fallback.
    Pass a dict as `stats` to receive counters — searches_failed > 0 with an
    empty return means "infrastructure problem", NOT "person not found".
    """
    candidates = []
    seen_urls = set()
    fetched_count = 0
    rejected = 0
    if stats is None:
        stats = {}
    stats.update({"searches_attempted": 0, "searches_failed": 0,
                  "results_seen": 0, "rejected_by_screen": 0, "fetched": 0})

    for qi, query in enumerate(queries or []):
        if qi > 0:
            time.sleep(2.0)
        stats["searches_attempted"] += 1
        try:
            results = searxng_search(query, limit=results_per_query,
                                     searxng_url=searxng_url)
        except Exception as e:
            stats["searches_failed"] += 1
            log(f"  discovery: search failed for '{query[:60]}': {str(e)[:80]}")
            continue

        for result in results:
            stats["results_seen"] += 1
            url = result.get("url", "")
            norm = normalize_url(url)
            if not norm or norm in seen_urls:
                continue
            seen_urls.add(norm)

            if should_skip_domain(url):
                continue

            title = result.get("title", "")
            snippet = result.get("content", "")

            page_text = None
            fetched = False
            if not is_auth_walled(url) and fetched_count < max_fetches:
                if fetched_count > 0:
                    time.sleep(0.5)
                page_text, _method = fetch_page(url)
                fetched_count += 1
                fetched = page_text is not None

            screen_text = page_text if page_text else f"{title} {snippet}"
            screen = verify_identity_from_page(
                screen_text, search_name, context_org, context_occ,
                context_location,
            )
            if screen["verdict"] == "reject":
                rejected += 1
                continue

            signals = extract_links_from_page(page_text or "", url, search_name)

            excerpt = ""
            if page_text:
                excerpt = page_text[:800]
                tokens = _name_tokens(search_name)
                if tokens:
                    fam = tokens[-1].lower()
                    idx = page_text.lower().find(fam)
                    if idx > 800:
                        lo = max(0, idx - 150)
                        excerpt += " [...] " + page_text[lo:idx + 150]

            candidates.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "fetched": fetched,
                "excerpt": excerpt,
                "signals": signals,
                "screen": screen,
            })

        if len(candidates) >= 5 or fetched_count >= max_fetches:
            break

    stats["rejected_by_screen"] = rejected
    stats["fetched"] = fetched_count
    log(f"  discovery: {len(candidates)} candidate page(s), "
        f"{rejected} rejected by name screen, {fetched_count} fetched, "
        f"{stats['searches_failed']}/{stats['searches_attempted']} searches failed")

    def _order(c):
        h = c["screen"]["hints"]
        return (
            not h.get("org_present", False),
            not h.get("city_present", False),
            not h.get("occupation_terms_present", False),
            not c["fetched"],
        )

    candidates.sort(key=_order)
    return candidates


# Platforms where a reused handle resolves to a public profile. Each entry:
# (platform, url_template, present_test) — present_test(body, status_ok) says
# whether the fetched page is a real profile vs a "user not found" shell.
def _profile_probe_targets(handle):
    return [
        ("github", f"https://github.com/{handle}",
         lambda b: b is not None and f'/{handle}?tab=repositories' in b.lower()
                   or (b is not None and 'itemprop="additionalname"' in b.lower())),
        ("github_api", f"https://api.github.com/users/{handle}",
         lambda b: b is not None and '"type"' in b and '"message"' not in b[:80].lower()),
        ("instagram", f"https://www.instagram.com/{handle}/",
         lambda b: b is not None and (f'"alternateName":"@{handle}"' in b
                   or f'@{handle}' in b) and 'Page Not Found' not in b),
        ("bluesky", f"https://bsky.app/profile/{handle}.bsky.social",
         lambda b: b is not None and handle in (b or "").lower()
                   and 'Profile not found' not in (b or "")),
        ("wordpress", f"https://{handle}.wordpress.com/",
         lambda b: b is not None and 'doesn&#8217;t exist' not in (b or "")
                   and 'do not exist' not in (b or "").lower()),
        ("gitlab", f"https://gitlab.com/{handle}",
         lambda b: b is not None and f'@{handle}' in (b or "")),
    ]


def parse_github_api_user(body):
    """Extract structured identity fields from an api.github.com/users/<h> body.

    Returns {} on any parse failure or a not-found shell. The GitHub user API
    hands us name/company/location/blog as clean JSON tied to a specific
    handle — no scraping, no guessing. Field values map to Weave columns.
    """
    try:
        d = json.loads(body or "")
    except Exception:
        return {}
    if not isinstance(d, dict) or "login" not in d or d.get("message"):
        return {}
    out = {}
    if d.get("name"):
        out["_github_name"] = d["name"].strip()
    company = (d.get("company") or "").strip().lstrip("@")
    if company:
        out["org"] = company
    if d.get("location"):
        out["location_city"] = d["location"].strip()
    blog = (d.get("blog") or "").strip()
    if blog:
        out["website"] = blog
    if d.get("bio"):
        out["_github_bio"] = d["bio"].strip()
    if d.get("email"):
        out["email"] = d["email"].strip()
    return out


def discover_by_email(email, subject_name="", searxng_url=None, stats=None):
    """Handle-pivot OSINT anchored on a contact's email.

    Two moves basic OSINT always makes and the name-only path skipped:
      1. Probe handle reuse: does the email's local part resolve to a public
         profile on GitHub / Instagram / Bluesky / WordPress / GitLab?
      2. Search the distinctive local part as a query (it disambiguates far
         better than a common name).
    Returns a list of candidate dicts shaped like fetch_search_results_with_links,
    each tagged origin='email-handle' or 'email-search' with the anchor that
    produced it, so the LLM gate can weigh provenance.
    """
    out = []
    if stats is None:
        stats = {}
    stats.setdefault("handles_probed", 0)
    stats.setdefault("profiles_hit", 0)

    variants = email_handle_variants(email)
    if not variants:
        return out
    seen = set()

    # 1. Handle-reuse probes
    for handle in variants:
        for platform, url, present in _profile_probe_targets(handle):
            norm = normalize_url(url)
            if norm in seen:
                continue
            seen.add(norm)
            stats["handles_probed"] += 1
            time.sleep(0.3)
            body = _http_get(url, timeout=8)
            try:
                hit = present(body)
            except Exception:
                hit = False
            if not hit:
                continue
            stats["profiles_hit"] += 1
            signals = extract_links_from_page(body or "", url, subject_name)
            cand = {
                "url": url,
                "title": f"{platform} profile @{handle}",
                "snippet": f"Handle '{handle}' derived from {email} resolves on {platform}.",
                "fetched": body is not None,
                "excerpt": (body or "")[:800],
                "signals": signals,
                "screen": {"verdict": "candidate",
                           "reasons": [f"handle reuse from email on {platform}"],
                           "hints": {}},
                "origin": "email-handle",
                "anchor": f"{email} -> {handle} @ {platform}",
            }
            if platform == "github_api":
                fields = parse_github_api_user(body)
                if fields:
                    cand["structured_fields"] = fields
                    gh_name = fields.get("_github_name", "")
                    # Independent identity corroboration: the handle came from
                    # the email, and the profile's real name matches the
                    # contact's family name -> two anchors agree.
                    subj_tokens = [t.lower() for t in _name_tokens(subject_name)]
                    if subj_tokens and gh_name:
                        gh_lower = gh_name.lower()
                        if all(t in gh_lower for t in subj_tokens):
                            cand["screen"]["reasons"].append(
                                f"github name '{gh_name}' matches contact name")
                            cand["name_corroborated"] = True
                    parts = [f"{k}={v}" for k, v in fields.items()]
                    cand["excerpt"] = "GitHub API: " + "; ".join(parts)
            out.append(cand)

    # 2. Local-part-as-query search
    local = _email_local(email)
    if local and len(local) >= 5:
        try:
            results = searxng_search(local, limit=6, searxng_url=searxng_url)
        except Exception as e:
            log(f"  email-search: '{local}' failed: {str(e)[:80]}")
            results = []
        for r in results:
            url = r.get("url", "")
            norm = normalize_url(url)
            if not norm or norm in seen or should_skip_domain(url):
                continue
            seen.add(norm)
            out.append({
                "url": url,
                "title": r.get("title", ""),
                "snippet": r.get("content", ""),
                "fetched": False,
                "excerpt": "",
                "signals": {"candidates": {"linkedin": [], "github": [], "twitter": []},
                            "emails": [], "links_found": 0, "notes": ""},
                "screen": {"verdict": "candidate",
                           "reasons": ["matched distinctive email local-part"],
                           "hints": {}},
                "origin": "email-search",
                "anchor": f"{email} local-part '{local}'",
            })

    log(f"  email-anchor: {stats['profiles_hit']} profile hit(s) from "
        f"{stats['handles_probed']} probe(s); {len(out)} candidate(s) total")
    return out


def build_scout_queries(name, name_given="", name_family="", org="", occupation="", location_city=""):
    """Build scout queries, most-specific first.

    TIER 1: site: filters (LinkedIn, GitHub, Crunchbase)
    TIER 2: boolean AND (better than bare quotes for common names)
    TIER 3: context-rich (org/occupation/location combinations)
    TIER 4: fallback (name only)
    """
    org = (org or "").strip()
    occupation = (occupation or "").strip()
    location_city = (location_city or "").strip()
    city_head = location_city.split(",")[0].strip()

    safe_name = clean_person_name(name)
    if not safe_name:
        return []

    queries = []

    # TIER 1: site: filters
    if org:
        queries.append(f'site:linkedin.com "{safe_name}" {org}')
    else:
        queries.append(f'site:linkedin.com "{safe_name}"')
    queries.append(f'site:github.com "{safe_name}"')
    if org:
        queries.append(f'site:crunchbase.com "{safe_name}" {org}')

    # TIER 2: boolean AND
    if org:
        queries.append(f'{safe_name} AND {org}')
        queries.append(f'"{safe_name}" AND ({org} OR company)')

    # TIER 3: context-rich
    if org and occupation:
        queries.append(f'{safe_name} {org} {occupation.split()[0]}')
    if org and city_head:
        queries.append(f'{safe_name} {org} {city_head}')
    if not org and occupation:
        queries.append(f'"{safe_name}" {occupation}')
    if not org and city_head:
        queries.append(f'"{safe_name}" {city_head}')

    # TIER 4: fallback
    queries.append(f'{safe_name} professional')
    queries.append(safe_name)

    seen = set()
    result = []
    for q in queries:
        if q not in seen:
            result.append(q)
            seen.add(q)

    return result[:8]


def build_scout_queries_for_person(person):
    """Build scout queries from a persons row (dict or sqlite3.Row)."""
    if hasattr(person, "keys") and not isinstance(person, dict):
        person = {k: person[k] for k in person.keys()}
    get = lambda k, d="": person.get(k) or d

    return build_scout_queries(
        name=get("name", ""),
        name_given=get("name_given", ""),
        name_family=get("name_family", ""),
        org=get("org", ""),
        occupation=get("occupation", ""),
        location_city=get("location_city", ""),
    )


# ── Shared utilities ──────────────────────────────────────────────────────

def log(msg):
    """Timestamped log output for overnight enrichment scripts."""
    ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# LLM identity gate (Sift phase). A page only contributes fields if a model
# confirms it is about THIS person.
#
# NO PROVIDER IS NAMED HERE. The model, provider and base URL come from the
# running Hermes profile's config (model.default / model.provider /
# model.base_url, with fallback_providers), so the gate follows whatever the
# agent is configured to use. Endpoints are OpenAI-compatible
# /chat/completions, which nous, OpenRouter, Groq and local runtimes all speak.
# No reachable backend -> the page contributes nothing; deferring is cheaper
# than un-writing a wrong identity later.

_LLM_CFG_CACHE = None


def _hermes_env(name):
    """Read a key from the profile .env, then the global one. Never logged."""
    for env_path in (Path.home() / ".hermes" / "profiles" / PROFILE / ".env",
                     Path.home() / ".hermes" / ".env"):
        try:
            for line in env_path.read_text().splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return os.environ.get(name, "")


def _llm_config():
    """(base_url, model, api_key) for the profile's configured LLM, or None.

    Reads the Hermes profile config so this module stays provider-agnostic.
    """
    global _LLM_CFG_CACHE
    if _LLM_CFG_CACHE is not None:
        return _LLM_CFG_CACHE or None

    cfg_path = Path.home() / ".hermes" / "profiles" / PROFILE / "config.yaml"
    candidates = []
    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception as e:
        log(f"  llm-gate: cannot read profile config ({str(e)[:60]}); deferring")
        _LLM_CFG_CACHE = False
        return None

    providers = cfg.get("providers") or {}

    def _provider_bits(provider, model, base_url=""):
        provider = (provider or "").strip()
        pconf = providers.get(provider) or {}
        base = base_url or pconf.get("base_url") or _PROVIDER_BASE_URLS.get(provider, "")
        key = pconf.get("api_key") or _hermes_env(f"{provider.upper()}_API_KEY")
        return (base, model, key) if base and model and key else None

    m = cfg.get("model") or {}
    bits = _provider_bits(m.get("provider"), m.get("default"), m.get("base_url"))
    if bits:
        candidates.append(bits)
    for fb in (cfg.get("fallback_providers") or []):
        bits = _provider_bits(fb.get("provider"), fb.get("model"))
        if bits:
            candidates.append(bits)

    _LLM_CFG_CACHE = candidates[0] if candidates else False
    if not candidates:
        log("  llm-gate: no usable provider in profile config; deferring")
        return None
    return _LLM_CFG_CACHE


def llm_verify_extract(name, url, content, context=None):
    """One page -> verified fields about this person, {} if wrong person or
    nothing stated, None if no LLM backend was reachable (caller defers).
    """
    import json as _json

    cfg = _llm_config()
    if not cfg:
        return None
    base_url, model, api_key = cfg

    ctx = {k: v for k, v in (context or {}).items()
           if k in ("org", "occupation", "location_city", "location_country",
                    "name_given", "name_family") and v}

    prompt = (
        "You verify identity matches for a personal contacts database.\n"
        "Contact: %s\nKnown context (may be empty): %s\n"
        "Web page URL: %s\nPage text (truncated):\n%s\n\n"
        "Is this page about that specific person (not a product, project, or a "
        "different person with a similar name)? Answer ONLY with JSON:\n"
        '{"same_person": true/false, "confidence": 0.0-1.0, "fields": {}}\n'
        "fields may contain only org, occupation, location_city, "
        "location_country, email, phone -- and only values the page explicitly "
        "states about this person. Omit anything not stated."
    ) % (name, _json.dumps(ctx) if ctx else "none", url, (content or "")[:4000])

    body = _json.dumps({
        "model": model,
        "max_tokens": 800,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log(f"  llm-gate: call failed ({str(e)[:60]}); deferring")
        return None

    if not text:
        log("  llm-gate: empty answer; deferring")
        return None

    try:
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        verdict = _json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:
        log("  llm-gate: unparseable verdict; treating page as no-match")
        return {}

    if not verdict.get("same_person") or float(verdict.get("confidence", 0)) < 0.7:
        return {}

    out = {}
    for k, v in (verdict.get("fields") or {}).items():
        if k in ("org", "occupation", "location_city", "location_country",
                 "email", "phone") and isinstance(v, str) and v.strip():
            out[k] = v.strip()
            out[k + "_source"] = url
    return out


def sift_extract_from_pages(name, search_results, max_pages=3, context=None):
    """
    Sift phase: turn search results into LLM-verified fields for one contact.

    search_results: list of {url, title, content} dicts (SearXNG results),
    exactly what overnight_enrichment.py passes. Flow per unique URL:
      1. skip noise domains; dedupe by normalized URL
      2. auth-walled -> judge on title+snippet only; else fetch via fetch_page
      3. cheap screen: family name absent -> drop without spending LLM tokens
      4. llm_verify_extract must confirm identity before ANY field is kept
    No LLM backend -> returns _sources only, with NO fields (fail closed).
    There is deliberately no regex-extraction fallback write path.
    """
    extracted = {}
    sources = []
    deferred = 0
    fetched_count = 0
    seen_urls = set()

    ok, reason = has_sufficient_anchors(
        {"name": name, **{k: (context or {}).get(k, "") for k in ("name_given", "name_family")}}
    )
    if not ok:
        log(f"  sift: refusing to enrich '{name}': {reason}")
        extracted["_sources"] = sources
        extracted["_skip_reason"] = reason
        return extracted

    ctx = context or {}
    context_org = ctx.get("org") or ""
    context_occ = ctx.get("occupation") or ""
    context_loc = ctx.get("location_city") or ""
    contact_email = ctx.get("email") or ""

    # Strongest anchor first: pivot on the contact's own email (handle reuse
    # + local-part search) before falling back to name-based search results.
    email_candidates = []
    if contact_email:
        try:
            email_candidates = discover_by_email(
                contact_email, subject_name=name,
                searxng_url=ctx.get("_searxng_url"),
            )
        except Exception as e:
            log(f"  sift: email pivot failed: {str(e)[:80]}")

    for result in (email_candidates + list(search_results or [])):
        url = result.get("url", "")
        norm = normalize_url(url)
        if not norm or norm in seen_urls:
            continue
        seen_urls.add(norm)

        if should_skip_domain(url):
            continue

        title = result.get("title", "")
        snippet = result.get("content", "")

        page_text = None
        method = "snippet"
        if not is_auth_walled(url) and fetched_count < max_pages:
            page_text, fetch_method = fetch_page(url)
            fetched_count += 1
            if page_text:
                method = fetch_method

        content_for_llm = page_text if page_text else f"{title}\n{snippet}"

        screen = verify_identity_from_page(
            content_for_llm, name, context_org, context_occ, context_loc
        )
        if screen["verdict"] == "reject":
            continue

        page_data = llm_verify_extract(name, url, content_for_llm, context=ctx)
        if page_data is None:
            deferred += 1
            continue
        if not page_data:
            continue

        sources.append({"url": url, "method": method})
        for k, v in page_data.items():
            if k not in extracted:
                extracted[k] = v

        core = ("org", "occupation", "location_city", "email")
        if all(f in extracted for f in core):
            break

    if deferred and not sources:
        log(f"  sift: LLM gate unavailable for {deferred} page(s); "
            f"writing nothing (fail closed)")

    extracted["_sources"] = sources
    return extracted


SCOUT_SCRIPTS_DIR = "/root/.hermes/profiles/indigo/skills/ocas-scout/scripts"
_IDENTITY_ORDER = {"high": 3, "med": 2, "low": 1, "none": 0, "error": 0}

# Single-valued predicates: a person has ~one current value, so a new differing
# value SUPERSEDES the old (old stays, stamped invalid). Everything else is
# multi-valued and accumulates — people can have several addresses, phones,
# emails, or social profiles, all valid at once.
SINGLE_VALUED_PREDICATES = {"org", "occupation", "pronouns"}

_BIO_BOILERPLATE = (
    "welcome to my scheduling page", "please follow the instructions",
    "this account is private", "page not found", "sign up", "log in",
    "create an account", "no bio yet", "just setting up my",
)


# A platform title announcing that someone has an account there. The name and handle
# vary per contact, so a fixed-phrase list cannot catch it; the SHAPE is the signal.
_BIO_PLATFORM_TITLE = re.compile(
    r"\bis on (snapchat|tiktok|instagram|facebook|twitter|threads|x)\b"
    r"|\bjoin (me|us) on\b"
    r"|\bwatch .{0,30}\bon (tiktok|youtube|snapchat)\b"
    r"|\b(profile|page) on \w+ *[.!]?$"
    r"|\bsee photos and videos\b",
    re.I)


def _is_boilerplate_bio(text, person_name="", handle=""):
    """True when the text carries nothing about the person.

    Beyond the fixed phrases, rejects a platform page title -- '<Name> is on
    Snapchat! (@<handle>)' -- which restates the name and handle already known and
    was being stored as a bio for 37 contacts.
    """
    t = (text or "").strip().lower()
    if len(t) < 12:
        return True
    if any(b in t for b in _BIO_BOILERPLATE):
        return True
    if _BIO_PLATFORM_TITLE.search(t):
        return True
    # Almost entirely the contact's own name and handle echoed back.
    stripped = t
    for tok in (person_name or "").lower().split() + [(handle or "").lower()]:
        if len(tok) > 2:
            stripped = stripped.replace(tok, " ")
    stripped = re.sub(r"[^a-z]+", "", stripped)
    return len(stripped) < 8


def _ensure_fact_validity_columns(weave):
    """Idempotent migration: add temporal-validity columns to an older facts
    table. Existing rows get NULL valid_until = currently valid."""
    cols = {r["name"] for r in weave.execute("PRAGMA table_info(facts)")}
    if "valid_until" not in cols:
        weave.execute_write("ALTER TABLE facts ADD COLUMN valid_until TEXT")
    if "superseded_by" not in cols:
        weave.execute_write("ALTER TABLE facts ADD COLUMN superseded_by TEXT")


# Source types that mean "a human put this here", as opposed to something scout
# inferred. A curated URL is treated by scout as the contact's own assertion about
# which account is theirs, so scout's own findings must never be fed back in: that
# would turn a guess into ground truth on the next pass.
_CURATED_URL_SOURCES = (
    "google_contacts", "contact_record", "imported", "linkedin_import",
    "user-stated", "user_stated",
)


def curated_urls_for_contact(contact_id, db_path=None):
    """Hand-entered profile URLs and website for a contact, newest first.

    Excludes anything scout produced. Returns [] on any error — a missing anchor
    list must degrade to the old behaviour, never break the run.
    """
    if not contact_id:
        return []
    try:
        from weave_sqlite import WeaveDB
        weave = WeaveDB(db_path) if db_path else WeaveDB()
        marks = ",".join("?" * len(_CURATED_URL_SOURCES))
        rows = weave.execute(
            "SELECT f.value AS value FROM facts f "
            "JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
            "WHERE e.source_id = ? AND f.valid_until IS NULL "
            "  AND (f.predicate LIKE 'profile_%' OR f.predicate = 'website') "
            "  AND f.source_type IN (" + marks + ") "
            "ORDER BY f.record_time DESC",
            (contact_id,) + _CURATED_URL_SOURCES)
        urls = [r["value"] for r in rows if r.get("value")]
        own = weave.execute("SELECT website FROM persons WHERE id = ?", (contact_id,))
        if own and own[0].get("website"):
            urls.append(own[0]["website"])
        seen, out = set(), []
        for u in urls:
            k = (u or "").strip().rstrip("/").lower()
            if k and k not in seen:
                seen.add(k)
                out.append(u.strip())
        return out
    except Exception as e:  # noqa: BLE001
        log(f"  scout: could not read curated urls ({str(e)[:60]})")
        return []

def scout_research_contact(contact, top_sites=300):
    """Run ocas-scout's person-OSINT for one contact and return the full result
    (the intended path; see ocas-scout/references/plans/contact-enrichment.plan.md).

    research_person anchors on the contact's distinctive email handle (+phone),
    runs maigret/holehe, and corroborates identity across independent sources —
    so a common name like 'Tomas Vega' is resolved by the handle, never the
    name alone. Returns the full result dict (identity / profiles / findings /
    enrichment / tools), or a minimal error result if scout is unavailable.
    """
    get = contact.get if isinstance(contact, dict) else (
        lambda k, d="": contact[k] if k in contact.keys() else d)
    if SCOUT_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCOUT_SCRIPTS_DIR)
    try:
        from research_person import research_person
    except Exception as e:  # noqa: BLE001
        log(f"  scout: research_person unavailable ({str(e)[:80]})")
        return {"identity": {"level": "error", "reason": str(e)[:120]},
                "profiles": [], "findings": [], "enrichment": {}, "tools": []}

    # Everything the contact record already knows. These were being dropped, so
    # the curated-URL path never ran in the nightly pipeline and contacts with no
    # email but a known employer, job title and personal site looked unsearchable.
    known_urls = curated_urls_for_contact(get("id", "") or "")
    if known_urls:
        log(f"  scout: {len(known_urls)} curated url(s) from the contact record")
    return research_person(
        get("name", "") or "",
        email=get("email", "") or "",
        employer=get("org", "") or "",
        phone=get("phone", "") or "",
        top_sites=top_sites,
        org=get("org", "") or "",
        occupation=get("occupation", "") or "",
        location_city=get("location_city", "") or "",
        known_urls=known_urls,
        name_given=get("name_given", "") or "",
        name_family=get("name_family", "") or "",
    )


def store_scout_findings(contact_id, res, person_name="", db_path=None,
                         min_identity="med"):
    """Store ALL of scout's corroborated data for a contact as graph facts.

    Weave is a graph store — once identity is corroborated across independent
    sources, every high-quality datum is worth keeping, not just a handful of
    scalar columns. Richer data compounds: a stored bio ("Assistant Professor
    at CSUMB") is what a future enrichment pass extracts org/occupation from,
    and a stored username lets the next run re-pivot directly.

    Writes, deduped by (predicate, value) already linked to the contact:
      - scalar enrichment: org / location_city / website / pronouns
      - one profile_<platform> = url per corroborated profile
      - each profile bio as bio_summary (the extraction feedstock)
      - each resolved handle as username
      - holehe account-existence as account_on
    Every fact carries source_ref (the profile URL) and a confidence tied to
    the identity level. Gated: nothing is written below `min_identity`.

    Returns (n_written, identity_level, written_predicates).
    """
    import uuid as _uuid

    idn = res.get("identity", {}) if isinstance(res, dict) else {}
    level = idn.get("level", "none")
    if _IDENTITY_ORDER.get(level, 0) < _IDENTITY_ORDER.get(min_identity, 2):
        return 0, level, []

    sys.path.insert(0, str(Path(__file__).parent))
    from weave_sqlite import WeaveDB
    weave = WeaveDB(db_path) if db_path else WeaveDB()
    _ensure_fact_validity_columns(weave)

    if not weave.execute("SELECT id FROM persons WHERE id = ?", (contact_id,)):
        return 0, level, []

    conf = 0.75 if level == "high" else 0.6
    rt = datetime.now(timezone.utc).isoformat()

    # All (predicate, value) already recorded (any validity) → multi-valued dedup.
    all_pairs = {(r["predicate"], r["value"]) for r in weave.execute(
        "SELECT f.predicate, f.value FROM facts f "
        "JOIN edges e ON e.target_id = f.id "
        "WHERE e.source_id = ? AND e.rel_type = 'HasFact'", (contact_id,))}
    # Currently-valid facts per predicate → single-valued supersession check.
    valid_by_pred = {}
    for r in weave.execute(
        "SELECT f.id, f.predicate, f.value FROM facts f "
        "JOIN edges e ON e.target_id = f.id "
        "WHERE e.source_id = ? AND e.rel_type = 'HasFact' "
        "AND f.valid_until IS NULL", (contact_id,)):
        valid_by_pred.setdefault(r["predicate"], []).append((r["id"], r["value"]))

    # Assemble candidates. Single-valued: keep the single highest-confidence
    # value (no within-run self-supersession). Multi-valued: keep all.
    single = {}       # predicate -> (value, source_ref, conf)
    multi = []        # (predicate, value, source_ref, conf)

    def _add(pred, val, src, c):
        val = (val or "").strip()
        if not val:
            return
        if pred in SINGLE_VALUED_PREDICATES:
            if pred not in single or c > single[pred][2]:
                single[pred] = (val, src, c)
        else:
            multi.append((pred, val, src, c))

    enr = res.get("enrichment", {}) or {}
    for k in ("org", "location_city", "website", "pronouns"):
        v = enr.get(k)
        if isinstance(v, str) and v.strip():
            if k == "org" and not validate_field("org", v, person_name):
                continue
            _add(k, v, enr.get(f"{k}_source", "scout_osint"), conf)

    for p in res.get("profiles", []):
        # Full-name agreement, or the contact's own curated URL. A surname-only
        # match may support the identity level but must not source facts: on a
        # common surname it is a namesake, and its bio, location and handle belong
        # to someone else.
        _full = (p.get("name_shared_tokens", 0) >= 2 and p.get("family_present"))
        if not (_full or p.get("curated")):
            continue
        url = (p.get("url") or "").strip()
        plat = (p.get("site") or "").strip().lower()
        if plat and url:
            _add(f"profile_{plat}", url, url, conf)
        if not _is_boilerplate_bio(p.get("bio", ""), person_name,
                                   p.get("handle", "")):
            _add("bio_summary", p["bio"].strip(), url or "scout_osint", conf)
        if p.get("location", "").strip():
            _add("location_city", p["location"].strip(), url, conf * 0.9)
        if p.get("handle", "").strip():
            _add("username", p["handle"].strip(), url or "scout_osint", conf)

    for f in res.get("findings", []):
        if f.get("finding_id") == "H001":
            for sr in f.get("source_refs", []):
                for site in (sr.get("quote", "") or "").split(","):
                    site = site.strip()
                    if site and "." in site and " " not in site:
                        _add("account_on", site, "holehe", 0.5)

    def _insert_fact(predicate, value, source_ref, c):
        c = min(1.0, max(0.0, float(c)))
        fid = str(_uuid.uuid4())
        weave.execute_write(
            "INSERT INTO facts (id, predicate, value, confidence, source_type, "
            "source_ref, record_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fid, predicate, value, c, "scout_osint", source_ref, rt))
        weave.execute_write(
            "INSERT INTO edges (id, source_id, target_id, rel_type, confidence, "
            "record_time) VALUES (?, ?, ?, ?, ?, ?)",
            (str(_uuid.uuid4()), contact_id, fid, "HasFact", c, rt))
        return fid

    written = []
    single_new = {}       # predicate -> new current value
    superseded_old = {}   # predicate -> [old values we just invalidated]

    # Multi-valued: accumulate, dedup by (predicate, value). Never supersede.
    for predicate, value, source_ref, c in multi:
        if (predicate, value) in all_pairs:
            continue
        all_pairs.add((predicate, value))
        _insert_fact(predicate, value, source_ref, c)
        written.append(predicate)

    # Single-valued: a new differing value supersedes the currently-valid one.
    for predicate, (value, source_ref, c) in single.items():
        current = valid_by_pred.get(predicate, [])
        if any(v == value for _, v in current):
            continue  # already the current valid value
        new_fid = _insert_fact(predicate, value, source_ref, c)
        for old_id, old_val in current:
            weave.execute_write(
                "UPDATE facts SET valid_until = ?, superseded_by = ? WHERE id = ?",
                (rt, new_fid, old_id))
            superseded_old.setdefault(predicate, []).append(old_val)
        single_new[predicate] = value
        written.append(predicate + (f"(superseded {len(current)})" if current else ""))

    # Enrich the ACTUAL contact node — the visible record, not just graph
    # metadata attached to it. Two rules, matching the temporal model:
    #   * multi-valued columns (location_city, website): fill only when EMPTY.
    #     A single column can't hold two addresses; the graph facts carry all.
    #   * single-valued columns (org, occupation, pronouns): fill when empty,
    #     AND update when the existing value is our own now-superseded data
    #     (so a job change is reflected) — but NEVER overwrite a value we did
    #     not source, which is presumed user-entered.
    # Only touch persons columns that actually exist — the base schema is
    # minimal; website/pronouns arrive via later migrations in production.
    person_columns = {r["name"] for r in weave.execute("PRAGMA table_info(persons)")}
    node_cols = tuple(c for c in ("org", "occupation", "location_city",
                                  "website", "pronouns") if c in person_columns)
    cur = weave.execute(
        f"SELECT {', '.join(node_cols)} FROM persons WHERE id = ?", (contact_id,)) \
        if node_cols else []
    cur = cur[0] if cur else {}
    set_parts, params, touched = [], [], []
    for col in node_cols:
        existing = (cur.get(col) or "").strip()
        if col in SINGLE_VALUED_PREDICATES:
            new_val = single_new.get(col)
            if not new_val:
                continue
            was_ours = existing in superseded_old.get(col, [])
            if not existing or was_ours:
                set_parts.append(f"{col} = ?")
                params.append(new_val)
                touched.append(col)
        else:
            new_val = (enr.get(col) or "").strip()
            if new_val and not existing:
                if col == "org" and not validate_field("org", new_val, person_name):
                    continue
                set_parts.append(f"{col} = ?")
                params.append(new_val)
                touched.append(col)
    if set_parts:
        params.append(contact_id)
        weave.execute_write(
            f"UPDATE persons SET {', '.join(set_parts)} WHERE id = ?", params)
        written.append("node:" + ",".join(touched))

    return len(written), level, written


WRITEABLE_PREDICATES = [
    "org", "occupation", "location_city", "location_country",
    "email", "phone", "website", "pronouns",
]


def enrich_weave_contact(contact_id, enrichment_data, confidence=0.7,
                         person_name="", db_path=None):
    """
    Write enrichment data back to Weave as Fact nodes with full provenance.
    Shared by quick_enrich and overnight_enrichment.
    Confidence can be per-field via {field}_confidence keys, or use default.
    db_path targets a specific DB (default: production) — pass a copy to test
    the write path without touching live data.

    social_profiles (a {platform: url} dict from scout) is flattened to one
    fact per platform (predicate 'profile_<platform>'). Returns True if any
    fields were written.
    """
    import uuid as _uuid

    sys.path.insert(0, str(Path(__file__).parent))
    from weave_sqlite import WeaveDB

    weave = WeaveDB(db_path) if db_path else WeaveDB()

    if not enrichment_data:
        return False

    written = 0
    rows = weave.execute("SELECT id FROM persons WHERE id = ?", (contact_id,))
    if not rows:
        return False

    record_time = datetime.now(timezone.utc).isoformat()

    # Flatten social_profiles dict -> individual profile_<platform> string facts.
    data = dict(enrichment_data)
    social = data.pop("social_profiles", None)
    data.pop("social_profiles_confidence", None)
    if isinstance(social, dict):
        for platform, url in social.items():
            if isinstance(url, str) and url.strip():
                pkey = f"profile_{platform.strip().lower()}"
                data[pkey] = url.strip()
                data[f"{pkey}_source"] = url.strip()

    for key, value in data.items():
        if key.startswith("_") or key.endswith("_source") or key.endswith("_confidence"):
            continue
        is_profile = key.startswith("profile_")
        if key not in WRITEABLE_PREDICATES and not is_profile:
            continue
        # profile_* and website/pronouns are already source-verified by scout's
        # corroboration; only run the heuristic validator on the free-text
        # fields it was written for.
        if key in ("org", "occupation", "location_city", "location_country",
                   "email", "phone") and not validate_field(key, value, person_name):
            continue
        if not isinstance(value, str) or not value.strip():
            continue

        fact_id = str(_uuid.uuid4())
        source_url = enrichment_data.get(f"{key}_source", "")
        ref = source_url or f"enrichment_{datetime.now(timezone.utc).strftime('%Y%m%d')}"

        field_confidence = enrichment_data.get(f"{key}_confidence", confidence)
        field_confidence = min(1.0, max(0.0, float(field_confidence)))

        weave.execute("""
            INSERT OR REPLACE INTO facts (id, predicate, value, confidence, source_type, source_ref, record_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fact_id, key, value, field_confidence, "web_enrichment", ref, record_time))

        edge_id = str(_uuid.uuid4())
        weave.execute_write("""
            INSERT OR IGNORE INTO edges (id, source_id, target_id, rel_type, confidence, record_time)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (edge_id, contact_id, fact_id, "HasFact", field_confidence, record_time))

        written += 1

    return written > 0
