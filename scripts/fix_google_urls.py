#!/usr/bin/env python3
import os
"""Re-canonicalise and de-duplicate the urls on GOOGLE contacts.

weave was repaired already; Google still holds the damage. Two things are wrong
there: urls that kept the closing brace of the JSON blob they were parsed out of
('https://github.com/xiomythemoney}'), and the same link listed twice because a
generic and a specific predicate each pushed it.

Only rewrites a contact when its url list actually changes, so a rerun is a
no-op, and never adds or removes a link -- it repairs spelling and collapses
exact repeats.
"""
import argparse, json, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
import google_sync as G
from url_norm import canonical_url, dedupe_key

API = "https://people.googleapis.com/v1"
ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
a = ap.parse_args()
tok = G.get_access_token()

def fetch_all():
    out, page = [], None
    while True:
        q = {"personFields": "names,urls,metadata", "pageSize": 200,
             "sources": "READ_SOURCE_TYPE_CONTACT"}
        if page: q["pageToken"] = page
        for attempt in range(6):
            try:
                rq = urllib.request.Request(API + "/people/me/connections?" +
                                            urllib.parse.urlencode(q),
                                            headers={"Authorization": "Bearer " + tok})
                d = json.loads(urllib.request.urlopen(rq, timeout=60).read()); break
            except Exception as e:
                if "429" in str(e) and attempt < 5:
                    time.sleep(10 * (attempt + 1)); continue
                raise
        out.extend(d.get("connections", []))
        page = d.get("nextPageToken")
        if not page: return out

people = fetch_all()
print("  google contacts read: %d" % len(people))
plans = []
for p in people:
    urls = p.get("urls") or []
    if not urls: continue
    fixed, seen = [], set()
    for u in urls:
        raw = (u.get("value") or "").strip()
        cv = canonical_url(raw) or raw
        k = dedupe_key(cv) or cv.lower()
        if not cv or k in seen: continue
        seen.add(k)
        e = {kk: vv for kk, vv in u.items() if kk != "metadata"}
        e["value"] = cv
        fixed.append(e)
    if [x.get("value") for x in fixed] != [(u.get("value") or "").strip() for u in urls]:
        plans.append((p, urls, fixed))

print("  contacts whose url list changes: %d" % len(plans))
shown = 0
for p, before, after in plans:
    if shown >= 10: break
    nm = ((p.get("names") or [{}])[0].get("displayName") or "?")
    print("     %-24s %d -> %d urls" % (str(nm)[:24], len(before), len(after)))
    for b in before:
        bv = (b.get("value") or "").strip()
        if bv not in [x["value"] for x in after]:
            print("          drop/fix: %r" % bv[:56])
    shown += 1

if not a.apply:
    print("\ndry run; pass --apply to write"); raise SystemExit

ok = fail = 0
for i in range(0, len(plans), 100):
    chunk = plans[i:i+100]
    body = {"contacts": {p["resourceName"]: {"etag": p.get("etag"), "urls": f}
                         for p, _b, f in chunk},
            "updateMask": "urls", "readMask": "urls"}
    for attempt in range(6):
        try:
            resp = G._api_post(API + "/people:batchUpdateContacts", tok, body, timeout=180)
            for _rn, r in (resp.get("updateResult") or {}).items():
                if (r.get("status") or {}).get("code"): fail += 1
                else: ok += 1
            break
        except Exception as e:
            if "429" in str(e) and attempt < 5:
                time.sleep(15 * (attempt + 1)); continue
            print("  batch error: %s" % str(e)[:120]); fail += len(chunk); break
    time.sleep(2)
print("\n  google updated=%d failed=%d" % (ok, fail))
