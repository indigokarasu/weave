import real_ladybug as lb
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/root/.hermes/commons/db/ocas-weave/weave.lbug")
db = lb.Database(str(DB_PATH))
conn = lb.Connection(db)

now = datetime.now(timezone.utc).isoformat()

targets = [
    {
        "id": "17dbb3f4-344a-41e5-8e54-e768f965e4a8",
        "name": "Onny Chatterjee",
        "name_given": "Onny",
        "name_family": "Chatterjee",
        "email": "onnychatterjee@meta.com",
        "location_city": "San Francisco",
        "location_country": "US",
        "occupation": "Staff Pathfinding UX Wearables, AI",
        "org": "Meta",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.7
    },
    {
        "id": "6b3053ba-ec9c-513a-91c1-9483a60cb819",
        "name": "Ankita Akerkar",
        "name_given": "Ankita",
        "name_family": "Akerkar",
        "email": "aakerkar@google.com",
        "location_city": "New York",
        "location_country": "US",
        "occupation": "Interaction Designer",
        "org": "Google",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.7
    },
    {
        "id": "4f606101-d371-52d2-b37b-46e8a930cf74",
        "name": "Laith Ulaby",
        "name_given": "Laith",
        "name_family": "Ulaby",
        "email": "laith.ulaby@gmail.com",
        "location_country": "US",
        "occupation": "Senior Research Manager",
        "org": "Toyota Research Institute",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.8
    },
    {
        "id": "23151383-bbd9-523b-85f1-c08d1ba69b1f",
        "name": "Marc Paulina",
        "name_given": "Marc",
        "name_family": "Paulina",
        "email": "marcpaulina@googlemail.com",
        "occupation": "UX Designer",
        "org": "Google",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.6
    },
    {
        "id": "4bc81947-3e82-5ef3-aa7b-385df5653f4a",
        "name": "Yury Pinsky",
        "name_given": "Yury",
        "name_family": "Pinsky",
        "email": "ypinsky@google.com",
        "occupation": "Director of Product Management, Bard (Gemini)",
        "org": "Google",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.85
    },
    {
        "id": "fb665fba-7b73-58a0-83f8-f370b2cebbd6",
        "name": "Gustavo Moura",
        "name_given": "Gustavo",
        "name_family": "Moura",
        "email": "moura@google.com",
        "org": "Google",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.4
    },
    {
        "id": "a9de1d18-00ef-5922-892b-16dd14a4e5bd",
        "name": "Hadar Shemtov",
        "name_given": "Hadar",
        "name_family": "Shemtov",
        "email": "hadar@google.com",
        "location_city": "San Francisco Bay Area",
        "location_country": "US",
        "occupation": "Search and Relevance Specialist",
        "org": "Google",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.85
    },
    {
        "id": "278c7c8e-0624-51dc-882f-55f5d46c56d5",
        "name": "Russell Matsuo",
        "name_given": "Russell",
        "name_family": "Matsuo",
        "email": "rux@google.com",
        "location_city": "San Francisco",
        "location_country": "US",
        "occupation": "Staff UX Designer",
        "org": "Google",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.85
    },
    {
        "id": "897554c7-1806-5026-8334-89e94aed0571",
        "name": "Behshad Behzadi",
        "name_given": "Behshad",
        "name_family": "Behzadi",
        "email": "behshad@google.com",
        "location_city": "Zurich",
        "location_country": "CH",
        "occupation": "CPO, CTO and Chief AI Officer",
        "org": "Sportradar",
        "notes": "Previously VP Engineering at Google for 17 years. Co-founded Google Assistant, Google Lens, Google Smart Display.",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.9
    },
    {
        "id": "a1fe685f-919f-5bcf-b92a-9ceca233ca2c",
        "name": "Peter Oh",
        "name_given": "Peter",
        "name_family": "Oh",
        "email": "poh@google.com",
        "org": "Google",
        "source_type": "inferred",
        "source_ref": "graph-expansion-scout-20260413",
        "confidence": 0.4
    }
]

upserted = []
errors = []
upsert_results = []

for t in targets:
    try:
        fields = {"name": t["name"], "source_type": t["source_type"], "source_ref": t["source_ref"], "confidence": t["confidence"], "record_time": now}
        for key in ["name_given", "name_family", "email", "location_city", "location_country", "occupation", "org", "notes"]:
            if t.get(key):
                fields[key] = t[key]

        set_parts = []
        for k, v in fields.items():
            if isinstance(v, str):
                set_parts.append(f"p.{k} = '{v.replace(chr(39), chr(39)+chr(39))}'")
            else:
                set_parts.append(f"p.{k} = {v}")

        set_clause = ",\n    ".join(set_parts)
        query = f"MERGE (p:Person {{id: '{t['id']}'}})\nSET {set_clause}"
        conn.execute(query)

        # Read back to confirm
        result = conn.execute(f"MATCH (p:Person {{id: '{t['id']}'}}) RETURN p.id, p.name, p.org, p.occupation")
        cols = result.get_column_names()
        rows = result.get_all()
        if rows:
            row_dict = dict(zip(cols, rows[0]))
            upserted.append({"id": t["id"], "name": t["name"], "status": "upserted", "verified": row_dict})
        else:
            errors.append({"id": t["id"], "name": t["name"], "status": "readback_failed"})
    except Exception as e:
        errors.append({"id": t["id"], "name": t["name"], "error": str(e)})

output = {
    "timestamp": now,
    "phase": "2_weave_upsert",
    "upserted_count": len(upserted),
    "error_count": len(errors),
    "upserted": upserted,
    "errors": errors
}

# Save results
with open("/root/.hermes/commons/data/ocas-expansion/phase2_weave_upsert_20260413.json", "w") as f:
    json.dump(output, f, indent=2, default=str)

print(json.dumps(output, indent=2, default=str))