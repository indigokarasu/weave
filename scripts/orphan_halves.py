"""Orgs left orphaned when their paired title was cleared as junk.

Clearing one half of a split string leaves the other half sitting alone and
looking plausible: Patrick Au-Yeung's title 'r and FounderThe long' was removed
as a fragment, and 'Franklin' stayed behind as his employer. The pair rule
cannot see it any more, because there is no longer a pair.

So: for every contact whose title was cleared today, check whether the org that
remains is a bare single word with no company-ness -- the signature of the other
half.
"""
import glob
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "skills/ocas-weave/scripts"))
from pair_junk import COMPANY_HINT

con = sqlite3.connect(os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/db/ocas-weave/weave.sqlite"))
con.row_factory = sqlite3.Row

# contacts whose org or title we cleared today
touched = set()
for f in glob.glob(os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave/job-junk-*.json")) \
        + glob.glob(os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave/org-junk-*.json")) \
        + glob.glob(os.path.join(os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "indigo")), "commons/data/ocas-weave/pair-repair-*.json")):
    try:
        d = json.load(open(f))
    except Exception:  # noqa: BLE001
        continue
    for key in ("cleared_columns", "cleared", "changes"):
        for e in (d.get(key) or []):
            if e.get("name"):
                touched.add(e["name"])
print("  contacts whose org/title was cleaned today: %d" % len(touched))

orphans = []
for nm in sorted(touched):
    r = con.execute("SELECT name, org, occupation, name_given, name_family "
                    "FROM persons WHERE name = ?", (nm,)).fetchone()
    if not r:
        continue
    org = (r["org"] or "").strip()
    title = (r["occupation"] or "").strip()
    if not org or title:
        continue                      # no org, or still has a title: not orphaned
    words = re.findall(r"[A-Za-z0-9&.'-]+", org)
    if len(words) <= 1 and not COMPANY_HINT.search(org):
        orphans.append(dict(r))

print("  orgs left alone as a bare single word: %d\n" % len(orphans))
for r in orphans:
    print("   %-26s org=%r" % (str(r["name"])[:26], r["org"]))
