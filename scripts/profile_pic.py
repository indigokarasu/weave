import sys as _sys
if '--help' in _sys.argv or '-h' in _sys.argv:
    print('Usage: ' + __file__ + ' [--help]')
    _sys.exit(0)
#!/usr/bin/env python3
"""
profile_pic.py — Free-source profile picture discovery & evaluation for Weave.

Modes:
  additive  – only add an image when the contact has no avatar_url yet.
  improve   – add an image when the current avatar is NOT a photograph (no face,
              or clearly a monogram / non-person graphic).  Never replaces a
              picture that already contains a detected human face.
  auto      – additive for the first run, improve on subsequent runs.

All image acquisition uses the same free fallbacks as weave_enrich.fetch_page.
Face detection uses OpenCV Haar cascades (downloaded once from GitHub).
"""
import os
import re
import html
import time
import uuid
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
CASCADE_PATH = os.path.join(os.path.dirname(__file__), "haarcascade_frontalface_default.xml")
CASCADE_URL = (
    "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/"
    "haarcascade_frontalface_default.xml"
)

# Import the shared search/fetch helpers from weave_enrich (same directory)
try:
    from weave_enrich import searxng_search
except Exception:
    searxng_search = None


# ── Haar cascade acquisition ────────────────────────────────────────────
def _ensure_cascade():
    if os.path.exists(CASCADE_PATH):
        return CASCADE_PATH
    try:
        urllib.request.urlretrieve(CASCADE_URL, CASCADE_PATH)
    except Exception as e:
        print(f"[profile_pic] failed to download cascade: {e}")
        return None
    return CASCADE_PATH


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
    """Return (face_detected, confidence, width, height)."""
    if not image_bytes:
        return False, 0.0, 0, 0
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False, 0.0, 0, 0
    h, w = img.shape[:2]
    model_path = os.path.join(os.path.dirname(__file__), "face_detection_yunet.onnx")
    if not os.path.exists(model_path):
        # download
        try:
            urllib.request.urlretrieve(
                "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                model_path,
            )
        except Exception:
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
def searxng_image_search(query, limit=6):
    """Search SearXNG for images. Returns list of image URLs."""
    import json
    import urllib.parse
    import urllib.request
    base = "http://localhost:8888/search"
    params = urllib.parse.urlencode({"q": query, "format": "json", "categories": "images", "limit": limit})
    url = f"{base}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HermesAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        results = []
        for item in data.get("results", [])[:limit]:
            img_url = item.get("img_src") or item.get("url", "")
            if img_url:
                results.append(img_url)
        return results
    except Exception as e:
        print(f"[profile_pic] SearXNG image search failed: {e}")
        return []
KNOWN_AVATAR_HOSTS = {
    "linkedin.com": "linkedin",
    "github.com": "github",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
}


def fetch_raw_html(url):
    # Try direct fetch first
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
    # also look for JSON-LD with image
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


def known_image_urls(contact):
    """Turn already‑found profile URLs into concrete avatar URL candidates."""
    candidates = []
    urls = contact.get("urls", []) or []
    for u in urls:
        host = urllib.parse.urlparse(u).netloc.lower()
        if "linkedin.com" in host:
            # fetch page and extract image
            for img in extract_avatar_from_page(u):
                candidates.append(("linkedin", img))
        elif "github.com" in host:
            m = re.search(r"github\.com/([A-Za-z0-9_-]+)", u)
            if m:
                handle = m.group(1).lower()
                candidates.append(("github", f"https://github.com/{handle}.png"))
        elif "twitter.com" in host or "x.com" in host:
            m = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})", u)
            if m:
                handle = m.group(1).lower()
                candidates.append(("twitter", f"https://x.com/{handle}/profile_image?size=original"))
        elif "instagram.com" in host:
            m = re.search(r"instagram\.com/([A-Za-z0-9_.-]+)", u)
            if m:
                handle = m.group(1).lower()
                candidates.append(("instagram", f"https://www.instagram.com/{handle}/?__a=1"))
        elif "facebook.com" in host:
            m = re.search(r"facebook\.com/([A-Za-z0-9_.-]+)", u)
            if m:
                handle = m.group(1).lower()
                candidates.append(("facebook", f"https://www.facebook.com/{handle}/picture"))
        else:
            # try to extract from any page
            for img in extract_avatar_from_page(u):
                candidates.append((host, img))
    return candidates


def sift_profile_pics(contact):
    """
    Collect avatar candidates from three sources:
      1. Known social‑media URLs already discovered by the main Sift pass.
      2. A free‑text SearXNG search for '<first> <last> avatar' — both direct
         image URLs and og:image tags extracted from the result pages.
      3. Any profile URLs in the contact record that produce an og:image.
      4. SearXNG image search.
    Returns a list of (source, url) tuples.
    """
    candidates = known_image_urls(contact)

    name = contact.get("name") or ""
    if not name:
        name = ((contact.get("name_given") or "") + " " + (contact.get("name_family") or "")).strip()

    if name and searxng_search:
        query = f"{name} profile picture avatar"
        try:
            res = searxng_search(query, limit=6)
            for item in res:
                url = item.get("url", "")
                if not url:
                    continue
                if re.search(r"\.(jpe?g|png|gif|webp)$", url, re.I):
                    candidates.append(("search_direct", url))
                    continue
                try:
                    imgs = extract_avatar_from_page(url)
                    for img in imgs:
                        candidates.append(("search_page", img))
                except Exception:
                    pass
        except Exception as e:
            print(f"[profile_pic] SearXNG search failed: {e}")

    if name:
        img_urls = searxng_image_search(f"{name} profile picture", limit=6)
        for img_url in img_urls:
            candidates.append(("search_image", img_url))

    return candidates

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
