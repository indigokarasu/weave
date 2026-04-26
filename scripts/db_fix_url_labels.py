#!/usr/bin/env python3
"""Fix Google Contacts URL labels to use proper site names instead of generic 'url'."""

import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError

TOKEN_PATH = "/root/.hermes/jared_google_token.json"
BASE = "https://people.googleapis.com/v1"

# Known social site → canonical label
SITE_LABELS = {
    "linkedin.com": "LinkedIn",
    "instagram.com": "Instagram",
    "flickr.com": "Flickr",
    "twitter.com": "Twitter",
    "x.com": "Twitter",
    "facebook.com": "Facebook",
    "github.com": "GitHub",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "tiktok.com": "TikTok",
    "vimeo.com": "Vimeo",
    "behance.net": "Behance",
    "dribbble.com": "Dribbble",
    "medium.com": "Medium",
    "pinterest.com": "Pinterest",
    "threads.net": "Threads",
    "mastodon.social": "Mastodon",
    "bsky.app": "Bluesky",
    "notion.so": "Notion",
    "notion.site": "Notion",
    "substack.com": "Substack",
    "spotify.com": "Spotify",
    "soundcloud.com": "SoundCloud",
    "twitch.tv": "Twitch",
    "reddit.com": "Reddit",
    "stackoverflow.com": "Stack Overflow",
    "calendly.com": "Calendly",
    "linktr.ee": "Linktree",
    "bio.link": "Bio Link",
    "about.me": "About.me",
    "angel.co": "AngelList",
    "wellfound.com": "AngelList",
    "crunchbase.com": "Crunchbase",
    "keybase.io": "KeyBase",
    "tumblr.com": "Tumblr",
    "wordpress.com": "WordPress",
    "blogger.com": "Blogger",
    "wix.com": "Wix",
    "squarespace.com": "Squarespace",
}


def get_access_token():
    with open(TOKEN_PATH) as f:
        td = json.load(f)
    expiry = td.get("expiry")
    if expiry:
        if isinstance(expiry, str):
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        else:
            exp = datetime.fromtimestamp(expiry, tz=timezone.utc)
        if exp < datetime.now(timezone.utc) + timedelta(minutes=5):
            resp = urllib.request.urlopen(urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=urllib.parse.urlencode({
                    "client_id": td["client_id"],
                    "client_secret": td["client_secret"],
                    "refresh_token": td["refresh_token"],
                    "grant_type": "refresh_token",
                }).encode()))
            new = json.loads(resp.read())
            td["token"] = new["access_token"]
            td["expiry"] = (datetime.now(timezone.utc) +
                            timedelta(seconds=new["expires_in"])).isoformat()
            with open(TOKEN_PATH, "w") as f:
                json.dump(td, f, indent=2)
    return td["token"]


def api_get(path, token, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read())


