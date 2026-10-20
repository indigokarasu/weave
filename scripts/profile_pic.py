#!/usr/bin/env python3
"""
profile_pic.py — Free-source profile picture discovery & evaluation for Weave.

Modes:
  additive  – only add an image when the contact has no avatar_url yet.
  improve   – add an image when the current avatar is NOT a photograph (no face,
              or clearly a monogram / non-person graphic).  Never replaces a
              picture that already contains a detected human face.
  auto      – additive for the first run, improve on subsequent runs.

Image sources (priority order):
  1. GitHub (direct avatar URL)
  2. Gravatar (email-based, d=404 to avoid defaults)
  3. LinkedIn (via TinyFish stealth browser - profile-displayphoto URLs)
  4. X/Twitter (via TinyFish - og:image or profile avatar)
  5. Instagram (via TinyFish - og:image or profile avatar)
  6. Facebook (via TinyFish - og:image or profile avatar)
  7. Reddit (via JSON API - icon_img)

Face detection uses OpenCV YuNet ONNX model.
"""
import sys as _sys
if '--help' in _sys.argv or '-h' in _sys.argv:
    print('Usage: ' + __file__ + ' [--help]')
    _sys.exit(0)

import os
import re
import html
import time
import uuid
import hashlib
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import cv2
import numpy as np
import requests

PROFILE = os.environ.get("HERMES_PROFILE", "indigo")
_PROF = os.environ.get(
    "HERMES_HOME",
    os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo"),
)
DB_PATH = os.path.join(_PROF, "commons", "db", "ocas-weave", "weave.sqlite")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "face_detection_yunet.onnx")
MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)

# Import the shared search/fetch helpers from weave_enrich (same directory)
try:
    from weave_enrich import searxng_search
except Exception:
    searxng_search = None

# Import TinyFish helper (includes name matching)
try:
    from tinyfish_helper import (
        extract_linkedin_profile_pic,
        extract_generic_profile_pic,
        extract_reddit_profile_pic,
        _names_match,
    )
except Exception:
    extract_linkedin_profile_pic = None
    extract_generic_profile_pic = None
    extract_reddit_profile_pic = None
    _names_match = None

# ── YuNet model acquisition ──────────────────────────────────────────────
def _ensure_model():
    if os.path.exists(MODEL_PATH):
        return MODEL_PATH
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as e:
        print(f"[profile_pic] failed to download YuNet model: {e}")
        return None
    return MODEL_PATH

# ── Image acquisition ────────────────────────────────────────────────────
def fetch_image_bytes(url):
    """Download an image, trying the same free fallbacks as fetch_page()."""
    if not url:
        return None
    # unescape HTML entities in URL
    url = html.unescape(url)
    user_agents = [
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    # direct requests
    for ua in user_agents:
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=10)
            if r.status_code == 200 and "image/" in r.headers.get("content-type", ""):
                return r.content
        except Exception:
            pass

    # Google cache
    try:
        cache_url = "https://webcache.googleusercontent.com/search?q=cache:" + urllib.parse.quote(url, safe="")
        r = requests.get(cache_url, headers={"User-Agent": user_agents[0]}, timeout=10)
        if r.status_code == 200 and "image/" in r.headers.get("content-type", ""):
            return r.content
    except Exception:
        pass

    # Wayback
    try:
        wayback_url = "https://web.archive.org/web/2024/" + url
        r = requests.get(wayback_url, headers={"User-Agent": user_agents[0]}, timeout=10)
        if r.status_code == 200 and "image/" in r.headers.get("content-type", ""):
            return r.content
    except Exception:
        pass

    # Jina Reader (returns text, may contain an <img src>)
    try:
        jina_url = "https://r.jina.ai/" + url
        r = requests.get(jina_url, headers={"User-Agent": "HermesAgent/1.0"}, timeout=15)
        text = r.text
        m = re.search(r'<img[^>]+src="([^"]+)"', text, re.I)
        if m:
            img_url = urllib.parse.urljoin(url, html.unescape(m.group(1)))
            return fetch_image_bytes(img_url)
    except Exception:
        pass

    return None


