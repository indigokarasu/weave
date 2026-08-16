#!/usr/bin/env python3
"""
Quick Enrich — Real-time OSINT background research for new contacts.

Usage:
  python3 quick_enrich.py "Full Name" [--org "Company"] [--location "City"]

Scout → Sift → Sherlock → Write pipeline.
Shared extraction/search/validation logic lives in weave_enrich.py.
"""
import sys
import time
import uuid
import re
import urllib.parse
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import os

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
SQLITE_DB = AGENT_ROOT / "commons/db/ocas-weave/weave.sqlite"

# Shared enrichment logic
sys.path.insert(0, str(Path(__file__).parent))
from weave_enrich import (
    searxng_search, fetch_page, extract_from_content, llm_verify_extract,
    validate_field, is_auth_walled, should_skip_domain,
    build_scout_queries, SEARXNG_URL, JINA_BASE,
    log, sift_extract_from_pages, enrich_weave_contact,
)


def open_db():
    from weave_sqlite import WeaveDB
    return WeaveDB(SQLITE_DB)


def run_sherlock(handles):
    """Username/handle expansion via sherlock CLI."""
    results = []
    for handle in handles[:3]:
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


def quick_enrich(name, org=None, location=None):
    """Run the full Scout → Sift → Sherlock → Write pipeline."""
    print(f"\n{'='*60}")
    print(f"QUICK ENRICH: {name}")
    if org:
        print(f"  Org: {org}")
    if location:
        print(f"  Location: {location}")
    print(f"{'='*60}")

    weave = open_db()

    # ── SCOUT PHASE ──
    print(f"\n[1/4] SCOUT — SearXNG identity-resolved research")
    parts = name.split()
    queries = build_scout_queries(
        name,
        name_given=parts[0] if parts else "",
        name_family=parts[-1] if len(parts) > 1 else "",
        org=org or "",
        location_city=location or ""
    )
    queries = queries[:5]  # Allow up to 5 queries now

    all_results = []
    for q in queries:
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
    seen_domains = set()

    # Build context for the LLM gate
    gate_context = {
        "name_given": parts[0] if parts else "",
        "name_family": parts[-1] if len(parts) > 1 else "",
    }
    if org:
        gate_context["org"] = org
    if location:
        gate_context["location_city"] = location

    for result in all_results:
        url = result.get("url", "")
        if not url or is_auth_walled(url):
            continue
        domain = urllib.parse.urlparse(url).netloc
        if domain in seen_domains or fetched >= 3:
            continue
        seen_domains.add(domain)
        content, method = fetch_page(url)
        fetched += 1
        if not content:
            continue
        log(f"  Fetched {domain} via {method} ({len(content)} chars)")
        # Pass context to gate so it can validate against known fields
        extracted = llm_verify_extract(name, url, content, context=gate_context)
        if extracted is None:
            log(f"  no LLM reachable for {domain}; trying regex fallback")
            # Fallback to regex extraction if no LLM (lower confidence)
            extracted = extract_from_content(name, content, url)
            if extracted:
                for k in extracted:
                    if k.endswith("_confidence"):
                        extracted[k] = max(0.3, extracted[k] - 0.2)  # Mark as unverified
        if not extracted:
            log(f"  no data extracted from {domain}")
            continue
        # Merge, preferring higher confidence
        for k, v in extracted.items():
            if k.endswith("_confidence"):
                if k not in merged or v > merged.get(k, 0):
                    merged[k] = v
            elif k not in merged:
                merged[k] = v
        # Extract handles from content
        for pattern in [rf'@(\w{{3,20}})\b', r'github\.com/(\w+)', r'twitter\.com/(\w+)', r'x\.com/(\w+)']:
            for m in re.finditer(pattern, content):
                h = m.group(1)
                if h.lower() not in ["www", "com", "org", "http", "https", "linkedin", "twitter", "facebook", "instagram"]:
                    handles.add(h)

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
    existing = weave.execute("SELECT id, name FROM persons WHERE name = :name", {"name": name})
    if existing:
        contact_id = existing[0]["id"]
        log(f"  Found existing contact: {existing[0]['name']} ({contact_id})")
    else:
        contact_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        weave.execute("""
            INSERT INTO persons (id, name, name_given, name_family, source_type, source_ref, confidence, record_time)
            VALUES (:id, :name, :given, :family, 'imported', 'quick_enrich', 0.7, :rt)
        """, {
            "id": contact_id, "name": name,
            "given": parts[0] if parts else "",
            "family": parts[-1] if len(parts) > 1 else "",
            "rt": now,
        })
        log(f"  Created new contact: {name} ({contact_id})")

    written = enrich_weave_contact(contact_id, merged, confidence=0.7, person_name=name)
    print(f"\n  Written: {'yes' if written else 'no'} fields")

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS FOR: {name}")
    print(f"{'='*60}")
    for key in ["occupation", "org", "location_city", "email", "phone"]:
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