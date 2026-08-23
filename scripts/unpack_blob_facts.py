#!/usr/bin/env python3
"""Facts whose value is a whole serialized payload instead of one statement.

A fact is supposed to say one thing: predicate 'org', value 'Microsoft
Research'. 204 rows instead hold an entire enrichment result --

    predicate 'enrichment'
    value     {"occupation": "Principal Applied Scientist",
               "org": "Microsoft Research", "location_city": null,
               "pipeline": "overnight_enrichment_v2"}

-- so nothing can read them. No query asks for predicate 'enrichment', and the
org inside is invisible to every consumer. They also corrupt anything that scans
fact values: the corroboration check matched one of these blobs as a "profile
url" because the JSON contains a source_ref that contains an https://.

They are legacy -- the newest is 2026-07-12 and the current pipeline writes
proper predicates -- so this is a one-off repair, not a live bug.

Each payload is unpacked into the facts it was always meant to be, keeping its
original source and timestamp, and the wrapper is retired with valid_until. The
same junk classifiers that cleaned the live data are applied on the way in, so
a legacy guess that would be rejected today is not admitted through the back
door. Contact records are not touched: this changes the graph's shape, not what
the address book shows.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
from contact_urls import classify_url            # noqa: E402
from job_junk_v3 import classify as classify_job  # noqa: E402
from org_junk import classify_org                 # noqa: E402

DB = "/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite"
AUDIT_DIR = "/root/.hermes/profiles/indigo/commons/data/ocas-weave"

# Keys that describe the RUN, not the person. They belong in a log, not a fact.
META_KEYS = {"pipeline", "pipeline_stages", "_pipeline_stages", "confidence",
             "source_type", "source_ref", "run_date", "enrichment_run",
             "fields", "sources", "notes", "evidence", "name"}
# Payload key -> the predicate it should have been written as.
FIELD_MAP = {"occupation": "occupation", "org": "org",
             "location_city": "location_city", "linkedin_location": "location_city"}

_URL_RE = re.compile(r"https?://[^\s,'\"\]\}<>]+")


def parse_payload(raw):
    """Best-effort structure out of a value that may not be valid JSON.

    The social_* rows were written with str() rather than json.dumps, so keys
    and values are unquoted: `[https://x]`, `{platform: GitHub, ...}`. Only URLs
    are wanted from those, and a regex reads them out of any of these shapes.
    """
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            # One writer nested the real payload one level down under 'fields'.
            # Treating that key as run metadata silently dropped every field it
            # held, so lift it before anything else looks at the dict.
            inner = d.get("fields")
            if isinstance(inner, dict):
                merged = dict(inner)
                merged.update({k: v for k, v in d.items() if k != "fields"})
                d = merged
            return d, _URL_RE.findall(raw)
        return {}, _URL_RE.findall(raw)
    except Exception:  # noqa: BLE001
        pass
    # A third writer used neither JSON nor repr but a flat
    # 'occupation=Chef & Owner; org=ChiliCali'. Same defect, same repair.
    if "=" in raw and not raw.lstrip().startswith(("{", "[")):
        out = {}
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            k, v = k.strip().lower(), v.strip()
            if k and v:
                out[k] = v
        if out:
            return out, _URL_RE.findall(raw)
    return {}, _URL_RE.findall(raw)


ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row

blobs = [dict(r) for r in con.execute(
    "SELECT f.id, f.predicate, f.value, f.confidence, f.source_type, "
    "f.source_ref, f.record_time, e.source_id AS pid, p.name AS pname, "
    "p.name_family AS pfam "
    "FROM facts f "
    "JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
    "LEFT JOIN persons p ON p.id = e.source_id "
    "WHERE f.valid_until IS NULL "
    "  AND ((TRIM(f.value) LIKE '{%' OR TRIM(f.value) LIKE '[%') "
    "       OR f.predicate IN ('enrichment','social_urls','social_profiles_raw'))")]

# What each person already has, so unpacking never creates a duplicate.
existing = set()
for r in con.execute(
        "SELECT e.source_id pid, f.predicate, f.value FROM facts f "
        "JOIN edges e ON e.target_id = f.id AND e.rel_type = 'HasFact' "
        "WHERE f.valid_until IS NULL"):
    existing.add((r["pid"], r["predicate"], (r["value"] or "").strip().lower()))

new_facts, retire, rejected, empty, dupes = [], [], [], [], []
for b in blobs:
    d, urls = parse_payload(b["value"])
    got, seen_already = [], False
    for key, pred in FIELD_MAP.items():
        val = d.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        val = val.strip()
        names = [b["pname"] or ""]
        why = None
        if pred == "occupation":
            why = classify_job(val, b["pname"] or "", "occupation", names)
        elif pred == "org" and (b["pfam"] or "").strip():
            # The org rules compare a company against the contact's own given
            # and family name, so they only mean anything for a person. A
            # company contact -- AlphaSights, Citibank, PayPal -- has no family
            # name and its org is legitimately its own name; judging it by the
            # person rules refuses a correct value. This is the same guard the
            # live cleanup used, applied off the same column rather than
            # re-derived from a name string.
            why = classify_org(val, b["pname"] or "", set())
        if why:
            rejected.append((b["pname"], pred, val, why))
            continue
        if (b["pid"], pred, val.lower()) in existing:
            seen_already = True
            continue
        existing.add((b["pid"], pred, val.lower()))
        got.append((pred, val, b["source_ref"] or ""))
    for u in urls:
        pred, canon = classify_url(u)
        if not pred or not canon:
            continue
        if (b["pid"], pred, canon.lower()) in existing:
            seen_already = True
            continue
        existing.add((b["pid"], pred, canon.lower()))
        got.append((pred, canon, b["source_ref"] or u))
    for pred, val, ref in got:
        new_facts.append({"pid": b["pid"], "pname": b["pname"], "predicate": pred,
                          "value": val, "confidence": b["confidence"],
                          "source_type": b["source_type"], "source_ref": ref,
                          "record_time": b["record_time"], "from_blob": b["id"]})
    if got:
        retire.append(b)
    elif seen_already:
        dupes.append(b)          # everything in it is already a proper fact
    else:
        empty.append(b)

print("  payload-valued facts found     : %d" % len(blobs))
print("  statements recovered from them : %d" % len(new_facts))
print("  blobs that yielded something   : %d" % len(retire))
print("  blobs already fully represented: %d" % len(dupes))
print("  blobs holding nothing at all   : %d" % len(empty))
print("  legacy values refused as junk  : %d" % len(rejected))
from collections import Counter
print("\n  recovered by predicate:")
for p, n in Counter(f["predicate"] for f in new_facts).most_common():
    print("     %-20s %d" % (p, n))
if rejected:
    print("\n  refused (same rules as the live data):")
    for nm, pred, val, why in rejected[:12]:
        print("     %-20s %-11s %-30r %s" % (str(nm)[:20], pred, str(val)[:30], why))
print("\n  sample of what is recovered:")
for f in new_facts[:10]:
    print("     %-22s %-14s %r" % (str(f["pname"])[:22], f["predicate"],
                                   str(f["value"])[:44]))
if empty:
    print("\n  blobs holding nothing at all (retired):")
    for b in empty[:8]:
        print("     %-20s %-12s %r" % (str(b["pname"])[:20], b["predicate"],
                                       str(b["value"])[:60]))

if not a.apply:
    print("\ndry run; pass --apply to write")
    raise SystemExit

now = datetime.now(timezone.utc).isoformat()
con.execute("BEGIN IMMEDIATE")
try:
    for f in new_facts:
        fid = str(uuid.uuid4())
        con.execute(
            "INSERT INTO facts (id, predicate, value, confidence, source_type, "
            "source_ref, record_time) VALUES (?,?,?,?,?,?,?)",
            (fid, f["predicate"], f["value"], f["confidence"] or 0.5,
             f["source_type"] or "inferred",
             ("unpacked:" + (f["source_ref"] or ""))[:400], f["record_time"]))
        con.execute(
            "INSERT INTO edges (id, source_id, target_id, rel_type, confidence, "
            "record_time) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), f["pid"], fid, "HasFact",
             f["confidence"] or 0.5, f["record_time"]))
    for b in retire + dupes + empty:
        con.execute("UPDATE facts SET valid_until = ? WHERE id = ?", (now, b["id"]))
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
print("\n  wrote %d facts, retired %d blobs" % (len(new_facts),
      len(retire) + len(dupes) + len(empty)))

os.makedirs(AUDIT_DIR, exist_ok=True)
p = os.path.join(AUDIT_DIR, "unpack-blobs-%s.json" % now[:19].replace(":", ""))
json.dump({"run_at": now, "recovered": new_facts,
           "retired": [b["id"] for b in retire + dupes + empty],
           "refused": [{"name": n, "predicate": p2, "value": v, "why": w}
                       for n, p2, v, w in rejected]},
          open(p, "w"), indent=1, default=str)
print("  audit: %s" % p)
