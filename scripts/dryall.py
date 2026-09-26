#!/usr/bin/env python3
"""Dry-run analysis of every Google-linked Weave contact's outbound payload.

Read-only: builds each contact's People-API payload via people_to_google and
reports field coverage, anomalies, and sample payloads. Writes nothing, calls
no Google API. Run this BEFORE any google_sync --apply to see what would ship.

Exit codes: 0 = no anomalies, 1 = anomalies found, 2 = DB missing/unreadable.
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter

HERMES_HOME = os.environ.get(
    "HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo"))

_P = argparse.ArgumentParser(
    prog="dryall.py",
    description="Dry-run every Google-linked contact: field coverage, anomaly flags, "
                "sample payloads. Read-only, no network. Run before google_sync --apply.",
)
_P.add_argument("--db", default=None,
                help="people.db path (default: $HERMES_HOME/commons/db/people/people.db)")
_P.add_argument("--samples", type=int, default=4,
                help="how many sample payloads to print (default 4)")
_P.add_argument("--since", default="2026-06",
                help="only count identifiers/attributes valid after this YYYY-MM "
                     "(default 2026-06; mirrors the Google sync watermark)")
args = _P.parse_args()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_to_google import build_google_payload  # noqa: E402

DB = args.db or os.path.join(HERMES_HOME, "commons/db/people/people.db")
if not os.path.exists(DB):
    print("people.db not found: %s" % DB, file=sys.stderr)
    print("set --db, or run google_sync first to create it", file=sys.stderr)
    sys.exit(2)

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
gl = db.execute("SELECT DISTINCT person_id FROM external_refs WHERE system='google'").fetchall()

stat = Counter()
samples = []
anomalies = []


def cur(pid, kind):
    return [r[0] for r in db.execute(
        f"SELECT value FROM identifiers WHERE person_id=? AND kind=? "
        f"AND (valid_until IS NULL OR valid_until>?)", (pid, kind, args.since))]


for (pid,) in gl:
    pr = db.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone()
    if not pr:
        anomalies.append(f"person {pid}: external_refs row with no people row (orphan)")
        continue
    attrs = {}
    for r in db.execute(
            "SELECT key,value FROM attributes WHERE person_id=? "
            "AND (valid_until IS NULL OR valid_until>?)", (pid, args.since)):
        attrs.setdefault(r[0], []).append(r[1])
    p = {"display_name": pr["display_name"], "given_name": pr["given_name"],
         "family_name": pr["family_name"],
         "emails": [{"value": e, "label": "work"} for e in cur(pid, "email")],
         "phones": [{"value": ph, "label": "mobile"} for ph in cur(pid, "phone")],
         "attrs": attrs, "relations": []}
    body, mask = build_google_payload(p)
    for f in mask.split(","):
        stat[f] += 1
    stat["TOTAL"] += 1
    # anomaly checks
    nurls = len(body.get("urls", []))
    if nurls > 6:
        anomalies.append(f"{pr['display_name']}: {nurls} urls (People API caps at 30, "
                         f">6 is usually a scrape artifact)")
    org = body.get("organizations", [{}])[0]
    if org.get("title") and len(org["title"]) > 60:
        anomalies.append(f"{pr['display_name']}: job title {len(org['title'])} chars (>60)")
    if "biographies" in body or "userDefined" in body:
        anomalies.append(f"{pr['display_name']}: NOTES/CUSTOM field leaked into payload")
    if len(samples) < args.samples and nurls >= 2:
        samples.append((pr["display_name"], mask,
                        [(u["type"], u["value"][:40]) for u in body.get("urls", [])]))

print(f"contacts to sync: {stat['TOTAL']}")
print("field coverage:", {k: v for k, v in stat.items() if k != "TOTAL"})
print(f"\nanomalies: {len(anomalies)}")
for a in anomalies[:10]:
    print("  ", a)
if len(anomalies) > 10:
    print(f"   ... and {len(anomalies)-10} more")
print("\nsamples:")
for n, m, u in samples:
    print(f"  {n}: [{m}]  urls={u}")
db.close()
sys.exit(1 if anomalies else 0)
