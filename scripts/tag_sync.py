#!/usr/bin/env python3
"""
Bi-directional tag/label sync: Google contactGroups <-> weave book_tags/book_contact_tags.

ADDITIVE ONLY (union semantics).
  * a group that exists only in Google  -> create the weave tag + its memberships
  * a tag that exists only in weave     -> create the Google group + add its members
  * a name present on both sides        -> add the missing memberships in BOTH directions
Nothing is ever deleted and no member is ever removed from either side. Anything
that would need a removal (or a rename) to truly converge is printed under
DIVERGENCE and left alone.

Only USER_CONTACT_GROUP groups participate. Google system groups (myContacts,
starred, chatBuddies, all, friends, family, coworkers, blocked) are skipped.
BOOK system tags (Favorites/Archived/Deceased/Pseudo -- see server/lib/tagPolicy.ts)
are skipped too: they are projections of persons.is_* flags, not user labels.

Matching rule: tags/groups are matched case-insensitively on the trimmed name
(this is exactly BOOK's own uniqueness rule, `lower(name)=lower($name)` in
server/routes/tags.ts). When a match is found, NEITHER side is renamed -- a
rename is a destructive edit -- and any casing difference is reported.

Google writes are limited to contactGroups create + members:modify(resourceNamesToAdd).
This script NEVER sends updatePersonFields and never touches names, organizations,
addresses or phoneNumbers.

Dry run by default. --apply writes. --limit N caps member additions per direction
(canary). --seed-company additionally ensures a Google group named exactly
'Company' and seeds it from the classification already produced by
company_label.py (~/work/company-label/plan.json) -- it does not reclassify.
"""
import os as _os
_PROF = _os.environ.get("HERMES_HOME") or _os.path.join(
    _os.path.expanduser("~"), ".hermes", "profiles", "indigo")
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import uuid
import zlib
from datetime import datetime, timezone

_HELP = {"--help", "-h"}

SCRIPTS = f"{_PROF}/skills/ocas-weave/scripts"
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

DB = f"{_PROF}/commons/db/ocas-weave/weave.sqlite"
PLAN = f"{_os.path.expanduser(chr(126))}/work/company-label/plan.json"
COMPANY_TAG = "Company"

# server/lib/tagPolicy.ts -- SYSTEM_TAG_TYPES
SYSTEM_TAG_NAMES = {"favorites", "archived", "deceased", "pseudo"}
# server/lib/tagPolicy.ts -- RESERVED_TEST_TAG_PREFIXES
RESERVED_PREFIXES = ("contract-", "dupe-check-", "dup-check-", "merge-tag-",
                     "orphan-add-", "orphan-remove-", "unattached-")
MAX_TAG_NAME = 64            # server/routes/tags.ts
MAX_COLOR_INDEX = 6          # server/routes/tags.ts clampColorIndex
MEMBERS_MODIFY_CHUNK = 400   # People API caps a members:modify at 1000


def key(name):
    return (name or "").strip().lower()