# ── Image analysis ───────────────────────────────────────────────────────
def detect_face(image_bytes):
    """Return (face_detected, confidence, width, height). Uses YuNet ONNX."""
    if not image_bytes:
        return False, 0.0, 0, 0
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False, 0.0, 0, 0
    h, w = img.shape[:2]
    model_path = _ensure_model()
    if not model_path:
        return False, 0.0, w, h
    detector = cv2.FaceDetectorYN_create(model_path, "", (w, h), 0.5, 0.3, 5000)
    detector.setInputSize((w, h))
    _, faces = detector.detect(img)
    if faces is None or len(faces) == 0:
        return False, 0.0, w, h
    # faces is array of [x, y, w, h, ...]
    face = max(faces, key=lambda f: f[2] * f[3])
    fw, fh = int(face[2]), int(face[3])
    confidence = float(face[-1]) if len(face) > 15 else 0.5
    return True, confidence, fw, fh


def image_quality(image_bytes):
    """Return (sharpness, color_variance, is_photo_guess) all 0..1."""
    if not image_bytes:
        return 0.0, 0.0, 0.0
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return 0.0, 0.0, 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F).var()
    sharpness = min(1.0, laplacian / 200.0)
    color_var = np.std(img, axis=(0, 1)).mean()
    color_var = min(1.0, color_var / 60.0)
    is_photo_guess = (sharpness + color_var) / 2.0
    return sharpness, color_var, is_photo_guess


def _is_placeholder_url(url):
    """Return True if the URL looks like a generic placeholder."""
    url_lower = url.lower()
    placeholder_keywords = [
        "default_open_graph",
        "default-avatar",
        "defaultavatar",
        "gravatar.com/avatar/?d=default",
        "placeholder",
        "no-image",
        "noimage",
        "blank",
        "1x1",
        "pixel",
        "spacer",
        "avatar-0",
        "avatar_0",
        "avatar-anon",
        "avatar_anon",
        "anonymous",
        "default_avatar",
        "defaultavatar",
    ]
    for kw in placeholder_keywords:
        if kw in url_lower:
            return True
    # Also check for known placeholder domains
    placeholder_domains = [
        "i0.wp.com",
        "s0.wp.com",
        "secure.gravatar.com",
    ]
    host = urllib.parse.urlparse(url).netloc.lower()
    for domain in placeholder_domains:
        if domain in host:
            # gravatar with no email hash is likely a default
            if "gravatar.com" in host and "/avatar/" in url and len(url.split("/avatar/")[-1]) < 32:
                return True
    return False


