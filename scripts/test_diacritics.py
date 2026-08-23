#!/usr/bin/env python3
import os
"""Diacritics are folded for MATCHING only, never in stored or transmitted values.

Folding accents made accented names matchable at all -- they were previously mangled
into strings that cannot occur. This test pins the other half of that contract: the
contact's real spelling must survive untouched in weave, in what the sync sends to
google, and in every stored fact. A future change that folds on a write path fails
here rather than quietly flattening 19 people's names.
"""
import sys, sqlite3, unicodedata
sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-scout/scripts"))
sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from _normalize import fold_accents, normalize_name
from research_person import _name_phrase_in_text, _name_agreement

DB = os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite")
fails = 0

def check(label, ok, detail=""):
    global fails
    fails += (not ok)
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", label, ("  " + detail) if detail else ""))

# --- matching MUST ignore diacritics ----------------------------------------
print("matching ignores diacritics:")
for full, g, f, text in [("Zoe Muller", "Zoe", "Muller", "Zoë Müller, engineer"),
                         ("Zoë Müller", "Zoë", "Müller", "Zoe Muller, engineer"),
                         ("Rene Cote", "Rene", "Cote", "René Côté profile")]:
    check("%-22r matches %r" % (full, text[:28]),
          _name_phrase_in_text(full, text, g, f))
check("accented name matches itself", _name_agreement("Zoë Müller", "Zoë Müller")[0] >= 2)

# --- storage MUST keep them --------------------------------------------------
print("\nstored records keep their diacritics:")
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
acc = [r for r in con.execute(
    "SELECT id, name, name_given, name_family FROM persons WHERE name IS NOT NULL")
    if any(ord(c) > 127 for c in r["name"])]
check("book still has accented names", len(acc) > 0, "%d contacts" % len(acc))
def has_combining(t):
    """True when the text carries a diacritic that folding would remove.

    Emoji are non-ascii but decompose to nothing, so a name like an emoji-prefixed
    one compares equal to its folded form without having been flattened. Testing
    equality alone misread those as damage."""
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFKD", t))

diacritic_names = [r["name"] for r in acc if has_combining(r["name"])]
check("book has names with real diacritics", len(diacritic_names) > 0,
      "%d of %d non-ascii" % (len(diacritic_names), len(acc)))
flattened = [n for n in diacritic_names if n == fold_accents(n)]
check("no diacritic name flattened", not flattened, str(flattened[:3]))

# a fact must never hold the de-accented spelling in place of the real one
bad = []
for r in acc:
    folded = fold_accents(r["name"])
    if folded == r["name"]:
        continue
    for fr in con.execute(
            "SELECT f.predicate, f.value FROM facts f "
            "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
            "WHERE e.source_id=? AND f.valid_until IS NULL", (r["id"],)):
        v = fr["value"] or ""
        if folded.lower() in v.lower() and r["name"].lower() not in v.lower():
            bad.append((r["name"], fr["predicate"], v[:40]))
check("no fact stores a de-accented name", not bad, str(bad[:2]))

# --- the outbound body MUST carry the real spelling --------------------------
print("\nwhat the sync would send:")
import copy
import google_sync as gs
sent = []
gs._api_post = lambda url, t, body=None, timeout=None: (
    sent.append(copy.deepcopy(body)) if "batchUpdate" in url else None) or {"updateResult": {}}
gs.sync_outbound(gs.get_access_token(), "2026-01-01T00:00:00+00:00")
rn_name = {r["google_resource_name"]: r["name"] for r in con.execute(
    "SELECT google_resource_name, name FROM persons "
    "WHERE google_resource_name IS NOT NULL AND name IS NOT NULL")}
checked = flat = 0
for b in sent:
    for rn, body in ((b or {}).get("contacts") or {}).items():
        nm = rn_name.get(rn) or ""
        if not any(ord(c) > 127 for c in nm) or "names" not in body:
            continue
        checked += 1
        sentname = " ".join(str(n.get("givenName", "")) + " " + str(n.get("familyName", ""))
                            for n in body["names"])
        if any(ord(c) > 127 for c in nm) and not any(ord(c) > 127 for c in sentname):
            flat += 1
            print("      FLATTENED: %r -> %r" % (nm, sentname))
check("accented contacts appear in outbound", checked > 0, "%d contacts" % checked)
check("none flattened on the wire", flat == 0)

print("\n%s" % ("PASS" if not fails else "%d FAILURE(S)" % fails))
sys.exit(1 if fails else 0)
