#!/usr/bin/env python3
"""
TinyFish browser automation helper for profile picture extraction and OSINT.
Provides a Python interface to TinyFish CLI for creating stealth browser sessions.
"""
import os
import json
import subprocess
import tempfile
import re
from pathlib import Path

TINYFISH_CLI = "/root/.hermes/profiles/indigo/node/bin/tinyfish"
EXTRACT_SCRIPT = "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts/extract_profile_pic.js"

# ── Name normalization (lightweight, no dependency on research_person) ──
def _normalize_name(name):
    """Lowercase, strip, collapse whitespace, remove punctuation."""
    if not name:
        return ""
    name = re.sub(r"[^\w\s]", "", name.lower())
    return " ".join(name.split())

def _family_name(name):
    """Return the last token of a normalized name."""
    parts = _normalize_name(name).split()
    return parts[-1] if parts else ""

def _names_match(contact_name, profile_name, require_given=True):
    """
    Strict name match: family names must be equal AND given name must appear.
    Returns True only if profile_name confirms contact_name.
    """
    a = _normalize_name(contact_name).split()
    b = _normalize_name(profile_name).split()
    if not a or not b:
        return False
    if a[-1] != b[-1]:
        return False
    if require_given and len(set(a) & set(b)) < min(2, len(a), len(b)):
        return False
    return True

# ── Extraction script (Node.js + Playwright via TinyFish) ──
EXTRACT_SCRIPT_CONTENT = r"""
const { chromium } = require('playwright-core');

async function extractProfilePic(profileUrl) {
    const { execSync } = require('child_process');
    const output = execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session create --url "${profileUrl}"`, { encoding: 'utf8' });
    const result = JSON.parse(output.trim());
    const browser = await chromium.connectOverCDP(result.cdp_url);
    const context = browser.contexts()[0];
    const page = context.pages()[0];
    page.setDefaultTimeout(60000);
    try {
        await page.waitForLoadState('networkidle', { timeout: 30000 });
    } catch (e) {}
    await page.waitForTimeout(8000);
    const content = await page.content();
    // Find all profile-displayphoto URLs (not background, not article, not company)
    const matches = content.match(/https:\/\/media\.licdn\.com\/dms\/image[^"']*profile-displayphoto[^"']*/g);
    let picUrl = null;
    if (matches) {
        const candidates = matches.filter(url => {
            if (url.includes('profile-displaybackgroundimage')) return false;
            if (url.includes('article-cover')) return false;
            if (url.includes('company-logo')) return false;
            if (url.includes('feedshare')) return false;
            if (url.includes('articleshare')) return false;
            if (url.includes('videocover')) return false;
            return true;
        });
        if (candidates.length > 0) {
            picUrl = candidates[0];
        }
    }
    // Extract full name from page
    const fullname = (await page.evaluate(() => {
        const sel = document.querySelector('h1.text-heading-xlarge') ||
                     document.querySelector('.pv-text-details__left-panel h1') ||
                     document.querySelector('h1[class*="text-heading"]') ||
                     document.querySelector('h1');
        return sel ? sel.innerText.trim() : null;
    })) || null;
    await browser.close();
    execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session close ${result.session_id}`, { encoding: 'utf8' });
    return { url: picUrl, fullname };
}

async function extractGenericProfilePic(profileUrl, platform) {
    const { execSync } = require('child_process');
    const output = execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session create --url "${profileUrl}"`, { encoding: 'utf8' });
    const result = JSON.parse(output.trim());
    const browser = await chromium.connectOverCDP(result.cdp_url);
    const context = browser.contexts()[0];
    const page = context.pages()[0];
    page.setDefaultTimeout(60000);
    try {
        await page.waitForLoadState('networkidle', { timeout: 30000 });
    } catch (e) {}
    await page.waitForTimeout(8000);
    // Try to get og:image
    const ogImage = await page.evaluate(() => {
        const meta = document.querySelector('meta[property="og:image"]');
        return meta ? meta.getAttribute('content') : null;
    });
    // Try platform-specific selectors
    let selectorPic = null;
    const selectors = {
        'twitter': ['img[data-testid="UserAvatar-Container"]', 'img.css-1dbjc4n.r-1adg3ll.r-1udh08x'],
        'x': ['img[data-testid="UserAvatar-Container"]', 'img.css-1dbjc4n.r-1adg3ll.r-1udh08x'],
        'instagram': ['img._aarf', 'header img'],
        'facebook': ['img[data-visualcompletion="image"]', '.profilePic img'],
        'reddit': ['img[alt*="Avatar"]', 'img[class*="avatar"]', 'img[data-testid="user-avatar"]'],
    };
    if (selectors[platform]) {
        for (const sel of selectors[platform]) {
            const el = await page.$(sel);
            if (el) {
                selectorPic = await el.getAttribute('src');
                if (selectorPic) break;
            }
        }
    }
    // Extract full name from page
    const fullname = (await page.evaluate(() => {
        const h1 = document.querySelector('h1');
        if (h1) return h1.innerText.trim();
        const title = document.querySelector('title');
        return title ? title.innerText.trim() : null;
    })) || null;
    await browser.close();
    execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session close ${result.session_id}`, { encoding: 'utf8' });
    return { url: picUrl || selectorPic || ogImage, fullname };
}

const url = process.argv[2];
const platform = process.argv[3] || 'linkedin';

if (platform === 'linkedin') {
    extractProfilePic(url).then(result => {
        if (result && result.url) console.log('SUCCESS:' + JSON.stringify(result));
        else console.log('FAILED');
    }).catch(err => console.error('ERROR:' + err.message));
} else {
    extractGenericProfilePic(url, platform).then(result => {
        if (result && result.url) console.log('SUCCESS:' + JSON.stringify(result));
        else console.log('FAILED');
    }).catch(err => console.error('ERROR:' + err.message));
}
"""