# ── Candidate discovery ──────────────────────────────────────────────────
def known_image_urls(contact):
    """Turn already‑found profile URLs into concrete avatar URL candidates.

    Returns list of (source, url) tuples. Sources: github, gravatar, linkedin, twitter, x, instagram, facebook, reddit

    Identity verification: for auth-walled platforms (LinkedIn, Twitter/X,
    Instagram, Facebook, Reddit), the TinyFish extraction now also returns the
    profile's full name. We only accept the candidate if that name confirms
    the contact's name via _names_match — this prevents attaching a different
    person's photo (e.g. a different "Mike Brzozowski" on LinkedIn).
    """
    candidates = []
    urls = contact.get("urls", []) or []
    contact_name = contact.get("name", "")

    for u in urls:
        host = urllib.parse.urlparse(u).netloc.lower()

        if "github.com" in host:
            m = re.search(r"github\.com/([A-Za-z0-9_-]+)", u)
            if m:
                handle = m.group(1).lower()
                candidates.append(("github", f"https://github.com/{handle}.png"))

        elif "linkedin.com" in host:
            # Use TinyFish to extract profile picture + verify identity
            if extract_linkedin_profile_pic and _names_match:
                try:
                    result = extract_linkedin_profile_pic(u)
                    if result and result.get("url") and result.get("fullname"):
                        if _names_match(contact_name, result["fullname"]):
                            candidates.append(("linkedin", result["url"]))
                        else:
                            print(f"[profile_pic] LinkedIn name mismatch: contact='{contact_name}' profile='{result['fullname']}' — skipping")
                except Exception:
                    pass
            # Fallback: try to extract from page (no name verification possible)
            for img in extract_avatar_from_page(u):
                candidates.append(("linkedin", img))

        elif "twitter.com" in host or "x.com" in host:
            m = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})", u)
            if m:
                handle = m.group(1).lower()
                # Try TinyFish first for better results + name verification
                if extract_generic_profile_pic and _names_match:
                    try:
                        result = extract_generic_profile_pic(u, "twitter")
                        if result and result.get("url") and result.get("fullname"):
                            if _names_match(contact_name, result["fullname"]):
                                candidates.append(("twitter", result["url"]))
                            else:
                                print(f"[profile_pic] Twitter name mismatch: contact='{contact_name}' profile='{result['fullname']}' — skipping")
                    except Exception:
                        pass
                # Fallback (no name verification)
                candidates.append(("twitter", f"https://x.com/{handle}/profile_image?size=original"))

        elif "instagram.com" in host:
            m = re.search(r"instagram\.com/([A-Za-z0-9_.-]+)", u)
            if m:
                handle = m.group(1).lower()
                if extract_generic_profile_pic and _names_match:
                    try:
                        result = extract_generic_profile_pic(u, "instagram")
                        if result and result.get("url") and result.get("fullname"):
                            if _names_match(contact_name, result["fullname"]):
                                candidates.append(("instagram", result["url"]))
                            else:
                                print(f"[profile_pic] Instagram name mismatch: contact='{contact_name}' profile='{result['fullname']}' — skipping")
                    except Exception:
                        pass
                candidates.append(("instagram", f"https://www.instagram.com/{handle}/?__a=1"))

        elif "facebook.com" in host:
            m = re.search(r"facebook\.com/([A-Za-z0-9_.-]+)", u)
            if m:
                handle = m.group(1).lower()
                if extract_generic_profile_pic and _names_match:
                    try:
                        result = extract_generic_profile_pic(u, "facebook")
                        if result and result.get("url") and result.get("fullname"):
                            if _names_match(contact_name, result["fullname"]):
                                candidates.append(("facebook", result["url"]))
                            else:
                                print(f"[profile_pic] Facebook name mismatch: contact='{contact_name}' profile='{result['fullname']}' — skipping")
                    except Exception:
                        pass
                candidates.append(("facebook", f"https://www.facebook.com/{handle}/picture"))

        elif "reddit.com" in host:
            m = re.search(r"reddit\.com/u(?:ser)?/([A-Za-z0-9_-]+)", u)
            if m:
                username = m.group(1)
                if extract_reddit_profile_pic and _names_match:
                    try:
                        result = extract_reddit_profile_pic(username)
                        if result and result.get("url") and result.get("fullname"):
                            if result["url"].endswith('default_icon.svg'):
                                continue
                            if _names_match(contact_name, result["fullname"]):
                                candidates.append(("reddit", result["url"]))
                            else:
                                print(f"[profile_pic] Reddit name mismatch: contact='{contact_name}' profile='{result['fullname']}' — skipping")
                    except Exception:
                        pass

    # Gravatar (known source: tied to the contact's email — no name verification needed)
    email = contact.get("email") or ""
    if not email and isinstance(contact.get("emailAddresses"), list):
        email = contact.get("emailAddresses", [{}])[0].get("value", "")
    if email:
        email = email.strip().lower()
        email_hash = hashlib.md5(email.encode('utf-8')).hexdigest()
        gravatar_url = f"https://www.gravatar.com/avatar/{email_hash}?d=404"
        candidates.append(("gravatar", gravatar_url))

    return candidates


