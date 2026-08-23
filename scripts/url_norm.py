"""One canonical form for a contact URL, used by every path that reads or writes one.

There were two: the inbound importer canonicalised (https, no www, no trailing
slash) while the outbound push sent the raw weave value with at most a scheme
prepended, and the merge compared raw values. So google's
'https://www.abouraya.com/' and weave's 'https://abouraya.com' looked like
different URLs and both survived -- 923 of google's 3,829 URLs are duplicates of
each other under normalisation. Comparing and writing must use the same function.

canonical_url() is what gets stored and pushed. dedupe_key() is only for
comparison; it lowercases the whole URL, which is wrong to store (paths can be
case-significant off-platform) but right for deciding "same link".
"""
import re
import urllib.parse

_BAD_SCHEMES = ("mailto:", "javascript:", "tel:", "data:", "file:", "sms:")

# handles are case-insensitive on these, so the path folds for storage too
_HANDLE_HOSTS = (
    "linkedin.com", "twitter.com", "x.com", "github.com", "instagram.com",
    "facebook.com", "medium.com", "behance.net", "dribbble.com", "tiktok.com",
    "bsky.app", "pinterest.com", "youtube.com", "soundcloud.com", "vimeo.com",
    "about.me", "calendly.com", "substack.com", "mastodon.social", "threads.net",
)

# carry no identity, and split one link into several
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
             "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref_src", "ref_url",
             "si", "feature", "trk", "trkinfo", "originalreferer"}

# scraped out of html/csv and left glued to the end
# Closing braces belong here too: a url parsed out of a JSON blob keeps the
# blob's brace, which is how one contact's github/medium/x/dribbble links all
# ended in '}' and were pushed to Google in that form.
_TRAILING_JUNK = "]}),.;'\"<>|"

_EMAIL_RE = re.compile(r"^[^\s/:@]+@[^\s/:@]+\.[A-Za-z]{2,}$")


def _split(raw):
    if not raw or not isinstance(raw, str):
        return None
    url = raw.strip().strip('"').strip("'").strip()
    while url and url[-1] in _TRAILING_JUNK:
        url = url[:-1]
    if not url:
        return None
    if url.lower().startswith(_BAD_SCHEMES):
        return None
    if "://" not in url:
        # A bare email is not a URL. Prepending a scheme makes urlsplit read the
        # local part as userinfo, so every '<anything>@gmail.com' collapsed to
        # 'https://gmail.com' -- one key for dozens of unrelated people.
        if _EMAIL_RE.match(url):
            return None
        url = "https://" + url.lstrip("/")
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:  # noqa: BLE001
        return None
    if p.scheme.lower() not in ("http", "https"):
        return None
    host = (p.netloc or "").lower().split("@")[-1].split(":")[0]
    if not host or "." not in host or host.endswith("."):
        return None
    if host.startswith("www."):
        host = host[4:]
    return p, host


def canonical_url(raw):
    """The single stored/pushed form: https, no www, no trailing slash, no tracking."""
    parts = _split(raw)
    if not parts:
        return None
    p, host = parts
    path = p.path.rstrip("/")
    if any(host == h or host.endswith("." + h) for h in _HANDLE_HOSTS):
        path = path.lower()
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if k.lower() not in _TRACKING])
    if not path and (query or p.fragment):
        # some sites route identity through the fragment (rdio.com/#/people/<user>);
        # an empty path directly against '#' is legal but unreadable
        path = "/"
    return urllib.parse.urlunsplit(("https", host, path, query, p.fragment))


def dedupe_key(raw):
    """Comparison only -- two URLs with this key are the same link."""
    c = canonical_url(raw)
    if not c:
        return None
    return c.lower().rstrip("/")


def dedupe(urls, keyfn=None):
    """Keep first occurrence per dedupe_key, preserving order."""
    out, seen = [], set()
    for u in urls:
        raw = keyfn(u) if keyfn else u
        k = dedupe_key(raw)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(u)
    return out
