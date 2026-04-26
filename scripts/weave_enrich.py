import real_ladybug
from datetime import datetime

db = real_ladybug.Database("/root/.hermes/commons/db/ocas-weave/weave.lbug")
con = real_ladybug.Connection(db)

# Enriched data
enrichments = {
    "6b3053ba-ec9c-513a-91c1-9483a60cb819": {  # Ankita Akerkar
        "occupation": "UX Designer",
        "org": "Google",
        "notes": "LinkedIn: https://www.linkedin.com/in/ankita-akerkar-701b0490",
        "confidence": 0.95
    },
    "278c7c8e-0624-51dc-882f-55f5d46c56d5": {  # Russell Matsuo
        "org": "Google",
        "confidence": 0.85
    },
    "a1fe685f-919f-5bcf-b92a-9ceca233ca2c": {  # Peter Oh
        "org": "Google",
        "confidence": 0.85
    },
    "17dbb3f4-344a-41e5-8e54-e768f965e4a8": {  # Onny Chatterjee
        "org": "Meta",
        "confidence": 0.60
    }
}

updated = 0
for person_id, data in enrichments.items():
    ts = datetime.now().isoformat()
    
    set_clauses = []
    set_clauses.append('p.confidence = %f' % data["confidence"])
    set_clauses.append('p.record_time = "%s"' % ts)
    set_clauses.append('p.source_ref = "hunter.io+sift"')
    
    if data.get("occupation"):
        set_clauses.append('p.occupation = "%s"' % data["occupation"])
    if data.get("org"):
        set_clauses.append('p.org = "%s"' % data["org"])
    if data.get("notes"):
        set_clauses.append('p.notes = "%s"' % data["notes"])
    
    query = """
    MATCH (p:Person {id: "%s"})
    SET %s
    """ % (person_id, ", ".join(set_clauses))
    
    try:
        result = con.execute(query)
        result.close()
        
        # Verify the update
        verify = con.execute('MATCH (p:Person {id: "%s"}) RETURN p.name, p.org, p.occupation, p.confidence' % person_id)
        row = verify.get_all()
        verify.close()
        
        if row:
            updated += 1
            print("  Updated: %s - %s at %s (%.2f)" % (row[0][0], row[0][1] or "Unknown", row[0][2] or "Unknown", row[0][3]))
        else:
            print("  Failed: %s - not found" % person_id[:8])
    except Exception as e:
        print("  Error for %s: %s" % (person_id[:8], str(e)[:80]))

print("\nTotal enriched: %d" % updated)
con.close()
db.close()
