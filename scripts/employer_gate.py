"""An inferred employer may only reach the contact record if something agrees.

'Heriot-Watt University' was not junk-SHAPED. It read as a perfectly good
employer and was simply false, so no string rule could ever have caught it.
What distinguishes it from a true find is not the string, it is that nothing
else about the contact points there:

    casey@examplehealth.example   -> 'Example Health'   the email domain says so
    sample.person@meta.example          -> 'Facebook AI'     Facebook IS Meta
    Kim Appelquist           -> 'Heriot-Watt'     nothing, anywhere

So a crawler's guess about where someone works is held out of the visible
contact record -- the row that syncs into the real address book -- unless the
contact's own email domain, one of their profile URLs, or another of their
facts independently names it.

The guess is not thrown away. It is still written to `facts` with its source and
confidence, where it can be reviewed. This gate governs promotion to the record,
not retention: the graph may hold a hypothesis, the address book may not.

Two things this deliberately does NOT do:

  * It does not judge whether the company is real. Heriot-Watt is a real
    university; that was never the problem.
  * It does not repair or reconstruct. A fragment that fails is left out, never
    assembled into something that looks better.

Some true employers fail this test -- a job nothing else about the contact
records cannot be told from an invented one. That is the intended direction of
the error: a missing employer is visibly missing, a wrong one reads as fact.
"""
import re

# Companies whose name and domain do not resemble each other. Members of a group
# all name the same employer, so matching any one corroborates the others. Only
# renames and acquisitions well-known enough to be certain -- not a place to
# guess, since a wrong entry here manufactures corroboration.
ALIAS_GROUPS = [
    {"facebook", "meta", "metaplatforms", "instagram", "whatsapp", "oculus"},
    {"google", "alphabet", "youtube", "deepmind", "waymo", "fitbit"},
    {"twitter", "xcorp"},
    {"square", "block"},
    {"berkeleylab", "lbl", "lbnl", "lawrenceberkeleynationallaboratory"},
    {"ibm", "internationalbusinessmachines", "redhat"},
    {"microsoft", "msft", "linkedin", "github"},
    {"amazon", "amazonwebservices", "twitch"},
    {"apple", "icloud"},
    {"salesforce", "slack", "tableau", "heroku"},
    {"adobe", "behance"},
    {"verizon", "yahoo", "aol"},
]

# Hosts where anyone may have a page. A profile on one of these says the contact
# uses the platform, never that they work for it -- half the address book has a
# github.com URL. Such a host cannot corroborate an employer of the same name.
PROFILE_HOSTS = {
    "github", "gitlab", "twitter", "linkedin", "facebook", "instagram",
    "medium", "behance", "dribbble", "youtube", "mastodon", "bsky", "bluesky",
    "threads", "tiktok", "reddit", "substack", "patreon", "tumblr", "flickr",
    "vimeo", "soundcloud", "spotify", "goodreads", "pinterest", "quora",
    "stackoverflow", "keybase", "about", "linktr", "google", "gravatar",
    "wordpress", "blogspot", "notion", "figma", "producthunt", "angel",
    "crunchbase", "wellfound", "eventbrite", "meetup", "calendly",
}

# Words carrying no identifying force. An employer matching a domain only
# through one of these is not corroborated at all.
_STOP = {
    "the", "and", "of", "for", "inc", "llc", "ltd", "corp", "corporation",
    "company", "co", "group", "holdings", "partners", "associates", "gmbh",
    "sa", "nv", "plc", "university", "college", "school", "institute", "lab",
    "labs", "laboratory", "studio", "studios", "design", "media", "digital",
    "consulting", "services", "solutions", "systems", "technologies", "tech",
    "software", "agency", "creative", "global", "international", "national",
    "center", "centre", "foundation", "association", "society", "council",
    "department", "office", "team", "network", "online", "web", "app",
}

# Free mailbox providers, as whole registrable domains. Deliberately not bare
# labels: matching the FIRST label of a domain treats 'mail.house.gov' as
# personal mail because it begins with 'mail', which silently discarded the one
# signal that placed a congressional staffer at the House of Representatives.
FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.jp",
    "yahoo.fr", "yahoo.de", "yahoo.ca", "ymail.com", "rocketmail.com",
    "hotmail.com", "hotmail.co.uk", "hotmail.fr", "outlook.com", "live.com",
    "live.co.uk", "msn.com", "aol.com", "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "pm.me", "fastmail.com", "fastmail.fm",
    "hey.com", "zoho.com", "gmx.com", "gmx.de", "gmx.net", "web.de",
    "mail.com", "email.com", "mail.ru", "inbox.com", "mailbox.org",
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "cox.net",
    "earthlink.net", "mindspring.com", "pacbell.net", "bellsouth.net",
    "charter.net", "juno.com", "netzero.net", "rogers.com", "shaw.ca",
    "telus.net", "btinternet.com", "orange.fr", "free.fr", "wanadoo.fr",
    "yandex.ru", "qq.com", "163.com", "126.com", "naver.com", "hushmail.com",
    "tutanota.com", "duck.com", "posteo.de", "runbox.com",
}