def ensure_extract_script():
    """Write the Node.js extraction script if it doesn't exist."""
    if not os.path.exists(EXTRACT_SCRIPT):
        with open(EXTRACT_SCRIPT, 'w') as f:
            f.write(EXTRACT_SCRIPT_CONTENT)
    return EXTRACT_SCRIPT

def create_session(url):
    """Create a TinyFish browser session and return session_id and cdp_url."""
    output = subprocess.check_output([
        TINYFISH_CLI, "browser", "session", "create", "--url", url
    ], text=True, timeout=30)
    return json.loads(output.strip())

def close_session(session_id):
    """Close a TinyFish browser session."""
    subprocess.run([
        TINYFISH_CLI, "browser", "session", "close", session_id
    ], capture_output=True, text=True, timeout=30)

def extract_linkedin_profile_pic(linkedin_url, timeout=120):
    """
    Extract profile picture URL and full name from a LinkedIn profile using TinyFish.
    Returns dict {url, fullname} or None.
    """
    ensure_extract_script()
    try:
        result = subprocess.run(
            ['/root/.hermes/profiles/indigo/node/bin/node', EXTRACT_SCRIPT, linkedin_url, 'linkedin'],
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout.strip()
        if output.startswith('SUCCESS:'):
            data = json.loads(output[8:])
            return {"url": data.get("url"), "fullname": data.get("fullname")}
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

def extract_generic_profile_pic(profile_url, platform, timeout=120):
    """
    Extract profile picture from X/Twitter, Instagram, Facebook using TinyFish.
    Returns dict {url, fullname} or None.
    """
    ensure_extract_script()
    try:
        result = subprocess.run(
            ['/root/.hermes/profiles/indigo/node/bin/node', EXTRACT_SCRIPT, profile_url, platform],
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout.strip()
        if output.startswith('SUCCESS:'):
            data = json.loads(output[8:])
            return {"url": data.get("url"), "fullname": data.get("fullname")}
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

def extract_reddit_profile_pic(username_or_url, timeout=120):
    """
    Extract profile picture from Reddit using TinyFish.
    Returns dict {url, fullname} or None.
    """
    ensure_extract_script()
    if username_or_url.startswith('http'):
        url = username_or_url
    else:
        url = f"https://www.reddit.com/user/{username_or_url}/"
    try:
        result = subprocess.run(
            ['/root/.hermes/profiles/indigo/node/bin/node', EXTRACT_SCRIPT, url, 'reddit'],
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout.strip()
        if output.startswith('SUCCESS:'):
            data = json.loads(output[8:])
            return {"url": data.get("url"), "fullname": data.get("fullname")}
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

def browse_and_extract(url, extractor_js, timeout=120):
    """
    Generic TinyFish browse: create session, run custom extractor JS, close session.
    extractor_js: string of JavaScript to run in page context, should return JSON-serializable value.
    Returns parsed JSON or None.
    """
    ensure_extract_script()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        script_content = f'''
const {{ chromium }} = require('/root/.hermes/profiles/indigo/skills/ocas-weave/scripts/node_modules/playwright-core');

async function run() {{
    const {{ execSync }} = require('child_process');
    const output = execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session create --url "{url}"`, {{ encoding: 'utf8' }});
    const result = JSON.parse(output.trim());
    const browser = await chromium.connectOverCDP(result.cdp_url);
    const context = browser.contexts()[0];
    const page = context.pages()[0];
    page.setDefaultTimeout(60000);
    try {{
        await page.waitForLoadState('networkidle', {{ timeout: 30000 }});
    }} catch (e) {{}}
    await page.waitForTimeout(5000);
    const extracted_result = await page.evaluate(() => {
        {extractor_js}
    });
    await browser.close();
    execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session close ${{result.session_id}}`, {{ encoding: 'utf8' }});
    return extracted_result;
}}

run().then(r => {{
    if (r !== null && r !== undefined) console.log('SUCCESS:' + JSON.stringify(r));
    else console.log('FAILED');
}}).catch(err => console.error('ERROR:' + err.message));
'''
        f.write(script_content)
        temp_script = f.name

    try:
        result = subprocess.run(
            ['/root/.hermes/profiles/indigo/node/bin/node', temp_script],
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout.strip()
        print(f"DEBUG: returncode={result.returncode}, stdout_len={len(output)}, stderr={result.stderr[:200]}")
        if output.startswith('SUCCESS:'):
            return json.loads(output[8:])
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
    finally:
        try:
            os.unlink(temp_script)
        except:
            pass

if __name__ == "__main__":
    # Test
    import sys
    if len(sys.argv) > 1:
        url = sys.argv[1]
        platform = sys.argv[2] if len(sys.argv) > 2 else 'linkedin'
        if platform == 'linkedin':
            print(extract_linkedin_profile_pic(url))
        elif platform == 'reddit':
            print(extract_reddit_profile_pic(url))
        else:
            print(extract_generic_profile_pic(url, platform))