def api_patch(path, token, body, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    return json.loads(urllib.request.urlopen(req).read())


def extract_domain(url_str):
    """Extract domain from URL string."""
    url_str = url_str.strip()
    if not url_str.startswith(("http://", "https://")):
        url_str = "https://" + url_str
    try:
        parsed = urllib.parse.urlparse(url_str)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return None


def classify_url(url_value, contact_name, contact_org):
    """Determine the correct label for a URL."""
    domain = extract_domain(url_value)
    if not domain:
        return None

    # Check known social sites
    if domain in SITE_LABELS:
        return SITE_LABELS[domain]

    # Check subdomains of known sites (e.g., subdomain.linkedin.com)
    for site_domain, label in SITE_LABELS.items():
        if domain.endswith("." + site_domain):
            return label

    # Check if URL matches company
    if contact_org:
        org_lower = contact_org.lower().replace(" ", "")
        org_domain = org_lower + ".com"
        if domain == org_domain or domain.startswith(org_lower + "."):
            return "Company"

    # Check if URL contains their name/handle
    if contact_name:
        name_slug = re.sub(r"[^a-z0-9]", "", contact_name.lower())
        url_slug = re.sub(r"[^a-z0-9]", "", domain + url_value.lower())
        if name_slug and len(name_slug) > 3 and name_slug in url_slug:
            return "Homepage"

    return "Website"


def main():
    token = get_access_token()

    # Fetch all contacts with URLs
    print("Fetching contacts...")
    contacts = []
    page_token = None
    while True:
        params = {
            "personFields": "names,urls,organizations",
            "pageSize": 100,
            "sources": "READ_SOURCE_TYPE_CONTACT",
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = api_get("/people/me/connections", token, params)
        except HTTPError as e:
            print(f"Error fetching contacts: {e}")
            break
        contacts.extend(resp.get("connections", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        print(f"  fetched {len(contacts)} so far...")

    print(f"Total contacts: {len(contacts)}")

    # Find contacts with mislabeled URLs
    fixes = []
    for contact in contacts:
        urls = contact.get("urls", [])
        if not urls:
            continue

        resource_name = contact["resourceName"]
        etag = contact["etag"]

        # Get contact name and org
        names = contact.get("names", [])
        display_name = names[0].get("displayName", "") if names else ""
        orgs = contact.get("organizations", [])
        org_name = orgs[0].get("name", "") if orgs else ""

        updates = []
        current_labels = []

        for i, url_entry in enumerate(urls):
            current_type = url_entry.get("type", "")
            url_value = url_entry.get("value", "")

            correct_label = classify_url(url_value, display_name, org_name)
            if correct_label and correct_label != current_type:
                updates.append({"index": i, "old": current_type, "new": correct_label, "url": url_value})
            current_labels.append(f"{url_value} → {current_type}")

        if updates:
            fixes.append({
                "name": display_name,
                "resource_name": resource_name,
                "etag": etag,
                "updates": updates,
                "current_urls": current_labels,
            })

    print(f"\nContacts with URL labels to fix: {len(fixes)}")
    for f in fixes:
        print(f"\n  {f['name']}:")
        for u in f["updates"]:
            print(f"    '{u['old']}' → '{u['new']}'  ({u['url']})")

    # Apply fixes
    CKPT_PATH = "/root/.hermes/data/url-label-fix/ckpt.txt"
    os.makedirs(os.path.dirname(CKPT_PATH), exist_ok=True)
    done = set()
    if os.path.exists(CKPT_PATH):
        with open(CKPT_PATH) as f:
            done = set(line.strip() for line in f if line.strip())

    if fixes:
        remaining = [f for f in fixes if f['resource_name'] not in done]
        print(f"\nApplying {len(remaining)} fixes ({len(done)} already done)...")
        patched = 0
        failed = 0
        for f in remaining:
            # Re-fetch contact for fresh etag
            try:
                fresh = api_get(
                    f"/{f['resource_name']}",
                    token,
                    {"personFields": "urls", "sources": "READ_SOURCE_TYPE_CONTACT"},
                )
                current_etag = fresh["etag"]
                current_urls = fresh.get("urls", [])

                # Apply type updates
                for u in f["updates"]:
                    if u["index"] < len(current_urls):
                        current_urls[u["index"]]["type"] = u["new"]

                # Build update payload
                body = {
                    "etag": current_etag,
                    "urls": current_urls,
                }

                api_patch(
                    f"/{f['resource_name']}:updateContact",
                    token,
                    body,
                    {"updatePersonFields": "urls"},
                )
                patched += 1
                print(f"  ✓ {f['name']}")
                with open(CKPT_PATH, "a") as ckpt:
                    ckpt.write(f['resource_name'] + "\n")
                time.sleep(1.5)  # Stay under 90 req/min rate limit
            except HTTPError as e:
                failed += 1
                error_body = e.read().decode() if hasattr(e, "read") else str(e)
                print(f"  ✗ {f['name']}: {e.code} {error_body[:200]}")
                if e.code == 429:
                    print("  Rate limited, waiting 15s...")
                    time.sleep(15)
                elif e.code == 401:
                    print("  Token expired, refreshing...")
                    token = get_access_token()
                time.sleep(1)

        print(f"\nDone: {patched} patched, {failed} failed")
    else:
        print("\nNo fixes needed!")


if __name__ == "__main__":
    main()
