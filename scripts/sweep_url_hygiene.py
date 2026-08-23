#!/usr/bin/env python3
"""Normalise every contact URL in Google Contacts and remove duplicates.

Google holds 3,829 URLs of which ~930 are the same link twice on one contact
(www vs non-www, http vs https, trailing slash). A handful of url slots hold a
plain email address, which belongs in the email field.

Per contact:
  - rewrite every url to canonical form (https, no www, no trailing slash, no
    tracking params, folded handle path)
  - drop duplicates, keeping the first occurrence and its label
  - move an email found in a url slot into emailAddresses if it is not already
    there, then drop it from urls

'urls' is a masked field, so the list we send replaces google's: the script
refuses to send any contact whose outgoing key set is not a superset of the
incoming one (minus deliberate email moves). Dry-run by default.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as G  # noqa: E402
from url_norm import canonical_url, dedupe_key  # noqa: E402

API = "https://people.googleapis.com/v1"
FIELDS = "names,urls,emailAddresses,metadata"


def fetch_all(tok):
    people, page = [], None
    while True:
        q = {"personFields": FIELDS, "pageSize": 1000,
             "sources": "READ_SOURCE_TYPE_CONTACT"}
        if page:
            q["pageToken"] = page
        rq = urllib.request.Request(API + "/people/me/connections?" + urllib.parse.urlencode(q),
                                    headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(rq, timeout=60) as r:
            d = json.loads(r.read())
        people.extend(d.get("connections", []))
        page = d.get("nextPageToken")
        if not page:
            break
    return people


def strip_meta(items):
    return [{k: v for k, v in dict(i).items() if k != "metadata"} for i in (items or [])]


def plan_one(p):
    """-> (body, note) or (None, None) when the contact is already clean."""
    urls = strip_meta(p.get("urls"))
    if not urls:
        return None, None
    emails = strip_meta(p.get("emailAddresses"))
    have_email = {str(e.get("value") or "").strip().lower() for e in emails}

    out, seen, moved, dropped, changed = [], set(), [], 0, False
    for u in urls:
        raw = str(u.get("value") or "").strip()
        c = canonical_url(raw)
        if not c:
            # not a URL. If it is an email, it belongs in emailAddresses.
            if "@" in raw and "." in raw.split("@")[-1]:
                if raw.lower() not in have_email:
                    emails.append({"value": raw, "type": "other"})
                    have_email.add(raw.lower())
                    moved.append(raw)
                dropped += 1
                changed = True
                continue
            # unparseable junk: leave it alone rather than silently deleting
            out.append(u)
            continue
        k = c.lower()
        if k in seen:
            dropped += 1
            changed = True
            continue
        seen.add(k)
        if c != raw:
            changed = True
        e = dict(u)
        e["value"] = c
        out.append(e)

    if not changed:
        return None, None
    body = {"urls": out}
    note = {"dropped": dropped, "moved": moved}
    if moved:
        body["emailAddresses"] = emails
    return body, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    tok = G.get_access_token()
    people = fetch_all(tok)
    print("contacts: %d" % len(people))

    plans = []
    for p in people:
        body, note = plan_one(p)
        if not body:
            continue
        # safety: nothing google holds may vanish except a deliberate email move
        before = {dedupe_key(u.get("value")) for u in (p.get("urls") or [])}
        before.discard(None)
        after = {dedupe_key(u.get("value")) for u in body["urls"]}
        after.discard(None)
        if before - after:
            print("  REFUSE %s: would drop %s"
                  % ((p.get("names") or [{}])[0].get("displayName", "?"), before - after))
            continue
        plans.append((p, body, note))
        if a.limit and len(plans) >= a.limit:
            break

    tot_dropped = sum(n["dropped"] for _p, _b, n in plans)
    tot_moved = sum(len(n["moved"]) for _p, _b, n in plans)
    print("contacts needing changes: %d" % len(plans))
    print("  duplicate/junk url entries removed: %d" % tot_dropped)
    print("  emails moved out of url slots     : %d" % tot_moved)
    for p, b, n in plans[:10]:
        nm = (p.get("names") or [{}])[0].get("displayName", "?")
        old = [u.get("value") for u in (p.get("urls") or [])]
        new = [u.get("value") for u in b["urls"]]
        if old != new:
            print("   %-22s" % nm[:22])
            for o in old:
                print("        - %s" % o[:66])
            for x in new:
                print("        + %s" % x[:66])
            if n["moved"]:
                print("        -> email: %s" % ", ".join(n["moved"]))

    if not a.apply:
        print("\ndry run; pass --apply to write")
        return

    # group by the exact field set: one mask governs a whole request and clears
    # any masked field a contact in it omits
    groups = {}
    for p, b, _n in plans:
        groups.setdefault(tuple(sorted(b.keys())), []).append((p, b))
    written = failed = 0
    for sig, rows in groups.items():
        mask = ",".join(sig)
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            contacts = {}
            for p, b in chunk:
                body = dict(b)
                body["etag"] = p.get("etag")
                contacts[p["resourceName"]] = body
            req = {"contacts": contacts, "updateMask": mask, "readMask": mask}
            try:
                resp = G._api_post(API + "/people:batchUpdateContacts", tok, req, timeout=120)
                for rn, res in (resp.get("updateResult") or {}).items():
                    if (res.get("status") or {}).get("code"):
                        failed += 1
                    else:
                        written += 1
            except Exception as e:  # noqa: BLE001
                failed += len(chunk)
                print("  batch error: %s: %s" % (type(e).__name__, e))
            time.sleep(0.5)
    print("written=%d failed=%d" % (written, failed))


if __name__ == "__main__":
    main()