# Second-level labels that are part of a country's namespace rather than a
# name of their own: 'lincoln.ox.ac.uk' is not registered at 'ac.uk'.
_PUBLIC_SL = {"co", "com", "org", "net", "ac", "gov", "edu", "gouv", "or", "ne"}



def flat(s):
    """Lowercase alphanumerics only -- how a name looks inside a domain."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def tokens(s):
    """Identifying words of a name, with the generic ones removed."""
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if w not in _STOP and len(w) > 2]


def registrable(domain):
    """The part of a hostname that was actually registered.

    'mail.house.gov' -> 'house.gov';  'gmail.com' -> 'gmail.com';
    'x.yahoo.co.uk'  -> 'yahoo.co.uk'.
    """
    parts = [p for p in (domain or "").lower().strip().lstrip("@").split(".") if p]
    if len(parts) < 2:
        return ".".join(parts)
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _PUBLIC_SL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_free_mail(domain):
    """A personal mailbox host tells you nothing about an employer.

    Matched on the registrable domain, never a prefix: 'meta.com' begins with
    'me' but is not me.com, and 'mail.house.gov' is not a webmail provider.
    """
    return registrable(domain) in FREE_MAIL


def _aliases(key):
    """Every name meaning the same employer as `key`, including itself."""
    out = {key}
    for grp in ALIAS_GROUPS:
        if key in grp:
            out |= grp
    return out


def candidates(org):
    """Every string that, found in a domain, would name this employer.

    Built from the whole name AND its individual words, because the alias that
    matters is usually a word rather than the full string: 'Facebook AI' flattens
    to 'facebookai', which resembles no domain, but its word 'facebook' resolves
    to 'meta'.
    """
    keys = set()
    whole = flat(org)
    if len(whole) >= 3:
        keys |= _aliases(whole)
    for t in tokens(org):
        al = _aliases(t)
        if len(al) > 1:            # a known brand: its whole group counts
            keys |= al
        elif len(t) >= 5:          # an ordinary word needs real length
            keys.add(t)
    return {k for k in keys if len(k) >= 3}


def _host(value):
    """The hostname of a URL-ish value. '' if it is not a URL."""
    v = str(value or "").strip()
    if "." not in v:
        return ""
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v, flags=re.I)
    host = v.split("/")[0].split("?")[0].split("@")[-1].lower()
    return host if "." in host else ""


def _matches(cands, domain):
    """Does a hostname carry one of these names?

    A long name may appear anywhere in the domain, since companies routinely sit
    on a subdomain ('about.gitlab.com') or run their words together. A short one
    must be a whole label: 'aws' happens to sit inside 'lawson.com', and that is
    not Amazon.
    """
    labels = [flat(x) for x in (domain or "").lower().split(".") if x]
    if not labels:
        return False
    joined = "".join(labels)
    for c in cands:
        if len(c) >= 5:
            if c in joined:
                return True
        elif c in labels:
            return True
    return False


def corroborate(org, email="", urls=(), other_values=()):
    """Is this employer supported by anything else the contact has?

    Returns a short phrase naming the supporting signal, or None. The phrase goes
    into the run log so a decision can be read back later without re-deriving it.
    """
    org = (org or "").strip()
    cands = candidates(org)
    if not cands:
        return None

    dom = (email or "").split("@")[-1].strip().lower()
    if dom and not is_free_mail(dom) and _matches(cands, dom):
        return "email domain %s" % dom

    for u in urls:
        h = _host(u)
        if not h:
            continue
        if h.split(".")[0] in PROFILE_HOSTS or \
                (len(h.split(".")) > 2 and h.split(".")[-2] in PROFILE_HOSTS):
            continue          # a page anyone can have is not employment
        if _matches(cands, h):
            return "profile url %s" % h

    # Last resort: some other fact names the same employer. The contact's own org
    # fact is excluded by the caller, so this means a separate statement -- a bio
    # line, a title, a headline.
    whole = flat(org)
    for v in other_values:
        vs = str(v or "")
        if vs.strip().lower() == org.lower():
            continue
        if len(whole) >= 5 and whole in flat(vs):
            return "named in another fact"
    return None
