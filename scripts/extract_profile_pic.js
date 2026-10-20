
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
    await browser.close();
    execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session close ${result.session_id}`, { encoding: 'utf8' });
    return picUrl;
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
    await browser.close();
    execSync(`/root/.hermes/profiles/indigo/node/bin/tinyfish browser session close ${result.session_id}`, { encoding: 'utf8' });
    return picUrl || selectorPic || ogImage;
}

const url = process.argv[2];
const platform = process.argv[3] || 'linkedin';

if (platform === 'linkedin') {
    extractProfilePic(url).then(result => {
        if (result) console.log('SUCCESS:' + result);
        else console.log('FAILED');
    }).catch(err => console.error('ERROR:' + err.message));
} else {
    extractGenericProfilePic(url, platform).then(result => {
        if (result) console.log('SUCCESS:' + result);
        else console.log('FAILED');
    }).catch(err => console.error('ERROR:' + err.message));
}
