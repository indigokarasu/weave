#!/usr/bin/env python3
"""
Enrichment control script for Weave contact enrichment pipeline.
Provides status and control commands consumed by weave_health_check.py.

Usage:
    python3 enrichment_control.py status
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
PROGRESS_FILE = AGENT_ROOT / "data/weave-enrichment/progress.jsonl"
STATS_FILE = AGENT_ROOT / "data/weave-enrichment/stats.json"
PID_FILE = AGENT_ROOT / "data/weave-enrichment/enrichment.pid"

def status():
    """Report enrichment pipeline status."""
    checks = {}
    
    # Check if process is running
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            checks["process"] = "running"
            checks["pid"] = pid
        except (ValueError, ProcessLookupError, PermissionError):
            checks["process"] = "not_running"
            checks["pid_file_stale"] = True
    else:
        checks["process"] = "not_running"
        checks["pid_file"] = "absent"
    
    # Check stats
    if STATS_FILE.exists():
        try:
            stats = json.loads(STATS_FILE.read_text())
            checks["stats"] = stats
        except (json.JSONDecodeError, OSError):
            checks["stats"] = "unreadable"
    else:
        checks["stats"] = "no_stats_file"
    
    # Check progress file
    if PROGRESS_FILE.exists():
        try:
            lines = PROGRESS_FILE.read_text().strip().splitlines()
            checks["progress_entries"] = len(lines)
            if lines:
                last = json.loads(lines[-1])
                checks["last_progress_ts"] = last.get("timestamp", "unknown")
        except (json.JSONDecodeError, OSError):
            checks["progress"] = "error_reading"
    else:
        checks["progress_entries"] = 0
    
    # Determine overall health
    if checks.get("process") == "running":
        print("ENRICHMENT_OK: Process running")
        print(json.dumps(checks, indent=2, default=str))
        return True
    else:
        print("ENRICHMENT_OK: Process idle (normal between runs)")
        print(json.dumps(checks, indent=2, default=str))
        return True

def main():
    if len(sys.argv) < 2:
        print("Usage: enrichment_control.py <status|start|stop>")
        sys.exit(1)
    
    cmd = sys.argv[1].lower()
    
    if cmd == "status":
        success = status()
        sys.exit(0 if success else 1)
    elif cmd == "start":
        print("Use overnight_enrichment.py directly for pipeline execution.")
        sys.exit(0)
    elif cmd == "stop":
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                os.kill(pid, 15)
                print(f"Sent SIGTERM to PID {pid}")
                PID_FILE.unlink(missing_ok=True)
            except (ValueError, ProcessLookupError) as e:
                print(f"Could not stop: {e}")
                PID_FILE.unlink(missing_ok=True)
                sys.exit(1)
        else:
            print("No PID file — process not tracked.")
        sys.exit(0)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
