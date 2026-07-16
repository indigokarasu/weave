"""Map a People-DB person -> a CLEAN Google contact-card payload (Choice-2).
Card fields only, properly labeled, TEMPORAL (current facts only). NEVER Notes,
NEVER the Weave/OSINT enrichment (that stays in people.db). Returns (body, update_mask)."""
import re
import sys

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip() or "Usage: python3 people_to_google.py")
    sys.exit(0)

URL_LABEL=[("linkedin.com","LinkedIn"),("github.com","GitHub"),("developers.google.com","Google Developer"),
           ("x.com","X"),("twitter.com","X"),("scholar.google","Academic"),("researchgate","Academic"),
           ("semanticscholar","Academic"),("medium.com","Medium"),("instagram.com","Instagram"),
           ("facebook.com","Facebook"),("youtube.com","YouTube")]
URL_KEYS={"github_profile","developer_profile","has_profile","social_urls","linkedin","website",
          "academic_profile","url","profile","twitter","instagram"}
def _url_label(u):
    ul=u.lower()
    for dom,lab in URL_LABEL:
        if dom in ul: return lab
    return "Website"
def _extract_urls(attrs):
    blob=" ".join(str(v) for vals in attrs.values() for v in vals)
    pats=[r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/[^\s,;)]+", r"(?:https?://)?github\.com/[^\s,;)]+",
          r"(?:https?://)?developers\.google\.com/[^\s,;)]+", r"(?:https?://)?(?:x|twitter)\.com/[^\s,;)]+",
          r"https?://[^\s,;)]+"]
    seen={}
    for p in pats:
        for m in re.findall(p, blob, re.I):
            u=m.strip().rstrip(".,;)")
            if len(u)<8: continue
            if not u.lower().startswith("http"): u="https://"+u
            lab=_url_label(u)
            if lab not in seen: seen[lab]=u   # one per platform label
    return [{"value":u,"type":k} for k,u in seen.items()]
def _best(vals):
    seen=[]
    for v in vals:
        v=str(v).strip()
        if v and v not in seen: seen.append(v)
    return max(seen,key=len) if seen else None
def _clean_title(occs):
    cand=None
    for v in occs:
        if " at " not in v and "(" not in v and len(v)<=60:
            cand=v.strip(); break
    if cand is None and occs:
        cand=re.split(r"\s+at\s+|[(,;]", occs[0])[0].strip()
    if cand and len(cand)>60:
        cand=re.split(r"[,;\-]| and ", cand)[0].strip()[:60]
    return cand or None

def build_google_payload(p):
    """p = {display_name, given_name, family_name,
            emails:[{value,label}], phones:[{value,label}],   # CURRENT only (caller filters historical)
            attrs:{key:[values]},                              # CURRENT core attrs only
            relations:[(type,name)]}"""
    body={}; mask=[]
    body["names"]=[{"displayName":p["display_name"],"givenName":p.get("given_name"),"familyName":p.get("family_name")}]; mask.append("names")
    if p.get("emails"):
        body["emailAddresses"]=[{"value":e["value"],"type":e.get("label","other")} for e in p["emails"]]; mask.append("emailAddresses")
    if p.get("phones"):
        body["phoneNumbers"]=[{"value":ph["value"],"type":ph.get("label","mobile")} for ph in p["phones"]]; mask.append("phoneNumbers")
    a=p["attrs"]
    org=_best(a.get("org",[])); title=_clean_title(a.get("occupation",[]))
    if org or title: body["organizations"]=[{k:v for k,v in {"name":org,"title":title}.items() if v}]; mask.append("organizations")
    loc=_best(a.get("location_city",[])); ctry=_best(a.get("location_country",[]))
    if loc and not ctry and ", " in loc: loc,ctry=loc.rsplit(", ",1)
    if loc or ctry:
        body["addresses"]=[{k:v for k,v in {"city":loc,"country":ctry,"formattedValue":", ".join(x for x in [loc,ctry] if x)}.items() if v}]; mask.append("addresses")
    if _best(a.get("birthday",[])):
        body["birthdays"]=[{"text":_best(a["birthday"])}]; mask.append("birthdays")
    urls=_extract_urls(a)
    if urls: body["urls"]=urls; mask.append("urls")
    rels=[{"person":n,"type":t} for t,n in p.get("relations",[]) if n and t.lower() not in ("knows",)]
    if rels: body["relations"]=rels[:15]; mask.append("relations")
    return body, ",".join(mask)
