"""Is this URL a page about a PERSON we identified, or just a page containing a name?

ocas-scout promotes generic web-search results to `profile_website` facts. A
search for a contact's name returns their real site, and also: people-search
aggregators that generate a page for every name, encyclopedia entries about a
namesake, catalog and database records, and ordinary articles that merely mention
someone. All four look identical to a name-match test, because the name IS the
query. Round 13 wrote, as contacts' websites:

    spokeo.com/Bert-Grimes                  a people-search page, generated per name
    en.wikipedia.org/wiki/David_Akers       the NFL kicker, not the CS professor
    thezoereport.com/fashion/skirted-...    an article
    knihobot.cz/a/265272                    a bookseller's catalogue record
    music.bugs.co.kr/track/21260914         a Korean music track
    investice.rb.cz/produkt/stock/...       a stock quote page

and pushed every one into the real address book.

The discriminator is not the name -- it is whether the HOST belongs to the
person. A personal domain (jonesabi.com for Abi Jones) is strong evidence; a
shared platform is acceptable only when the handle sits at the root of a known
profile host (medium.com/@x, github.com/x). Everything else is a page that
happens to contain a name, and is refused.
"""
import re
import urllib.parse

# hosts that manufacture a page per name: matching one proves nothing
_AGGREGATOR_HOSTS = (
    "spokeo.com", "whitepages.com", "radaris.com", "peoplefinders.com",
    "beenverified.com", "intelius.com", "mylife.com", "fastpeoplesearch.com",
    "truepeoplesearch.com", "thatsthem.com", "zoominfo.com", "rocketreach.co",
    "signalhire.com", "contactout.com", "apollo.io", "lusha.com", "usphonebook.com",
    "clustrmaps.com", "addresses.com", "peekyou.com", "192.com", "anywho.com",
    "nuwber.com", "ancestry.com", "familysearch.org", "findagrave.com",
    "locatefamily.com", "cyberbackgroundchecks.com", "smartbackgroundchecks.com",
)
# reference works: the page is about SOMEONE with that name, rarely this contact
_REFERENCE_HOSTS = (
    "wikipedia.org", "wikidata.org", "wikimedia.org", "imdb.com", "themoviedb.org",
    "fdb.cz", "csfd.cz", "knihobot.cz", "ucebnice.cz", "databazeknih.cz",
    "goodreads.com", "ichacha.net", "baike.baidu.com", "everipedia.org",
    "prabook.com", "alchetron.com", "wikiwand.com", "dbpedia.org",
)
# catalogues and listings: a track, a product, a stock, a flipbook
_CATALOG_HOSTS = (
    "music.bugs.co.kr", "genie.co.kr", "melon.com", "fliphtml5.com", "issuu.com",
    "investice.rb.cz", "yahoo.com", "marketwatch.com", "bloomberg.com",
    "crunchbase.com", "pitchbook.com", "opencorporates.com", "sec.gov",
    "slideshare.net", "scribd.com", "amazon.com", "ebay.com", "etsy.com",
)
# generic corporate pages that are nobody's profile
# Specific corporate pages only. A bare "google.com" here also matched
# sites.google.com/view/<user> and developers.google.com/profile/u/<user>, which
# ARE personal pages -- the rule has to name the product, not the company.
_GENERIC_HOSTS = (
    "support.google.com", "health.google", "accounts.google.com",
    "policies.google.com", "myaccount.google.com", "youtube.com/watch",
    "facebook.com/sharer", "microsoft.com", "apple.com", "adobe.com",
    "linkedin.com/pulse",
)
_SHORTENERS = (
    "t.co", "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "lnkd.in",
    "rebrand.ly", "cutt.ly", "is.gd", "trib.al", "dlvr.it", "ift.tt", "shorturl.at",
)
# platforms where a root-level handle IS the person's profile
_PROFILE_HOSTS = (
    "linkedin.com", "github.com", "gitlab.com", "twitter.com", "x.com",
    "instagram.com", "medium.com", "behance.net", "dribbble.com", "bsky.app",
    "mastodon.social", "substack.com", "about.me", "calendly.com", "soundcloud.com",
    "vimeo.com", "flickr.com", "youtube.com", "tiktok.com", "observablehq.com",
    "angel.co", "wellfound.com", "scholar.google.com", "orcid.org", "keybase.io",
    "stackoverflow.com", "dev.to", "hashnode.com", "notion.site", "read.cv",
    "threads.net", "pinterest.com", "disqus.com", "gravatar.com", "patreon.com",
    "sites.google.com", "developers.google.com", "carbonmade.com", "cargo.site",
    "squarespace.com", "wordpress.com", "blogspot.com", "webflow.io", "github.io",
)


def _host(url):
    try:
        p = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    except Exception:  # noqa: BLE001
        return "", ""
    h = (p.netloc or "").lower().split("@")[-1].split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h, p.path or ""


def _matches(host, group):
    return any(host == g or host.endswith("." + g) for g in group)


