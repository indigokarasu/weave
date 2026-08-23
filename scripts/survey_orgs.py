"""Every org and occupation value, so the junk patterns are visible rather than guessed.

Jared named five: "Contact Me Book Katie Allen to speak", "Trust Issues",
"Heriot, Watt", "Franklin, Past Chiefs", "franklin, r and FounderThe long".
Those are not one defect -- they look like at least four:
  a call-to-action sentence, a comma-split proper noun, a truncated fragment,
  and run-together scraped text.
Find the full extent before writing any rule.
"""
import collections
import re
import sqlite3
import sys

sys.path.insert(0, "/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
try:
    from google_sync import is_implausible_job_value
except Exception as e:  # noqa: BLE001
    is_implausible_job_value = None
    print("  (could not import is_implausible_job_value: %s)" % e)

con = sqlite3.connect("/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
con.row_factory = sqlite3.Row

NAMED = ["Contact Me Book Katie Allen to speak", "Trust Issues", "Heriot, Watt",
         "Franklin, Past Chiefs", "franklin, r and FounderThe long"]
print("=== the five Jared named: does the existing checker catch them? ===")
for v in NAMED:
    verdict = is_implausible_job_value(v) if is_implausible_job_value else "n/a"
    print("   %-42r -> %s" % (v, verdict))

# where do they live?
print("\n=== where those values actually are ===")
for v in NAMED:
    for r in con.execute(
            "SELECT p.name, 'persons.'||'org' AS loc, p.org AS val FROM persons p "
            "WHERE p.org = ? UNION ALL "
            "SELECT p.name, 'persons.occupation', p.occupation FROM persons p "
            "WHERE p.occupation = ?", (v, v)):
        print("   %-24s %-20s %r" % (r["name"][:24], r["loc"], r["val"]))
    for r in con.execute(
            "SELECT p.name, f.predicate, f.value, f.source_type FROM facts f "
            "JOIN edges e ON e.target_id=f.id AND e.rel_type='HasFact' "
            "JOIN persons p ON p.id=e.source_id "
            "WHERE f.valid_until IS NULL AND f.value = ?", (v,)):
        print("   %-24s fact.%-15s %-34r %s"
              % (r["name"][:24], r["predicate"], str(r["value"])[:34], r["source_type"]))

print("\n=== all distinct org values, flagged by shape ===")
vals = collections.Counter()
for r in con.execute("SELECT org v FROM persons WHERE org IS NOT NULL AND org != '' "
                     "UNION ALL SELECT f.value FROM facts f WHERE f.predicate='org' "
                     "AND f.valid_until IS NULL"):
    vals[r["v"].strip()] += 1

SUSPECT = [
    ("comma-split proper noun", re.compile(r"^[A-Z][a-z]+,\s*[A-Z][a-z]+$")),
    ("starts with a lowercase word", re.compile(r"^[a-z]")),
    ("contains a run-together capital", re.compile(r"[a-z][A-Z][a-z]")),
    ("call to action / sentence", re.compile(
        r"\b(contact|book|hire|email|call|visit|click|follow|subscribe|learn more|"
        r"read more|get in touch|to speak|available for)\b", re.I)),
    ("very long (a sentence, not a name)", re.compile(r"^.{45,}$")),
    ("ends mid-word / dangling", re.compile(r"\b(and|the|of|at|for|with|in|to)$", re.I)),
    ("contains a pipe or slash list", re.compile(r"[|]|\s/\s")),
]
hits = collections.defaultdict(list)
for v, n in vals.items():
    for label, rx in SUSPECT:
        if rx.search(v):
            hits[label].append((v, n))
for label, _rx in SUSPECT:
    rows = hits.get(label) or []
    if not rows:
        continue
    print("\n  %-38s %d" % (label, len(rows)))
    for v, n in sorted(rows)[:10]:
        print("       %-58r x%d" % (v[:58], n))
print("\n  distinct org values total: %d" % len(vals))