def now_iso():
    # matches BOOK's `new Date().toISOString()`
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        "%03dZ" % (datetime.now(timezone.utc).microsecond // 1000)


def color_for(name):
    return zlib.crc32(key(name).encode()) % (MAX_COLOR_INDEX + 1)


def reserved(name):
    return key(name).startswith(RESERVED_PREFIXES)


def refusal(name):
    """None if the name may participate, else the reason it may not."""
    n = (name or "").strip()
    if not n:
        return "empty name"
    if key(n) in SYSTEM_TAG_NAMES:
        return ("collides with a BOOK system tag (%s) -- system tags mirror "
                "persons.is_* flags and are not user labels" % key(n))
    if reserved(n):
        return "reserved test-only tag prefix (BOOK createTag returns 400)"
    if len(n) > MAX_TAG_NAME:
        return "name longer than BOOK's %d-char limit" % MAX_TAG_NAME
    return None


# ------------------------------------------------------------------ Google

def _retry(fn, *a, **kw):
    backoff = 5.0
    for i in range(6):
        try:
            return fn(*a, **kw)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < 5:
                print("    HTTP %d -> sleep %.0fs" % (e.code, backoff))
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            raise


def g_get(url, token):
    from google_api import api_get
    return _retry(api_get, url, token, timeout=60, max_retries=1)


def g_post(url, token, body):
    from google_api import api_post
    return _retry(api_post, url, token, body, timeout=60)


def google_user_groups(token, base):
    """All USER_CONTACT_GROUPs with their live member resourceNames."""
    groups, page = [], None
    while True:
        url = base + "/contactGroups?pageSize=200&groupFields=name,groupType,memberCount"
        if page:
            url += "&pageToken=" + urllib.parse.quote(page)
        d = g_get(url, token)
        groups.extend(d.get("contactGroups") or [])
        page = d.get("nextPageToken")
        if not page:
            break
    out, skipped = [], []
    for g in groups:
        if g.get("groupType") != "USER_CONTACT_GROUP":
            skipped.append((g.get("name"), g.get("groupType")))
            continue
        d = g_get("%s/%s?maxMembers=5000" % (base, g["resourceName"]), token)
        out.append({"resourceName": g["resourceName"],
                    "name": (g.get("name") or "").strip(),
                    "members": list(d.get("memberResourceNames") or []),
                    "pending": False})
        time.sleep(0.4)
    return out, skipped


def ensure_group(token, base, name, apply_):
    """Return (resourceName|None, created). Case-insensitive lookup."""
    d = g_get(base + "/contactGroups?pageSize=200&groupFields=name,groupType", token)
    for g in d.get("contactGroups") or []:
        if key(g.get("name")) == key(name):
            return g["resourceName"], False
    if not apply_:
        return None, False
    g = g_post(base + "/contactGroups", token, {"contactGroup": {"name": name}})
    return g["resourceName"], True


def add_members(token, base, group_rn, rns):
    """POST members:modify with resourceNamesToAdd only. Returns notFound list."""
    not_found = []
    for i in range(0, len(rns), MEMBERS_MODIFY_CHUNK):
        chunk = rns[i:i + MEMBERS_MODIFY_CHUNK]
        res = g_post("%s/%s/members:modify" % (base, group_rn), token,
                     {"resourceNamesToAdd": chunk})
        not_found.extend(res.get("notFoundResourceNames") or [])
        time.sleep(1.0)
    return not_found


# ------------------------------------------------------------------- weave

def weave_state(conn):
    """tags -> {key: {id,name,color_index,is_system,members:set(person_id)}},
       plus grn<->person maps for live (valid_until IS NULL) rows."""
    tags = {}
    for tid, name, ci, issys in conn.execute(
            "SELECT id,name,color_index,is_system FROM book_tags"):
        tags.setdefault(key(name), []).append(
            {"id": tid, "name": name, "color_index": ci,
             "is_system": issys, "members": set()})
    by_id = {t["id"]: t for lst in tags.values() for t in lst}
    for cid, tid in conn.execute("SELECT contact_id,tag_id FROM book_contact_tags"):
        if tid in by_id:
            by_id[tid]["members"].add(cid)

    grn2pid, pid2grn = {}, {}
    dead = 0
    for pid, grn, vu in conn.execute(
            "SELECT id,google_resource_name,valid_until FROM persons"):
        g = (grn or "").strip()
        if vu is not None:
            dead += 1
            continue
        if not g:
            continue
        grn2pid.setdefault(g, []).append(pid)
        pid2grn[pid] = g
    return tags, grn2pid, pid2grn, dead


def ensure_tag(conn, name, apply_):
    row = conn.execute("SELECT id,name FROM book_tags WHERE lower(name)=lower(?)",
                       (name,)).fetchone()
    if row:
        return row[0], False
    if not apply_:
        return None, False
    tid = str(uuid.uuid4())
    ts = now_iso()
    conn.execute("INSERT INTO book_tags(id,name,color_index,is_system,"
                 "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                 (tid, name.strip()[:MAX_TAG_NAME], color_for(name), 0, ts, ts))
    return tid, True


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap member additions per direction (canary)")
    ap.add_argument("--seed-company", action="store_true",
                    help="also create the 'Company' Google group and seed it "
                         "from company_label.py's plan.json")
    ap.add_argument("--only", action="append", default=[],
                    help="restrict the APPLY phase to these tag names "
                         "(case-insensitive, repeatable). Reporting stays full.")
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()

    from google_api import get_access_token, PEOPLE_API_BASE as BASE
    token = get_access_token()
    conn = sqlite3.connect(args.db)
    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 100)
    print("TAG SYNC  [%s]%s   %s" % (mode,
          ("  limit=%d" % args.limit) if args.limit else "", now_iso()))
    print("=" * 100)

    # ---------------------------------------------------------- seed Company
    company_rns, company_pending = [], False
    if args.seed_company:
        plan = json.load(open(args.plan))
        # order only: put weave-backed contacts first so a --limit 1 canary
        # exercises the whole round trip (group create -> member -> weave tag
        # -> weave membership). The full run writes the same set either way.
        plan = sorted(plan, key=lambda e: 0 if e.get("weave_ids") else 1)
        company_rns = [e["rn"] for e in plan if e.get("rn")]
        print("\n## SEED '%s' (from %s -- %d contacts classified as companies)"
              % (COMPANY_TAG, args.plan, len(company_rns)))
        grn, created = ensure_group(token, BASE, COMPANY_TAG, args.apply)
        if grn and not created:
            print("   google group already exists: %s" % grn)
        elif created:
            print("   CREATED google group %r -> %s" % (COMPANY_TAG, grn))
        else:
            print("   would CREATE google group %r" % COMPANY_TAG)
            company_pending = True
        if args.apply and grn:
            todo = company_rns[:args.limit] if args.limit else company_rns
            nf = add_members(token, BASE, grn, todo)
            print("   added %d/%d contacts to %s%s"
                  % (len(todo) - len(nf), len(company_rns), grn,
                     ("  notFound=%s" % nf) if nf else ""))
        elif not args.apply:
            print("   would add %d contacts as members"
                  % (min(args.limit, len(company_rns)) if args.limit else len(company_rns)))

    # ------------------------------------------------------------ read state
    ggroups, gskipped = google_user_groups(token, BASE)
    if company_pending:
        ggroups.append({"resourceName": None, "name": COMPANY_TAG,
                        "members": list(company_rns), "pending": True})
    wtags, grn2pid, pid2grn, dead = weave_state(conn)

    print("\n## SIDES")
    print("   google USER groups : %d   (skipped %d SYSTEM: %s)"
          % (len(ggroups), len(gskipped), ", ".join(n for n, _ in gskipped)))
    print("   weave book_tags    : %d   (%d system, %d user)"
          % (sum(len(v) for v in wtags.values()),
             sum(1 for v in wtags.values() for t in v if t["is_system"]),
             sum(1 for v in wtags.values() for t in v if not t["is_system"])))
    print("   weave live persons with google_resource_name: %d  (%d rows "
          "skipped: valid_until set)" % (len(grn2pid), dead))

    gby = {key(g["name"]): g for g in ggroups}
    dup_g = [g["name"] for g in ggroups if len([x for x in ggroups
             if key(x["name"]) == key(g["name"])]) > 1]

    divergence = []
    google_only, weave_only, both = [], [], []
    for k in sorted(set(gby) | set(wtags)):
        g = gby.get(k)
        ws = [t for t in wtags.get(k, [])]
        if g and not ws:
            google_only.append(k)
        elif ws and not g:
            weave_only.append(k)
        else:
            both.append(k)

    # ------------------------------------------------------- reconcile table
    print("\n## RECONCILIATION  (per tag: google members / weave members / "
          "adds needed)")
    print("%-24s %-11s %7s %7s %8s %8s  %s"
          % ("NAME", "SIDE", "GOOGLE", "WEAVE", "->WEAVE", "->GOOGLE", "NOTE"))
    print("-" * 100)

    plan_to_weave = []   # (tag_key, google_name, [person_id])
    plan_to_google = []  # (tag_key, weave_name, group_rn|None, [rn])

    for k in sorted(set(gby) | set(wtags)):
        g = gby.get(k)
        ws = wtags.get(k, [])
        side = "both" if (g and ws) else ("google-only" if g else "weave-only")
        gname = g["name"] if g else ws[0]["name"]
        gmem = set(g["members"]) if g else set()
        wmem = set()
        for t in ws:
            wmem |= t["members"]
        note = []

        why = refusal(gname if g else ws[0]["name"])
        if why:
            print("%-24s %-11s %7d %7d %8s %8s  SKIPPED: %s"
                  % (gname[:24], side, len(gmem), len(wmem), "-", "-", why))
            divergence.append("tag %r skipped: %s" % (gname, why))
            continue
        if ws and any(t["is_system"] for t in ws):
            print("%-24s %-11s %7d %7d %8s %8s  SKIPPED: weave tag is_system=1"
                  % (gname[:24], side, len(gmem), len(wmem), "-", "-"))
            divergence.append("tag %r skipped: weave row has is_system=1" % gname)
            continue
        if len(ws) > 1:
            divergence.append("weave has %d book_tags rows for %r (ids %s) -- "
                              "merging them would need a delete"
                              % (len(ws), k, [t["id"][:8] for t in ws]))
            note.append("%d weave rows" % len(ws))
        if g and ws and g["name"] != ws[0]["name"]:
            divergence.append("case mismatch: google %r vs weave %r -- matched as "
                              "the same tag; renaming either side would be a "
                              "destructive edit, so neither was renamed"
                              % (g["name"], ws[0]["name"]))
            note.append("case: G%r/W%r" % (g["name"], ws[0]["name"]))

        # google -> weave
        add_w, orphan_g = [], []
        for rn in sorted(gmem):
            pids = grn2pid.get(rn)
            if not pids:
                orphan_g.append(rn)
                continue
            for pid in pids:
                if pid not in wmem:
                    add_w.append(pid)
        if orphan_g:
            note.append("%d google members have no weave row" % len(orphan_g))
            divergence.append("group %r: %d member(s) have no weave persons row "
                              "(no row invented): %s"
                              % (gname, len(orphan_g),
                                 ", ".join(orphan_g[:5]) +
                                 (" ..." if len(orphan_g) > 5 else "")))

        # weave -> google
        add_g, orphan_w = [], []
        for pid in sorted(wmem):
            rn = pid2grn.get(pid)
            if not rn:
                orphan_w.append(pid)
                continue
            if rn not in gmem and rn not in add_g:
                add_g.append(rn)
        if orphan_w:
            note.append("%d weave members have no google contact" % len(orphan_w))
            divergence.append("tag %r: %d weave member(s) have no live "
                              "google_resource_name, cannot be pushed: %s"
                              % (gname, len(orphan_w), orphan_w[:5]))

        print("%-24s %-11s %7d %7d %8d %8d  %s"
              % (gname[:24], side, len(gmem), len(wmem), len(add_w), len(add_g),
                 "; ".join(note)))

        if add_w:
            plan_to_weave.append((k, gname, add_w))
        if add_g:
            plan_to_google.append((k, gname, g["resourceName"] if g else None, add_g))

    print("-" * 100)
    print("google-only: %d %s" % (len(google_only), sorted(google_only)))
    print("weave-only : %d %s" % (len(weave_only), sorted(weave_only)))
    print("both       : %d %s" % (len(both), sorted(both)))
    if dup_g:
        divergence.append("google has case-duplicate group names: %s" % sorted(set(dup_g)))

    tot_w = sum(len(x[2]) for x in plan_to_weave)
    tot_g = sum(len(x[3]) for x in plan_to_google)
    print("\nPLANNED WRITES: %d weave membership row(s), %d google membership "
          "addition(s), %d weave tag(s) to create, %d google group(s) to create"
          % (tot_w, tot_g,
             len([k for k, _, _ in plan_to_weave if k not in wtags]),
             len([1 for _, _, rn, _ in plan_to_google if rn is None])))

    if divergence:
        print("\n## DIVERGENCE (reported, NOT acted on -- each would need a "
              "removal/rename/invented row)")
        for d in divergence:
            print("   - %s" % d)

    if not args.apply:
        print("\nDRY RUN -- nothing written to Google or to weave.")
        conn.close()
        return

    # ------------------------------------------------------------ apply
    only = {key(x) for x in args.only}
    print("\n## APPLY%s" % ("  (scoped to %s)" % sorted(only) if only else ""))
    if only:
        held_w = [(k, n, p) for k, n, p in plan_to_weave if k not in only]
        held_g = [(k, n, r, p) for k, n, r, p in plan_to_google if k not in only]
        for k, n, p in held_w:
            print("   HELD (not in --only): weave %r  %d membership row(s) pending"
                  % (n, len(p)))
        for k, n, r, p in held_g:
            print("   HELD (not in --only): google %r  %d member(s) pending" % (n, len(p)))
        plan_to_weave = [x for x in plan_to_weave if x[0] in only]
        plan_to_google = [x for x in plan_to_google if x[0] in only]
    n_w = n_g = 0
    for k, name, pids in plan_to_weave:
        todo = pids[:args.limit] if args.limit else pids
        tid, created = ensure_tag(conn, name, True)
        if created:
            print("   weave tag CREATED %r -> %s (color_index=%d)"
                  % (name, tid, color_for(name)))
        ts = now_iso()
        for pid in todo:
            conn.execute("INSERT OR IGNORE INTO book_contact_tags(contact_id,"
                         "tag_id,created_at) VALUES(?,?,?)", (pid, tid, ts))
            n_w += 1
        conn.commit()
        print("   weave %r: +%d membership row(s) (of %d)" % (name, len(todo), len(pids)))

    for k, name, group_rn, rns in plan_to_google:
        todo = rns[:args.limit] if args.limit else rns
        if group_rn is None:
            group_rn, created = ensure_group(token, BASE, name, True)
            print("   google group CREATED %r -> %s" % (name, group_rn))
        nf = add_members(token, BASE, group_rn, todo)
        n_g += len(todo) - len(nf)
        print("   google %r: +%d member(s) (of %d)%s"
              % (name, len(todo) - len(nf), len(rns),
                 ("  notFound=%s" % nf) if nf else ""))

    conn.commit()
    conn.close()
    print("\nAPPLIED: %d weave membership rows, %d google memberships." % (n_w, n_g))


if set(sys.argv[1:]) & _HELP and len(sys.argv) == 2:
    print((__doc__ or "").strip())
    sys.exit(0)

if __name__ == "__main__":
    main()