def name_tokens(name):
    return {t.lower() for t in re.findall(r"[A-Za-z]{3,}", name or "")}


def host_carries_name(url, name):
    """A personal domain: the contact's name appears in the host itself."""
    host, _ = _host(url)
    if not host:
        return False
    stem = host.split(".")[0] if host.count(".") <= 1 else host.rsplit(".", 2)[0]
    flat = re.sub(r"[^a-z]", "", host.lower())
    toks = name_tokens(name)
    if not toks:
        return False
    hits = sum(1 for t in toks if t in flat)
    return hits >= 2 or any(len(t) >= 5 and t in re.sub(r"[^a-z]", "", stem.lower())
                            for t in toks)


def is_publishable_url(url, name=""):
    """False for a page that is about a name rather than about this person."""
    host, path = _host(url)
    if not host:
        return False
    if _matches(host, _SHORTENERS):
        return False
    for group in (_AGGREGATOR_HOSTS, _REFERENCE_HOSTS, _CATALOG_HOSTS):
        if _matches(host, group):
            return False
    full = host + path
    if any(full.startswith(g) or _matches(host, (g.split("/")[0],)) and g in full
           for g in _GENERIC_HOSTS):
        if not _matches(host, _PROFILE_HOSTS):
            return False
    return True


# Where a person's page actually lives on each platform. linkedin.com/posts/<x>
# is a POST, usually by someone else: round 14 filed two strangers' hiring posts
# as a contact's linkedin profile because the host was known and the path was
# only two segments deep.
_PROFILE_PATHS = {
    "linkedin.com": ("in",),
    "medium.com": ("@",),
    "youtube.com": ("@", "c", "user", "channel"),
    "tiktok.com": ("@",),
    "substack.com": ("@",),
    "flickr.com": ("photos", "people"),
    "pinterest.com": (),
    "developers.google.com": ("profile",),
    "sites.google.com": ("view", "site"),
    "scholar.google.com": ("citations",),
}
# never a personal profile, whatever the host
_NON_PROFILE_SEGMENTS = {
    "posts", "pulse", "feed", "company", "school", "groups", "jobs", "events",
    "watch", "shorts", "playlist", "search", "explore", "topics", "tag", "tags",
    "category", "blog", "news", "article", "articles", "story", "stories",
    "product", "products", "shop", "help", "support", "about-us", "contact",
}


def _profile_path_ok(host, segs):
    """Does this path shape name a person on this platform?"""
    if segs and segs[0].lower() in _NON_PROFILE_SEGMENTS:
        return False
    for h, allowed in _PROFILE_PATHS.items():
        if host == h or host.endswith("." + h):
            if not allowed:
                return len(segs) <= 2
            if not segs:
                return False
            first = segs[0].lower()
            return any(first == a or (a == "@" and first.startswith("@"))
                       for a in allowed)
    return len(segs) <= 2


# Path prefixes that introduce a handle rather than being one
_HANDLE_PREFIXES = {"photos", "people", "view", "site", "in", "profile", "u",
                    "user", "users", "c", "channel", "citations", "add", "paypalme"}


# on these the SUBDOMAIN is the account, not the path
_SUBDOMAIN_PLATFORMS = ("wordpress.com", "blogspot.com", "tumblr.com",
                        "substack.com", "medium.com", "github.io", "carbonmade.com",
                        "squarespace.com", "webflow.io", "myportfolio.com",
                        "wixsite.com", "weebly.com", "netlify.app", "vercel.app")


def url_handle(url):
    """The part of the URL that identifies the account, or ''."""
    _h, path = _host(url)
    for plat in _SUBDOMAIN_PLATFORMS:
        if _h.endswith("." + plat):
            sub = _h[: -(len(plat) + 1)]
            if sub and sub != "www":
                return sub.lower()
    segs = [x for x in (path or "").split("/") if x]
    for s_ in segs:
        low = s_.lower().lstrip("@")
        if low in _HANDLE_PREFIXES or not low:
            continue
        return low
    return ""


