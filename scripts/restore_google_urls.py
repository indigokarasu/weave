"""Put back URLs removed by a purge run that over-rejected.

The slug-vs-name rule threw away real handles (github.com/brainwane is Sumana
Harihareswara's), so the run has to be undone on both sides. Weave was restored
from the audit's fact ids; this restores google's url lists, merging the removed
values back in rather than replacing (other runs may have edited since).
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERMES_HOME = os.environ.get(
    "HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo"))

_P = argparse.ArgumentParser(
    prog="restore_google_urls.py",
    description="Undo a junk-URL purge that over-rejected: re-merge removed URLs into "
                "Google Contacts. MERGES, never replaces (other runs may have edited since). "
                "Destructive writeback — read references/database_maintenance.md first.",
)
_P.add_argument("audit", nargs="?", default=None,
                help="junk-urls-*.json audit file to replay (default: newest under "
                     "$HERMES_HOME/commons/data/ocas-weave/)")
_P.add_argument("--dry-run", action="store_true",
                help="list contacts that would be restored and exit without writing (default)")
_P.add_argument("--apply", action="store_true",
                help="actually PATCH Google Contacts (requires both --apply and --dry-run absent)")
_P.add_argument("--limit", type=int, default=0,
                help="cap the number of contacts written (0 = no cap; use for staged runs)")
_P.add_argument("--batch", type=int, default=200,
                help="contacts per batchUpdateContacts call (default 200)")
args = _P.parse_args()

sys.path.insert(0, os.path.join(HERMES_HOME, "skills/ocas-weave/scripts"))
import google_sync as G  # noqa: E402
from url_norm import canonical_url, dedupe_key  # noqa: E402

API = "https://people.googleapis.com/v1"
audit = args.audit
if audit is None:
    candidates = sorted(
        glob.glob(os.path.join(HERMES_HOME, "commons/data/ocas-weave/junk-urls-*.json")),
        key=os.path.getmtime)
    if not candidates:
        print("no junk-urls-*.json audit file under %s — nothing to restore"
              % os.path.join(HERMES_HOME, "commons/data/ocas-weave"), file=sys.stderr)
        sys.exit(2)
    audit = candidates[-1]
if not os.path.exists(audit):
    print("audit file not found: %s" % audit, file=sys.stderr)
    sys.exit(2)
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

if args.limit:
    plans = plans[:args.limit]
if not args.apply:
    print("  DRY RUN — %d contacts would be restored. Re-run with --apply to write."
          % len(plans))
    sys.exit(0)
if not plans:
    print("  nothing to restore (all removed URLs already present)")
    sys.exit(0)

written = failed = 0
for i in range(0, len(plans), args.batch):
    chunk = plans[i:i + args.batch]
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
sys.exit(1 if failed else 0)
