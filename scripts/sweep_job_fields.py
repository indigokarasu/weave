#!/usr/bin/env python3
"""Clear job-field junk by TESTING the value, not by matching a remembered string.

The sweep matched google's value against the exact string weave held. When the two
differed even slightly -- 'dio Engineering' in google versus 'dio Engineering (Mus'
in weave -- google kept its copy and the next inbound pulled it straight back. Every
previous cleanup carried this flaw.

This reads what google actually holds and clears it if it fails the gate, so value
drift cannot defeat it. Pass --apply to write."""
import sys, json, sqlite3, urllib.request, urllib.parse, time
sys.path.insert(0,"/root/.hermes/profiles/indigo/skills/ocas-weave/scripts")
import google_sync as gs
from google_sync import is_implausible_job_value as bad
tok=gs.get_access_token()
con=sqlite3.connect("/root/.hermes/profiles/indigo/commons/db/ocas-weave/weave.sqlite")
con.row_factory=sqlite3.Row
APPLY="--apply" in sys.argv
def api(u,m="GET",b=None):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        u,method=m,headers={"Authorization":"Bearer %s"%tok,"Content-Type":"application/json"},
        data=json.dumps(b).encode() if b else None),timeout=30))

rns=[r for r in con.execute("SELECT id,name,org,occupation,google_resource_name FROM persons "
                            "WHERE google_resource_name IS NOT NULL AND google_resource_name<>''")]
gmap={}
for i in range(0,len(rns),50):
    url=("https://people.googleapis.com/v1/people:batchGet?personFields=organizations&"
         + "&".join("resourceNames=%s"%urllib.parse.quote(r["google_resource_name"]) for r in rns[i:i+50]))
    try:
        d=api(url)
        for resp in d.get("responses",[]):
            p=resp.get("person") or {}
            gmap[p.get("resourceName") or resp.get("requestedResourceName")]=p
    except Exception as e: print("  batch %d: %s" % (i,str(e)[:50]))
    time.sleep(0.2)

todo=[]
for r in rns:
    p=gmap.get(r["google_resource_name"]) or {}
    o=(p.get("organizations") or [{}])[0]
    gn=(o.get("name") or "").strip(); gt=(o.get("title") or "").strip()
    bn = gn and gn.lower()!="self" and bad(gn)[0]
    bt = gt and bad(gt)[0]
    wn = (r["org"] or "").strip(); wt=(r["occupation"] or "").strip()
    bwn = wn and wn.lower()!="self" and bad(wn)[0]
    bwt = wt and bad(wt)[0]
    if bn or bt or bwn or bwt:
        todo.append((r,p,gn,gt,bn,bt,bwn,bwt))
print("contacts with junk in google or weave: %d" % len(todo))
for r,p,gn,gt,bn,bt,bwn,bwt in todo:
    print("   %-24s google org=%-22r title=%-28r  weave org=%-16r occ=%r"
          % (r["name"][:24], gn[:22] if bn else "", gt[:28] if bt else "",
             wn[:16] if bwn else "", (r["occupation"] or "")[:24] if bwt else ""))
if not APPLY:
    print("\n  dry run — pass --apply"); sys.exit(0)
for r,p,gn,gt,bn,bt,bwn,bwt in todo:
    if bn or bt:
        newo=[] if not ((gn if not bn else "") or (gt if not bt else "")) else \
             [{"name": "" if bn else gn, "title": "" if bt else gt}]
        api("https://people.googleapis.com/v1/%s:updateContact?updatePersonFields=organizations"
            % r["google_resource_name"], "PATCH", {"etag":p.get("etag"),"organizations":newo})
        time.sleep(0.12)
    if bwn: con.execute("UPDATE persons SET org='' WHERE id=?", (r["id"],))
    if bwt: con.execute("UPDATE persons SET occupation='' WHERE id=?", (r["id"],))
con.commit()
print("\n  cleaned %d contact(s) on whichever side held junk" % len(todo))