def _flat(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def slug_names_another_person(url, name, other_names=()):
    """Does the URL's handle spell out a DIFFERENT known person's name?

    Round 15 filed sites.google.com/view/taehwankim under Saul Wyner: the page
    mentioned him somewhere, so the name check passed, while the URL plainly
    belongs to Tae Kim -- who is also in this address book.

    Deliberately narrow. An earlier version rejected any handle that shared no
    three-character run with the contact's name, and threw away real handles:
    github.com/brainwane IS Sumana Harihareswara, behance.net/kalmdown IS Karl
    Mochel. Handles are nicknames far more often than they are names, so the only
    safe rejection is one where the handle demonstrably spells someone else.
    """
    handle = url_handle(url)
    if len(handle) < 6:
        return False
    mine = _flat(name)
    for other in other_names:
        if not other or _flat(other) == mine:
            continue
        parts = [p for p in (_flat(x) for x in str(other).split()[:2]) if len(p) >= 3]
        if len(parts) < 2:
            continue
        if all(p in handle for p in parts):
            # and this contact's own name is not in there
            own = [p for p in (_flat(x) for x in str(name).split()[:2]) if len(p) >= 3]
            if not any(p in handle for p in own):
                return True
    return False


def host_matches_affiliation(url, org="", email=""):
    """Is this host the contact's employer or email domain?

    A page at your employer's or your university's domain is your page; the same
    path shape on a site you have no connection to is a namesake entry.
    """
    host, _ = _host(url)
    if not host:
        return False
    # Every label, not just the second-to-last: multi-part TLDs (lincoln.ox.ac.uk)
    # would otherwise reduce to 'ac'.
    _GENERIC = {"www", "com", "org", "net", "edu", "gov", "ac", "co", "uk", "us",
                "io", "ai", "app", "dev", "me", "info", "biz", "cz", "de", "fr"}
    labels = [x for x in host.split(".") if x and x not in _GENERIC]

    dom = (email or "").split("@")[-1].strip().lower()
    if dom and "." in dom:
        if host == dom or host.endswith("." + dom) or dom.endswith("." + host):
            return True
        dlabels = {x for x in dom.split(".") if x and x not in _GENERIC}
        if dlabels & set(labels):
            return True

    o = re.sub(r"[^a-z0-9]", "", (org or "").lower())
    if len(o) >= 4:
        for lab in labels:
            L = re.sub(r"[^a-z0-9]", "", lab)
            if len(L) >= 4 and (L in o or o in L):
                return True
    return False


def handle_is_opaque(url):
    """A handle with no name in it identifies nobody.

    A numeric id is assigned by the site, not chosen by the person, so it is no
    evidence that this contact owns the page.
    """
    h = url_handle(url)
    if not h:
        return False
    return len(re.sub(r"[^a-z]", "", h.lower())) < 3


def linkedin_slug_is_someone_else(url, name):
    """A LinkedIn /in/ slug is built from the owner's real name.

    So a hyphenated, multi-word slug that shares nothing with this contact is
    another person's profile. Deliberately limited to LinkedIn and to
    name-shaped slugs -- the general "handle must resemble the name" rule was
    tried and discarded real handles (github.com/brainwane IS Sumana
    Harihareswara, behance.net/kalmdown IS Karl Mochel).
    """
    m = re.search(r"//(?:[a-z0-9.-]*\.)?linkedin\.com/in/([^/?#]+)", str(url or ""), re.I)
    if not m:
        return False
    slug = m.group(1).lower()
    groups = [g for g in re.split(r"[-_.]", slug) if re.fullmatch(r"[a-z]{2,}", g)]
    if len(groups) < 2:
        return False          # a concatenated handle, not a name -- leave it
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", name or "")}
    if not own:
        return False
    flat = re.sub(r"[^a-z]", "", slug)
    return not any(w in flat for w in own)


def is_person_profile(url, name, platform=None, other_names=(), org="", email=""):
    """Stricter: publishable AND plausibly THIS person's own page.

    `platform` is scout's own classification of the hit. Anything other than
    "website" means it recognised the host as a profile site, which is better
    evidence than a hand-kept host list -- that list was missing facebook and
    snapchat and threw away real profiles. For a recognised platform only the
    PATH still has to be checked, because linkedin.com/posts/<stranger> and
    linkedin.com/company/<employer> sit on a profile host without being one.
    """
    if not is_publishable_url(url, name):
        return False
    if other_names and slug_names_another_person(url, name, other_names):
        return False
    if handle_is_opaque(url):
        return False
    if linkedin_slug_is_someone_else(url, name):
        return False
    host, path = _host(url)
    segs = [s for s in path.split("/") if s]
    if platform and platform not in ("website", "", None):
        return _profile_path_ok(host, segs)
    if _matches(host, _PROFILE_HOSTS):
        return _profile_path_ok(host, segs)
    if host_carries_name(url, name):
        return True          # their own domain, any depth
    if segs and segs[0].startswith("~"):
        return True          # ~user on an institutional host is a personal page
    if host_matches_affiliation(url, org, email):
        # Their employer's own site. A bare domain is the company itself and
        # belongs on the contact (monkeyranch.com for Sue Cooper of Monkey
        # Ranch); a DEEP page there must still name them, or it is the
        # employer's content rather than this person's profile
        # (aquent.com/blog/how-to-interview-a-designer, ox.ac.uk/people/medical).
        if not segs:
            return True
        _pf = re.sub(r"[^a-z]", "", (path or "").lower())
        return any(t in _pf for t in name_tokens(name) if len(t) >= 4)
    if len(segs) == 1 and segs[0].startswith("@"):
        return True          # @handle is a handle everywhere (any mastodon host)
    if not segs:
        # a bare domain is somebody's site, and a search that surfaced it for
        # this name most often surfaced theirs (scifiinterfaces.com)
        return True
    # An unknown host is somebody else's site. A page there carrying the
    # contact's name is a page ABOUT a name -- the namesake trap again -- so it
    # is refused however shallow the path.
    return False
