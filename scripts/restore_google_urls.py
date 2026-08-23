"""Put back URLs removed by a purge run that over-rejected.

The slug-vs-name rule threw away real handles (github.com/brainwane is Sumana
Harihareswara's), so the run has to be undone on both sides. Weave was restored
from the audit's fact ids; this restores google's url lists, merging the removed
values back in rather than replacing (other runs may have edited since).
"""
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402
from url_norm import canonical_url, dedupe_key  # noqa: E402

API = "https://people.googleapis.com/v1"
audit = sys.argv[1] if len(sys.argv) > 1 else sorted(
    glob.glob("/root/.hermes/profiles/indigo/commons/data/ocas-weave/junk-urls-*.json"),
    key=os.path.getmtime)[-1]
d = json.load(open(audit))
want = {g["resourceName"]: [u for u in g["removed"]] for g in d["google_removed"]}
print("  audit: %s" % os.path.basename(audit))
print("  contacts to restore: %d (%d urls)" % (len(want), sum(len(v) for v in want.values())))

tok = G.get_access_token()
people, page = [], None
while True:
    q = {"personFields": "names,urls,metadata", "pageSize": 1000,
         "sources": "READ_SOURCE_TYPE_CONTACT"}
    if page:
        q["pageToken"] = page
    rq = urllib.request.Request(API + "/people/me/connections?" + urllib.parse.urlencode(q),
                                headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(rq, timeout=60) as r:
        j = json.loads(r.read())
    people.extend(j.get("connections", []))
    page = j.get("nextPageToken")
    if not page:
        break

plans = []
for p in people:
    add = want.get(p["resourceName"])
    if not add:
        continue
    cur = [{k: v for k, v in u.items() if k != "metadata"} for u in (p.get("urls") or [])]
    have = {dedupe_key(u.get("value")) for u in cur}
    new = list(cur)
    for v in add:
        c = canonical_url(v)
        if c and dedupe_key(c) not in have:
            new.append({"value": c})
            have.add(dedupe_key(c))
    if len(new) != len(cur):
        plans.append((p, new))
print("  contacts actually needing the url back: %d" % len(plans))

written = failed = 0
for i in range(0, len(plans), 200):
    chunk = plans[i:i + 200]
    contacts = {p["resourceName"]: {"etag": p.get("etag"), "urls": urls}
                for p, urls in chunk}
    try:
        resp = G._api_post(API + "/people:batchUpdateContacts", tok,
                           {"contacts": contacts, "updateMask": "urls",
                            "readMask": "urls"}, timeout=120)
        for _rn, res in (resp.get("updateResult") or {}).items():
            if (res.get("status") or {}).get("code"):
                failed += 1
            else:
                written += 1
    except Exception as e:  # noqa: BLE001
        failed += len(chunk)
        print("  batch error: %s: %s" % (type(e).__name__, e))
    time.sleep(0.5)
print("restored=%d failed=%d" % (written, failed))
