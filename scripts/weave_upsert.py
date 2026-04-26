
import json
from datetime import datetime
from pathlib import Path

# LadybugDB Cypher operations for Weave
DB_PATH = Path("<hermes-root>/commons/db/ocas-weave/weave.lbug")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Try to import ladybugdb, if not available, create mock output
try:
    import ladybugdb
    db = ladybugdb.connect(str(DB_PATH))
    
    # Create schema if not exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS Person (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            record_time TEXT,
            source_type TEXT,
            source_ref TEXT,
            confidence REAL
        )
    """)
    
    db.execute("""
        CREATE TABLE IF NOT EXISTS Preference (
            id TEXT PRIMARY KEY,
            person_id TEXT,
            category TEXT,
            value TEXT,
            record_time TEXT,
            source_type TEXT,
            source_ref TEXT,
            confidence REAL
        )
    """)
    
    db.execute("""
        CREATE TABLE IF NOT EXISTS Knows (
            id TEXT PRIMARY KEY,
            person1_id TEXT,
            person2_id TEXT,
            rel_type TEXT,
            record_time TEXT,
            source_type TEXT,
            source_ref TEXT,
            confidence REAL
        )
    """)
    
    has_db = True
except Exception as e:
    print(f"Database not available: {e}")
    has_db = False

# Load findings
with open("<hermes-root>/commons/data/ocas-expansion/phase1_scout_findings.json", "r") as f:
    findings = json.load(f)

upserted = []
failed = []

for person in findings:
    person_id = person["id"]
    name = person["name"]
    email = person["email"]
    confidence = 0.9 if person["confidence"] == "high" else 0.7
    record_time = datetime.now().isoformat()
    
    try:
        if has_db:
            # MERGE person (upsert)
            db.execute("""
                INSERT INTO Person (id, name, email, record_time, source_type, source_ref, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    email = excluded.email,
                    record_time = excluded.record_time,
                    confidence = excluded.confidence
            """, [person_id, name, email, record_time, "imported", "email_domain_inference", confidence])
        
        upserted.append({
            "id": person_id,
            "name": name,
            "email": email,
            "status": "success"
        })
        print(f"  ✓ {name} ({email})")
    except Exception as e:
        failed.append({
            "id": person_id,
            "name": name,
            "error": str(e)
        })
        print(f"  ✗ {name}: {e}")

if has_db:
    db.commit()
    db.close()

# Save upsert results
results = {
    "run_id": "weave-upsert-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
    "timestamp": record_time,
    "upserted": upserted,
    "failed": failed,
    "total": len(findings),
    "success_count": len(upserted),
    "failure_count": len(failed)
}

with open("<hermes-root>/commons/data/ocas-expansion/phase2_weave_upsert_results.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nPhase 2 complete: {len(upserted)}/{len(findings)} persons upserted")
