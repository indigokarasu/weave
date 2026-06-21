"""
Weave Visualizer API Server — v1.1
Reads weave.lbug snapshot, serves graph data, and handles writes via
stop-bridge → write → restart-bridge pattern.
"""
import json
import os
import re
import subprocess
import shutil
import time
from pathlib import Path
from typing import Optional

import real_ladybug as lb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ── Paths ──
WEAVE_DB = Path("<hermes-root>/commons/db/ocas-weave/weave.lbug")
WEAVE_DIR = Path("<hermes-root>/commons/db/ocas-weave")
SNAPSHOT_DIR = WEAVE_DIR / "snapshots"
VIZ_SNAPSHOT = SNAPSHOT_DIR / "weave_viz_copy.lbug"
BRIDGE_SERVICE = "ladybug-bridge-weave.service"

app = FastAPI(title="Weave Visualizer API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── DB helpers ──

def get_db():
    """Read-only connection to the snapshot."""
    db_path = VIZ_SNAPSHOT if VIZ_SNAPSHOT.exists() else WEAVE_DB
    db = lb.Database(str(db_path))
    return lb.Connection(db)


def with_write_db():
    """
    Get a write-capable connection to the live weave.lbug.
    Stops the bridge, copies snapshot to temp, yields (conn, temp_path),
    then restarts the bridge. Caller must close conn and clean up temp.
    """
    # Stop bridge
    subprocess.run(["systemctl", "stop", BRIDGE_SERVICE], capture_output=True, timeout=15)
    time.sleep(1)
    r = subprocess.run(["systemctl", "is-active", BRIDGE_SERVICE], capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        subprocess.run(["systemctl", "kill", "--signal=SIGKILL", BRIDGE_SERVICE], capture_output=True, timeout=10)
        time.sleep(1)

    temp_path = SNAPSHOT_DIR / f"weave_write_{os.getpid()}.lbug"
    temp_wal = SNAPSHOT_DIR / f"weave_write_{os.getpid()}.lbug.wal"
    shutil.copy2(str(WEAVE_DB), str(temp_path))
    wal = WEAVE_DIR / "weave.lbug.wal"
    if wal.exists():
        shutil.copy2(str(wal), str(temp_wal))

    db = lb.Database(str(temp_path))
    conn = lb.Connection(db)

    class _WriteContext:
        def __init__(self, conn, temp_path):
            self.conn = conn
            self.temp_path = temp_path
        def promote(self):
            """Write temp DB back to live weave.lbug."""
            self.conn.close()
            shutil.copy2(str(self.temp_path), str(WEAVE_DB))
            live_wal = WEAVE_DIR / "weave.lbug.wal"
            if temp_wal.exists():
                shutil.copy2(str(temp_wal), str(live_wal))
            # Start bridge
            subprocess.run(["systemctl", "start", BRIDGE_SERVICE], capture_output=True, timeout=15)

    return _WriteContext(conn, temp_path)


def extract_node_props(raw: dict) -> dict:
    skip = {"_ID", "_LABEL", "_SRC", "_DST"}
    return {k: v for k, v in raw.items() if k not in skip and v is not None}


def extract_rel_props(raw: dict) -> dict:
    skip = {"_ID", "_LABEL", "_SRC", "_DST"}
    return {k: v for k, v in raw.items() if k not in skip and v is not None}


# ── Color hashing ──

def hash_string(s: str) -> int:
    """djb2 hash — deterministic, good distribution."""
    h = 5381
    for c in s:
        h = ((h << 5) + h) + ord(c)
    return h & 0xFFFFFFFF


# Pre-compute colors for known orgs
_ORG_COLOR_CACHE: dict[str, str] = {}

def org_color(org: str) -> str:
    """Deterministic color from org string, derived from accent hue."""
    if not org:
        return "#9CA3AF"  # tertiary
    if org in _ORG_COLOR_CACHE:
        return _ORG_COLOR_CACHE[org]
    h = hash_string(org)
    # Use hash to pick a hue offset from the accent hue (221)
    hue_offset = (h % 360)
    # Saturation 40-70%, lightness 45-65% — readable range
    sat = 40 + (h % 30)
    lit = 45 + (h % 20)
    color = f"hsl({hue_offset}, {sat}%, {lit}%)"
    _ORG_COLOR_CACHE[org] = color
    return color


# ── Snapshot refresh ──

def refresh_snapshot():
    """Stop bridge, copy DB, restart bridge."""
    subprocess.run(["systemctl", "stop", BRIDGE_SERVICE], capture_output=True, timeout=15)
    time.sleep(1)
    r = subprocess.run(["systemctl", "is-active", BRIDGE_SERVICE], capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        subprocess.run(["systemctl", "kill", "--signal=SIGKILL", BRIDGE_SERVICE], capture_output=True, timeout=10)
        time.sleep(1)

    shutil.copy2(str(WEAVE_DB), str(VIZ_SNAPSHOT))
    wal = WEAVE_DIR / "weave.lbug.wal"
    if wal.exists():
        shutil.copy2(str(wal), str(VIZ_SNAPSHOT) + ".wal")

    subprocess.run(["systemctl", "start", BRIDGE_SERVICE], capture_output=True, timeout=15)
    return {"status": "refreshed", "timestamp": time.time()}


# ── API: Read endpoints ──

@app.get("/api/health")
async def health():
    snap_exists = VIZ_SNAPSHOT.exists()
    snap_age = 0
    if snap_exists:
        snap_age = int(time.time() - VIZ_SNAPSHOT.stat().st_mtime)
    return {
        "status": "ok",
        "db_exists": WEAVE_DB.exists(),
        "db_size_mb": round(WEAVE_DB.stat().st_size / 1024 / 1024, 1) if WEAVE_DB.exists() else 0,
        "snapshot_exists": snap_exists,
        "snapshot_age_seconds": snap_age,
    }


@app.get("/api/graph")
async def get_graph(limit: int = 2000):
    """Return the graph: nodes (persons) + links (knows relationships)."""
    try:
        conn = get_db()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")

    try:
        persons = conn.execute(f"MATCH (p:Person) RETURN p LIMIT {limit}")
        nodes = []
        for row in persons:
            raw = row[0]
            props = extract_node_props(raw)
            org = props.get("org", "")
            nodes.append({
                "id": props.get("id", ""),
                "name": props.get("name", "Unknown"),
                "email": props.get("email", ""),
                "org": org,
                "occupation": props.get("occupation", ""),
                "location_city": props.get("location_city", ""),
                "location_country": props.get("location_country", ""),
                "confidence": props.get("confidence", 0),
                "source_type": props.get("source_type", ""),
                "preference_count": 0,
                "fact_count": 0,
                "color": org_color(org),
            })

        rels = conn.execute(
            f"MATCH (a:Person)-[r:Knows]->(b:Person) RETURN a.id, b.id, r LIMIT {limit * 2}"
        )
        links = []
        for row in rels:
            rel_props = extract_rel_props(row[2])
            strength = rel_props.get("strength") or 0.5
            links.append({
                "source": row[0],
                "target": row[1],
                "rel_type": rel_props.get("rel_type", "knows"),
                "strength": strength,
                "context": rel_props.get("context", ""),
                "confidence": rel_props.get("confidence", 0.5),
            })

        pref_counts = {}
        for row in conn.execute("MATCH (p:Person)-[:HasPreference]->(pref) RETURN p.id, count(*) as cnt"):
            pref_counts[row[0]] = row[1]

        fact_counts = {}
        for row in conn.execute("MATCH (p:Person)-[:HasFact]->(f:Fact) RETURN p.id, count(*) as cnt"):
            fact_counts[row[0]] = row[1]

        for node in nodes:
            pid = node["id"]
            node["preference_count"] = pref_counts.get(pid, 0)
            node["fact_count"] = fact_counts.get(pid, 0)

        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": len(nodes),
                "total_links": len(links),
                "total_preferences": sum(pref_counts.values()),
                "total_facts": sum(fact_counts.values()),
            }
        }
    finally:
        conn.close()
@app.get("/api/person/{person_id:path}")
async def get_person(person_id: str):
    """Get full details for a single person."""
    if not re.match(r'^(viz-|pref-)?[0-9a-f-]+$', person_id):
        raise HTTPException(status_code=400, detail="Invalid person ID format")

    try:
        conn = get_db()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")

    try:
        # Person
        rows = list(conn.execute(f"MATCH (p:Person {{id: '{person_id}'}}) RETURN p"))
        if not rows:
            raise HTTPException(status_code=404, detail="Person not found")
        person = extract_node_props(rows[0][0])

        # Preferences
        preferences = [extract_node_props(r[0]) for r in conn.execute(
            f"MATCH (p:Person {{id: '{person_id}'}})-[:HasPreference]->(pref) RETURN pref"
        )]

        # Facts (limit to 20 for panel)
        facts = [extract_node_props(r[0]) for r in conn.execute(
            f"MATCH (p:Person {{id: '{person_id}'}})-[:HasFact]->(f:Fact) RETURN f LIMIT 20"
        )]

        # Outgoing connections
        outgoing = []
        for row in conn.execute(
            f"MATCH (p:Person {{id: '{person_id}'}})-[r:Knows]->(other:Person) RETURN other.name, other.id, r"
        ):
            rp = extract_rel_props(row[2])
            outgoing.append({
                "name": row[0], "id": row[1],
                "rel_type": rp.get("rel_type", "knows"),
                "strength": rp.get("strength", 0.5),
                "context": rp.get("context", ""),
            })

        # Incoming connections
        incoming = []
        for row in conn.execute(
            f"MATCH (other:Person)-[r:Knows]->(p:Person {{id: '{person_id}'}}) RETURN other.name, other.id, r"
        ):
            rp = extract_rel_props(row[2])
            incoming.append({
                "name": row[0], "id": row[1],
                "rel_type": rp.get("rel_type", "knows"),
                "strength": rp.get("strength", 0.5),
                "context": rp.get("context", ""),
            })

        return {
            "person": person,
            "preferences": preferences,
            "facts": facts,
            "connections_out": outgoing,
            "connections_in": incoming,
        }
    finally:
        conn.close()


@app.get("/api/stats")
async def get_stats():
    """Aggregate statistics."""
    try:
        conn = get_db()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")

    try:
        stats = {}
        for label, query in [
            ("total_persons", "MATCH (p:Person) RETURN count(p)"),
            ("total_relationships", "MATCH ()-[r:Knows]->() RETURN count(r)"),
            ("total_preferences", "MATCH ()-[:HasPreference]->() RETURN count(*)"),
            ("total_facts", "MATCH ()-[:HasFact]->() RETURN count(*)"),
        ]:
            r = list(conn.execute(query))
            stats[label] = r[0][0] if r else 0

        org_result = conn.execute(
            "MATCH (p:Person) WHERE p.org IS NOT NULL AND p.org <> '' "
            "RETURN p.org, count(*) as cnt ORDER BY cnt DESC LIMIT 10"
        )
        stats["top_orgs"] = [{"org": row[0], "count": row[1]} for row in org_result]

        loc_result = conn.execute(
            "MATCH (p:Person) WHERE p.location_city IS NOT NULL AND p.location_city <> '' "
            "RETURN p.location_city, count(*) as cnt ORDER BY cnt DESC LIMIT 10"
        )
        stats["top_locations"] = [{"location": row[0], "count": row[1]} for row in loc_result]

        type_result = conn.execute(
            "MATCH ()-[r:Knows]->() WHERE r.rel_type IS NOT NULL "
            "RETURN r.rel_type, count(*) as cnt ORDER BY cnt DESC"
        )
        stats["rel_types"] = [{"type": row[0], "count": row[1]} for row in type_result]

        return stats
    finally:
        conn.close()


# ── API: Snapshot management ──

@app.post("/api/refresh")
async def api_refresh():
    """Trigger a snapshot refresh (admin only — auth via nginx)."""
    try:
        result = refresh_snapshot()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── API: Write endpoints (edit) ──

def _stop_bridge():
    """Stop the bridge aggressively — try graceful first, then force kill."""
    # Try graceful stop with short timeout
    try:
        subprocess.run(["systemctl", "stop", BRIDGE_SERVICE], capture_output=True, timeout=5)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(1)
    # Check if still running, force kill if needed
    r = subprocess.run(["systemctl", "is-active", BRIDGE_SERVICE], capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        subprocess.run(["systemctl", "kill", "--signal=SIGKILL", BRIDGE_SERVICE], capture_output=True, timeout=10)
        time.sleep(1)
    # Kill any remaining process holding the lock
    lsof = subprocess.run(["lsof", str(WEAVE_DB)], capture_output=True, text=True, timeout=5)
    for line in lsof.stdout.split('\n')[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 2:
            try:
                pid = int(parts[1])
                os.kill(pid, 9)
            except (ValueError, ProcessLookupError, PermissionError):
                pass


def _start_bridge():
    subprocess.run(["systemctl", "start", BRIDGE_SERVICE], capture_output=True, timeout=15)


@app.post("/api/person")
async def create_or_update_person(body: dict):
    """Create or update a person."""
    from fastapi import Request
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    person_id = body.get("id") or f"viz-{int(time.time() * 1000)}"
    props = {"id": person_id, "name": name}
    for k in ["email", "phone", "org", "occupation", "location_city", "location_country", "name_given", "name_family"]:
        v = body.get(k)
        if v and str(v).strip():
            props[k] = str(v).strip()
    props["source_type"] = "user-stated"
    props["source_ref"] = "weave-viz-ui"
    props["confidence"] = 1.0
    props["record_time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    _stop_bridge()
    try:
        temp_path = SNAPSHOT_DIR / f"weave_edit_{os.getpid()}.lbug"
        shutil.copy2(str(WEAVE_DB), str(temp_path))
        try:
            db = lb.Database(str(temp_path))
            conn = lb.Connection(db)
            # Build SET clauses — strings get quoted, numerics don't
            clauses = []
            for k, v in props.items():
                if k == "id":
                    continue
                if isinstance(v, (int, float)):
                    clauses.append(f"p.{k} = {v}")
                else:
                    clauses.append(f"p.{k} = '{v}'")
            set_clauses = ", ".join(clauses)
            conn.execute(
                f"MERGE (p:Person {{id: '{person_id}'}}) "
                f"ON CREATE SET {set_clauses} "
                f"ON MATCH SET {set_clauses}"
            )
            conn.close()
            shutil.copy2(str(temp_path), str(WEAVE_DB))
            return {"id": person_id, "name": name, "status": "created"}
        finally:
            if temp_path.exists():
                temp_path.unlink()
    finally:
        _start_bridge()


@app.post("/api/relationship")
async def create_relationship(body: dict):
    """Create a Knows relationship. Body: {source_id, target_id, rel_type, context?}"""
    source_id = body.get("source_id", "").strip()
    target_id = body.get("target_id", "").strip()
    rel_type = body.get("rel_type", "knows").strip()
    context = body.get("context", "").strip()

    if not source_id or not target_id:
        raise HTTPException(status_code=400, detail="source_id and target_id required")
    if not re.match(r'^(viz-|pref-)?[0-9a-f-]+$', source_id) or not re.match(r'^(viz-|pref-)?[0-9a-f-]+$', target_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")

    _stop_bridge()
    try:
        temp_path = SNAPSHOT_DIR / f"weave_edit_{os.getpid()}.lbug"
        shutil.copy2(str(WEAVE_DB), str(temp_path))
        try:
            db = lb.Database(str(temp_path))
            conn = lb.Connection(db)

            # Ensure both persons exist
            src = list(conn.execute(f"MATCH (a:Person {{id: '{source_id}'}}) RETURN a"))
            if not src:
                raise HTTPException(status_code=404, detail=f"Source person not found: {source_id}")
            tgt = list(conn.execute(f"MATCH (b:Person {{id: '{target_id}'}}) RETURN b"))
            if not tgt:
                raise HTTPException(status_code=404, detail=f"Target person not found: {target_id}")

            now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            conn.execute(
                f"MATCH (a:Person {{id: '{source_id}'}}), (b:Person {{id: '{target_id}'}}) "
                f"CREATE (a)-[r:Knows {{rel_type: '{rel_type}', "
                f"context: '{context}', "
                f"strength: 0.8, "
                f"since: '{now}', "
                f"source_ref: 'weave-viz-ui', "
                f"confidence: 1.0, "
                f"record_time: '{now}'}}]->(b)"
            )
            conn.close()

            shutil.copy2(str(temp_path), str(WEAVE_DB))
            return {"source": source_id, "target": target_id, "rel_type": rel_type, "status": "created"}
        finally:
            if temp_path.exists():
                temp_path.unlink()
    finally:
        _start_bridge()


@app.post("/api/preference")
async def add_preference(body: dict):
    """Add a preference to a person. Body: {person_id, value, category?, valence?}"""
    person_id = body.get("person_id", "").strip()
    value = body.get("value", "").strip()
    if not person_id or not value:
        raise HTTPException(status_code=400, detail="person_id and value required")
    if not re.match(r'^(viz-|pref-)?[0-9a-f-]+$', person_id):
        raise HTTPException(status_code=400, detail="Invalid person ID format")

    pref_id = f"pref-{int(time.time() * 1000)}"
    category = body.get("category", "general")
    valence = body.get("valence", "like")
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    _stop_bridge()
    try:
        temp_path = SNAPSHOT_DIR / f"weave_edit_{os.getpid()}.lbug"
        shutil.copy2(str(WEAVE_DB), str(temp_path))
        try:
            db = lb.Database(str(temp_path))
            conn = lb.Connection(db)

            conn.execute(
                f"MATCH (p:Person {{id: '{person_id}'}}) "
                f"CREATE (pref:Preference {{id: '{pref_id}', value: '{value}', "
                f"category: '{category}', valence: '{valence}', "
                f"source_type: 'user-stated', source_ref: 'weave-viz-ui', "
                f"confidence: 1.0, record_time: '{now}'}}) "
                f"CREATE (p)-[:HasPreference]->(pref)"
            )
            conn.close()

            shutil.copy2(str(temp_path), str(WEAVE_DB))
            return {"id": pref_id, "value": value, "status": "created"}
        finally:
            if temp_path.exists():
                temp_path.unlink()
    finally:
        _start_bridge()


# ── API: Delete endpoints ──

def _write_op():
    """Context-manager-like helper for write operations."""
    _stop_bridge()
    temp_path = SNAPSHOT_DIR / f"weave_edit_{os.getpid()}.lbug"
    shutil.copy2(str(WEAVE_DB), str(temp_path))
    try:
        db = lb.Database(str(temp_path))
        conn = lb.Connection(db)
        yield conn, temp_path
        conn.close()
        shutil.copy2(str(temp_path), str(WEAVE_DB))
    finally:
        if temp_path.exists():
            temp_path.unlink()
        _start_bridge()


@app.post("/api/delete-person")
async def delete_person(body: dict):
    """Delete a person and all their edges."""
    person_id = body.get("id", "").strip()
    if not re.match(r'^(viz-|pref-)?[0-9a-f-]+$', person_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    gen = _write_op()
    try:
        conn, temp_path = next(gen)
        list(conn.execute(f"MATCH (p:Person {{id: '{person_id}'}}) DETACH DELETE p"))
    except StopIteration:
        pass
    return {"id": person_id, "status": "deleted"}


@app.post("/api/delete-relationship")
async def delete_relationship(body: dict):
    """Delete a Knows relationship."""
    source_id = body.get("source_id", "").strip()
    target_id = body.get("target_id", "").strip()
    rel_type = body.get("rel_type", "").strip()
    if not source_id or not target_id:
        raise HTTPException(status_code=400, detail="source_id and target_id required")
    gen = _write_op()
    try:
        conn, temp_path = next(gen)
        list(conn.execute(
            f"MATCH (a:Person {{id: '{source_id}'}})-[r:Knows {{rel_type: '{rel_type}'}}]->(b:Person {{id: '{target_id}'}}) DELETE r"
        ))
    except StopIteration:
        pass
    return {"status": "deleted"}


@app.post("/api/delete-preference")
async def delete_preference(body: dict):
    """Delete a preference by id or by person_id + value."""
    person_id = body.get("person_id", "").strip()
    pref_id = body.get("preference_id", "").strip()
    value = body.get("value", "").strip()
    if not person_id:
        raise HTTPException(status_code=400, detail="person_id required")
    gen = _write_op()
    try:
        conn, temp_path = next(gen)
        if pref_id:
            list(conn.execute(f"MATCH (pref:Preference {{id: '{pref_id}'}}) DETACH DELETE pref"))
        elif value:
            list(conn.execute(
                f"MATCH (p:Person {{id: '{person_id}'}})-[:HasPreference]->(pref:Preference {{value: '{value}'}}) DELETE pref"
            ))
    except StopIteration:
        pass
    return {"status": "deleted"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WEAVE_VIZ_PORT", 9120))
    uvicorn.run(app, host="127.0.0.1", port=port)
