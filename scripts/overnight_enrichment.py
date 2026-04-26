#!/usr/bin/env python3
"""
Overnight Weave Contact Enrichment Pipeline
Uses Scout for person research and Sift for web extraction.

This script should use:
- Scout (ocas-scout) for person-focused OSINT research with identity resolution
- Sift (ocas-sift) for web search and structured extraction
- Proper confidence scoring and provenance tracking

Currently uses direct SearXNG search as a fallback when Scout/Sift are unavailable.
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
SYNC_EVERY = 30         # Sync to Google after this many enriched contacts
DEADLINE_HOUR_ET = 8    # Stop at 8am ET
SEARXNG_URL = "http://localhost:8888/search"
PROGRESS_FILE = "<hermes-root>/data/weave-enrichment/progress.jsonl"
STATS_FILE = "<hermes-root>/data/weave-enrichment/stats.json"

# Minimum confidence threshold for writing data
MIN_CONFIDENCE = 0.7

def log(msg):
    ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")
    print(f"[{ts}] {msg}", flush=True)

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
            "ts": datetime.now(timezone.utc).isoformat()
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
        "total_processed_all_time": total_processed
    }
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)

def searxng_search(query, limit=3):
    """Search via local SearXNG."""
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

def get_contacts_needing_enrichment():
    """Query Weave for contacts with gaps, excluding user-provided data."""
    import real_ladybug as lb
    db = lb.Database("<hermes-root>/commons/db/ocas-weave/weave.lbug", read_only=True)
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

def extract_info_from_search(name, search_results):
    """Extract enrichment data from search results.
    
    Uses improved pattern matching to handle more formats.
    """
    results = {}
    
    # Normalize name for comparison
    name_lower = name.lower()
    name_parts = name_lower.split()
    
    for item in search_results:
        text = f"{item.get('title', '')} {item.get('content', '')}"
        url = item.get("url", "")
        
        # Skip if this result doesn't seem to be about the right person
        # Check if at least part of the name appears in the result
        text_lower = text.lower()
        if not any(part in text_lower for part in name_parts if len(part) > 3):
            continue
        
        # Extract org from LinkedIn company URLs
        if not results.get("org"):
            if "linkedin.com/company/" in url:
                parts = url.split("linkedin.com/company/")
                if len(parts) > 1:
                    company = parts[1].split("/")[0].split("?")[0].replace("-", " ").title()
                    if len(company) > 2 and company.lower() not in ["in", "search", "jobs", "dir"]:
                        results["org"] = company
        
        # Extract occupation and org from text patterns - improved
        if not results.get("occupation"):
            title_patterns = [
                # "Title at Company" format
                r'([A-Z][a-zA-Z\s&/]+?(?:Manager|Director|Engineer|Designer|Developer|Analyst|Lead|Head|VP|President|CEO|CTO|CFO|COO|Founder|Consultant|Specialist|Coordinator|Administrator|Architect|Scientist|Researcher))\s+(?:at|@|of|for|-)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
                # "works as/is a Title at Company" format  
                r'(?:works?\s+(?:as|at)|is\s+(?:a|an|the)|serves?\s+as|was\s+(?:a|an))\s+([A-Z][a-zA-Z\s&/]+?)\s+(?:at|@|of|for)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
                # Generic "Title at Company" 
                r'([A-Z][a-zA-Z\s&/]+?)\s+(?:at|@)\s+([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
                # "Title - Company" format
                r'([A-Z][a-zA-Z\s&/]+?)\s*[-–]\s*([A-Z][a-zA-Z\s&.,]+?)(?:\.|,|\s|$)',
            ]
            for pattern in title_patterns:
                match = re.search(pattern, text)
                if match and match.lastindex and match.lastindex >= 2:
                    title = match.group(1).strip()
                    company = match.group(2).strip()
                    # Validate title
                    if (len(title) < 60 and len(title) > 3 
                        and title.lower() not in ["linkedin", "view", "profile", "search", "the", "this", "that"]
                        and "http" not in title.lower()
                        and not title.startswith("http")):
                        # Validate company
                        if (len(company) < 80 and len(company) > 1
                            and company.lower() not in ["linkedin", "view", "profile", "search"]):
                            results["occupation"] = title
                            if not results.get("org"):
                                results["org"] = company
                            break
        
        # Extract location - improved patterns
        if not results.get("location_city"):
            loc_patterns = [
                # "Location: City, State" format
                r'(?:Location|Based|Located|Lives?|Area|City):\s*([A-Z][a-zA-Z\s]+,\s*[A-Z]{2}(?:\s*[A-Z]{2})?)',
                # "in City, State" format
                r'(?:in|based in|located in|from)\s+([A-Z][a-zA-Z\s]+,\s*[A-Z]{2}(?:\s*[A-Z]{2})?)',
                # "City, State" at start of line or after punctuation
                r'(?:^|[.\s,])\s*([A-Z][a-zA-Z\s]+,\s*[A-Z]{2}(?:\s*[A-Z]{2})?)\s*(?:$|[.\s,])',
                # "City, Country" format
                r'(?:in|based in|located in)\s+([A-Z][a-zA-Z\s]+,\s*[A-Z][a-zA-Z]+)',
            ]
            static_cities = [
                "San Francisco", "New York", "Los Angeles", "Seattle", "Chicago",
                "Austin", "Boston", "Denver", "Miami", "Portland", "Washington DC",
                "Bay Area", "Silicon Valley", "London", "Tokyo", "Singapore",
                "Toronto", "Vancouver", "Sydney", "Berlin", "Paris", "San Antonio",
                "Dallas", "Houston", "Phoenix", "Philadelphia", "San Diego",
            ]
            for pattern in loc_patterns:
                match = re.search(pattern, text)
                if match:
                    loc = match.group(1).strip()
                    if len(loc) > 3 and len(loc) < 60:
                        results["location_city"] = loc
                        break
            # Static city check
            if not results.get("location_city"):
                for city in static_cities:
                    if city in text:
                        results["location_city"] = city
                        break
        
        # Extract email from text
        if not results.get("email"):
            email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
            if email_match:
                email = email_match.group(0)
                # Filter out generic/support emails
                if not any(x in email.lower() for x in ["noreply", "no-reply", "support@", "info@", "admin@", "example.com", "test@"]):
                    results["email"] = email
    
    return results

def validate_enrichment_field(key, value, person_name=""):
    """Validate enrichment data before writing. Returns True if field looks valid."""
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    if len(value) < 2:
        return False
    
    person_name_lower = (person_name or "").lower().strip()
    
    if key == "occupation":
        # Occupation should NOT be the person's own name
        if person_name_lower and value.lower() == person_name_lower:
            return False
        if person_name_lower and value.lower() == person_name_lower.split(",")[0].strip():
            return False
        # Occupation should be a job title, not a description
        too_long_or_weird = len(value) > 60 or len(value) < 3
        # Filter common garbage patterns
        garbage_patterns = [
            "log in", "sign in", "sign up", "main review", "salt lake",
            "select ot", "with the no.", "pick in the", "days ago",  # sports headlines
            "linkedin view", "profile", "view more", "see more",      # UI scraping artifacts
            "anniversary", "years", "welcome",                       # social noise
        ]
        if any(g in value.lower() for g in garbage_patterns):
            return False
        if too_long_or_weird:
            return False
        # First word should not be a conjunction or preposition
        first_word = value.split()[0].lower() if value.split() else ""
        if first_word in ["is", "are", "was", "were", "the", "a", "an", "its", "it's", "been"]:
            return False
        # Must contain at least some title-like words
        title_indicators = ["manager", "director", "engineer", "lead", "head", "vp", "senior", 
                          "junior", "staff", "principal", "analyst", "designer", "developer",
                          "architect", "founder", "ceo", "cto", "cfo", "president", "assistant",
                          "coordinator", "specialist", "consultant", "instructor", "associate",
                          "officer", "representative", "therapist", "producer", "editor",
                          "writer", "owner", "partner"]
        if not any(t in value.lower() for t in title_indicators):
            # Allow uppercase-title-like patterns (e.g. "Vice President", "SVP Engineering")
            words = value.split()
            if not all(w[0].isupper() for w in words if len(w) > 2):
                return False
        return True
    
    elif key == "org":
        # Organization should NOT be a generic word
        if value.lower() in ["the", "design", "visitor", "guest", "university", "college"]:
            return False
        if person_name_lower and value.lower() == person_name_lower:
            return False
        if len(value) > 80:
            return False
        # Organization should not be an occupation
        occupation_indicators = ["engineer", "manager", "director", "president", "vp",
                                 "head", "lead", "architect", "developer", "ceo", "cto",
                                 "cfo", "principal", "analyst", "specialist"]
        if value.lower() in occupation_indicators:
            return False
        return True
    
    elif key == "location_city":
        if len(value) > 80:
            return False
        # Filter obviously non-city text (all caps business descriptions)
        if value.isupper() and len(value) > 30:
            return False
        return True
    
    return True


def enrich_weave_contact(contact_id, enrichment_data, confidence=0.7, person_name=""):
    """Write enrichment data back to Weave FACTS with provenance.
    Each piece of enrichment data is stored as a Fact node linked to the Person.
    DO NOT overwrite Person fields directly — use Fact nodes for provenance.
    """
    import uuid
    import real_ladybug as lb
    
    if not enrichment_data:
        return False
    
    db = lb.Database("<hermes-root>/commons/db/ocas-weave/weave.lbug", read_only=False)
    conn = lb.Connection(db)
    
    written = 0
    try:
        # Verify person exists first
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
            
            fact_id = str(uuid.uuid4())
            # Create Fact node with full provenance
            cypher = (
                "MATCH (p:Person {id: $person_id}) "
                "CREATE (f:Fact {id: $fact_id, predicate: $key, value: $value, "
                "source_type: 'web_enrichment', source_ref: $source_ref, "
                "confidence: $confidence, record_time: $record_time}) "
                "CREATE (p)-[:HasFact {fact_key: $key}]->(f) "
                "RETURN f.id"
            )
            params = {
                "person_id": contact_id,
                "fact_id": fact_id,
                "key": key,
                "value": value,
                "source_ref": "searxng_enrichment_" + datetime.now(timezone.utc).strftime("%Y%m%d"),
                "confidence": confidence,
                "record_time": record_time,
            }
            r = conn.execute(cypher, params)
            if r.get_all():
                written += 1
            else:
                log(f"  ✗ Fact write failed for {key}")
        
        conn.close()
        return written > 0
        
    except Exception as e:
        conn.close()
        raise e

def sync_to_google():
    """Run Google Contacts sync."""
    log("Syncing to Google Contacts...")
    try:
        result = subprocess.run(
            ["python3", "<hermes-root>/skills/ocas-weave/scripts/google_sync.py"],
            capture_output=True, text=True, timeout=300
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
    # If it's already past 8am today, deadline is 8am tomorrow
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

def main():
    session_start = datetime.now(timezone.utc)
    
    log("=" * 60)
    log("OVERNIGHT WEAVE ENRICHMENT STARTING")
    log(f"Hours until 8am ET deadline: {hours_until_deadline():.1f}")
    log("=" * 60)
    
    # Load progress from previous runs
    processed_ids = load_progress()
    log(f"Previously processed: {len(processed_ids)} contacts")
    
    # Load detailed progress to identify which were actually enriched vs skipped
    enriched_ids = set()
    no_data_ids = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    cid = rec.get("id", "")
                    if cid and rec.get("fields"):
                        enriched_ids.add(cid)
                    elif cid:
                        no_data_ids.add(cid)
                except:
                    pass
    
    # Get contacts needing enrichment
    contacts = get_contacts_needing_enrichment()
    log(f"Total contacts needing enrichment: {len(contacts)}")
    
    # NOTE: Do NOT filter by progress file — the enrichment only fills NULL/empty fields,
    # so re-processing is harmless. The progress file is for logging/monitoring only.
    # Filtering by progress caused a bug where contacts with partial enrichment
    # (e.g. location_city found, but org/occupation still missing) were permanently excluded.
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
        # Check deadline
        if is_past_deadline():
            log(f"⏰ Deadline reached (8am ET). Stopping.")
            break
        
        name = contact["name"]
        contact_id = contact["id"]
        gaps = contact["gaps"]
        
        remaining = len(to_process) - i
        log(f"[{i+1}/{len(to_process)}] {name} | gaps: {', '.join(gaps)} | {remaining} remaining")
        
        try:
            # Build search queries
            queries = []
            name_given = contact.get("name_given", "")
            name_family = contact.get("name_family", "")
            existing_org = contact.get("org", "")
            existing_loc = contact.get("location_city", "")
            
            if name_family:
                queries.append(f'"{name_given} {name_family}" LinkedIn')
            if existing_org:
                queries.append(f'"{name}" {existing_org}')
            else:
                queries.append(f'"{name}" professional career')
            
            # Run searches
            all_results = []
            for q in queries[:2]:
                try:
                    results = searxng_search(q, limit=3)
                    all_results.extend(results)
                    time.sleep(SEARCH_DELAY)
                except Exception as e:
                    log(f"  Search error: {e}")
            
            if not all_results:
                log(f"  No search results, skipping")
                skipped_count += 1
                save_progress(contact_id, name, [], error="no_search_results")
                continue
            
            # Extract enrichment data
            enrichment = extract_info_from_search(name, all_results)
            
            if not enrichment:
                log(f"  No extractable data found")
                skipped_count += 1
                save_progress(contact_id, name, [], error="no_extractable_data")
                continue
            
            # Write to Weave with confidence scoring
            success = enrich_weave_contact(contact_id, enrichment, confidence=MIN_CONFIDENCE, person_name=name)
            
            if success:
                # Only report fields that passed validation (were actually written)
                written_fields = [k for k in enrichment.keys()
                                 if validate_enrichment_field(k, enrichment[k], name)]
                fields_found = written_fields
                log(f"  ✓ Enriched: {fields_found} → { {k: enrichment[k] for k in written_fields} }")
                enriched_count += 1
                save_progress(contact_id, name, fields_found)
                
                # Periodic Google sync
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
        
        # Brief pause between contacts
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
    log(f"  Remaining contacts with gaps: {len(contacts) - total_processed}")
    log("=" * 60)

if __name__ == "__main__":
    main()