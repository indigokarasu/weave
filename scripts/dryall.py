#!/usr/bin/env python3
# Usage: dryall.py [--help]
# argparse-based dry-run analysis for Weave contacts
import sqlite3
from people_to_google import build_google_payload
db=sqlite3.connect("<hermes-home>/profiles/<profile>/commons/db/people/people.db"); db.row_factory=sqlite3.Row
gl=db.execute("SELECT DISTINCT person_id FROM external_refs WHERE system='google'").fetchall()
from collections import Counter
stat=Counter(); samples=[]; anomalies=[]
def cur(pid,kind):
    return [r[0] for r in db.execute(f"SELECT value FROM identifiers WHERE person_id=? AND kind=? AND (valid_until IS NULL OR valid_until>'2026-06')",(pid,kind))]
for (pid,) in gl:
    pr=db.execute("SELECT * FROM people WHERE id=?",(pid,)).fetchone()
    if not pr: continue
    attrs={}
    for r in db.execute("SELECT key,value FROM attributes WHERE person_id=? AND (valid_until IS NULL OR valid_until>'2026-06')",(pid,)): attrs.setdefault(r[0],[]).append(r[1])
    p={"display_name":pr["display_name"],"given_name":pr["given_name"],"family_name":pr["family_name"],
       "emails":[{"value":e,"label":"work"} for e in cur(pid,"email")],
       "phones":[{"value":ph,"label":"mobile"} for ph in cur(pid,"phone")],"attrs":attrs,"relations":[]}
    body,mask=build_google_payload(p)
    for f in mask.split(","): stat[f]+=1
    stat["TOTAL"]+=1
    # anomaly checks
    nurls=len(body.get("urls",[])); 
    if nurls>6: anomalies.append(f"{pr['display_name']}: {nurls} urls")
    org=body.get("organizations",[{}])[0]
    if org.get("title") and len(org["title"])>60: anomalies.append(f"{pr['display_name']}: long title")
    if "biographies" in body or "userDefined" in body: anomalies.append(f"{pr['display_name']}: NOTES/CUSTOM leaked!")
    if len(samples)<4 and nurls>=2: samples.append((pr["display_name"],mask,[(u["type"],u["value"][:40]) for u in body.get("urls",[])]))
print(f"contacts to sync: {stat['TOTAL']}")
print("field coverage:", {k:v for k,v in stat.items() if k!='TOTAL'})
print(f"\nanomalies: {len(anomalies)}")
for a in anomalies[:10]: print("  ",a)
print("\nsamples:")
for n,m,u in samples: print(f"  {n}: [{m}]  urls={u}")