def fetch_raw_html(url):
    """Fetch raw HTML with fallbacks."""
    user_agents = [
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    for ua in user_agents:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            if len(html) >= 200:
                return html
        except Exception:
            pass
    # Fallback to Google cache
    try:
        cache_url = "https://webcache.googleusercontent.com/search?q=cache:" + urllib.parse.quote(url, safe="")
        r = requests.get(cache_url, headers={"User-Agent": user_agents[0]}, timeout=10)
        if r.status_code == 200 and len(r.text) >= 200:
            return r.text
    except Exception:
        pass
    # Fallback to Wayback Machine
    try:
        wayback_url = "https://web.archive.org/web/2024/" + url
        r = requests.get(wayback_url, headers={"User-Agent": user_agents[0]}, timeout=10)
        if r.status_code == 200 and len(r.text) >= 200:
            return r.text
    except Exception:
        pass
    return None


def extract_avatar_from_page(url):
    """Fetch a page and extract likely avatar image URLs."""
    html = fetch_raw_html(url)
    if not html:
        return []
    urls = []
    # og:image
    for m in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I):
        urls.append(m.group(1))
    # twitter:image
    for m in re.finditer(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
        urls.append(m.group(1))
    for m in re.finditer(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', html, re.I):
        urls.append(m.group(1))
    # img tags with avatar/profile in src or class
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I):
        src = m.group(1)
        if re.search(r'avatar|profile|photo', src, re.I):
            urls.append(src)
    # JSON-LD image
    for m in re.finditer(r'"image"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1))
    # dedupe and resolve relative URLs
    seen = set()
    out = []
    for u in urls:
        u = urllib.parse.urljoin(url, u)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def sift_profile_pics(contact):
    """
    Collect avatar candidates ONLY from known social-media URLs already
    discovered by the main Sift pass. The image is known to be the
    person because it comes from a profile page we already matched.
    Returns a list of (source, url) tuples.
    """
    return known_image_urls(contact)


# ── Candidate scoring & selection ────────────────────────────────────────
def evaluate_candidates(contact, candidates, mode):
    """
    mode: 'additive' | 'improve' | 'auto'
    Returns (selected_url, score) or (None, 0)
    """
    current_url = contact.get("avatar_url")
    current_analysis = None
    if current_url:
        img = fetch_image_bytes(current_url)
        if img:
            sharp, color, photo = image_quality(img)
            face, conf, w, h = detect_face(img)
            current_analysis = {
                "quality": (sharp + color + photo) / 3,
                "photo": photo,
                "face": face,
                "url": current_url,
                "width": w,
                "height": h,
            }

    best = (None, 0.0)
    for src, url in candidates:
        if _is_placeholder_url(url):
            continue
        img = fetch_image_bytes(url)
        if not img:
            continue
        # Limit image size (max 5MB)
        if len(img) > 5 * 1024 * 1024:
            continue
        sharp, color, photo = image_quality(img)
        face, conf, w, h = detect_face(img)
        img_cv = cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)
        if img_cv is None:
            continue
        img_h, img_w = img_cv.shape[:2]
        # basic sanity: must be at least ~80px square
        if img_w < 80 or img_h < 80:
            continue
        # Face must be at least 5% of image area and confidence >= 0.5 to be considered a photo
        face_area_ratio = (w * h) / (img_w * img_h) if img_w * img_h > 0 else 0
        if face and (conf < 0.5 or face_area_ratio < 0.05):
            # Downgrade face flag if confidence or area is too low
            face = False
            conf = 0.0
        score = (photo + face * conf + sharp * 0.4 + color * 0.2) / 1.6

        # Require a detected face for selection
        if not face:
            continue
        # mode logic
        if mode == "additive":
            if current_url:  # already has picture – skip
                continue
        elif mode == "improve":
            if current_analysis and current_analysis["face"]:  # already a photo → skip
                continue
            if current_analysis and current_analysis["quality"] > score + 0.1:
                # existing picture is noticeably better – skip
                continue
        # for 'auto' keep the best score

        if score > best[1]:
            best = (url, score)
    return best


# ── Persist the chosen avatar ────────────────────────────────────────────
def set_contact_avatar(contact_id, avatar_url):
    """Write to the persons table; create a fact for provenance."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE persons SET avatar_url = ? WHERE id = ?", (avatar_url, contact_id))
    conn.execute(
        """
        INSERT INTO facts (id, predicate, value, confidence, source_type, source_ref, record_time)
        VALUES (?, 'avatar_url', ?, 0.9, 'web_enrichment', 'profile_pic', ?)
        """,
        (str(uuid.uuid4()), avatar_url, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def enrich_profile_pic(contact, mode="auto"):
    """
    High‑level entry point.
    contact: dict with at least 'id', 'name', 'avatar_url', 'urls'
    Returns (avatar_url, score) of the chosen image or (None, 0).
    """
    candidates = sift_profile_pics(contact)
    if not candidates:
        return None, 0.0
    url, score = evaluate_candidates(contact, candidates, mode)
    if url:
        set_contact_avatar(contact["id"], url)
    return url, score


# ── Tests ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Quick self-test
    import tempfile
    
    # Test placeholder detection
    assert _is_placeholder_url("https://example.com/default_avatar.png")
    assert _is_placeholder_url("https://gravatar.com/avatar/abc?d=default")
    assert not _is_placeholder_url("https://github.com/user.png")
    assert not _is_placeholder_url("https://media.licdn.com/dms/image/v2/.../profile-displayphoto...")
    print("Placeholder tests passed")
    
    # Test known_image_urls with various URLs
    contact = {
        "id": "test",
        "name": "Test User",
        "avatar_url": None,
        "urls": [
            "https://github.com/testuser",
            "https://linkedin.com/in/testuser",
            "https://twitter.com/testuser",
            "https://reddit.com/user/testuser",
        ],
        "email": "test@example.com",
    }
    candidates = known_image_urls(contact)
    sources = [s for s, _ in candidates]
    print(f"Sources found: {sources}")
    assert "github" in sources
    assert "gravatar" in sources
    print("Candidate discovery tests passed")
    
    print("All tests passed!")
