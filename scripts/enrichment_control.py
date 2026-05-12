#!/usr/bin/env python3
"""
Weave Enrichment Control - Start/stop/status for enrichment loops
Usage: 
  python3 weave_enrichment_control.py start --duration 8h
  python3 weave_enrichment_control.py stop
  python3 weave_enrichment_control.py status
"""

import json
import sys
import subprocess
import signal
import os
from pathlib import Path
from datetime import datetime

# Configuration
AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))
WEAVE_DATA = AGENT_ROOT / "commons/data/weave-enrichment-control"
STATE_FILE = WEAVE_DATA / "state.json"
PID_FILE = WEAVE_DATA / "enrichment.pid"
LOG_FILE = AGENT_ROOT / "logs/weave-enrichment.log"

def ensure_dirs():
    """Ensure required directories exist"""
    WEAVE_DATA.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_state():
    """Load current state"""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"running": False}

def save_state(state):
    """Save current state"""
    ensure_dirs()
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def get_pid():
    """Get running process PID"""
    if PID_FILE.exists():
        with open(PID_FILE) as f:
            try:
                return int(f.read().strip())
            except ValueError:
                return None
    return None

def save_pid(pid):
    """Save process PID"""
    ensure_dirs()
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))

def is_process_running(pid):
    """Check if process is running"""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

def start_enrichment(duration_str):
    """Start enrichment loop"""
    ensure_dirs()
    
    # Check if already running
    pid = get_pid()
    if pid and is_process_running(pid):
        state = load_state()
        print(f"Enrichment already running (PID: {pid})")
        print(f"Started: {state.get('started_at', 'Unknown')}")
        print(f"Duration: {state.get('duration_seconds', 0)/3600:.1f} hours")
        return 0
    
    # Start the enrichment loop
    script_path = Path(__file__).parent / "overnight_weave_enrichment.py"
    if not script_path.exists():
        print(f"Error: Script not found at {script_path}")
        return 1
    
    print(f"Starting overnight enrichment (runs until 8am ET)...")
    
    # Start in background
    process = subprocess.Popen(
        ["python3", str(script_path)],
        stdout=open(LOG_FILE, 'a'),
        stderr=subprocess.STDOUT,
        start_new_session=True
    )
    
    # Save PID
    save_pid(process.pid)
    
    # Update state
    state = load_state()
    state.update({
        "running": True,
        "started_at": datetime.now().isoformat(),
        "duration": duration_str,
        "pid": process.pid
    })
    save_state(state)
    
    print(f"✓ Started enrichment (PID: {process.pid})")
    print(f"  Duration: {duration_str}")
    print(f"  Log: {LOG_FILE}")
    print(f"  State: {STATE_FILE}")
    
    return 0

def stop_enrichment():
    """Stop enrichment loop"""
    pid = get_pid()
    if not pid:
        print("No enrichment process found")
        return 0
    
    if not is_process_running(pid):
        print(f"Process {pid} not running")
        # Clean up PID file
        if PID_FILE.exists():
            PID_FILE.unlink()
        return 0
    
    print(f"Stopping enrichment (PID: {pid})...")
    
    try:
        # Send SIGTERM for graceful shutdown
        os.kill(pid, signal.SIGTERM)
        
        # Wait a bit
        import time
        time.sleep(2)
        
        # Force kill if still running
        if is_process_running(pid):
            os.kill(pid, signal.SIGKILL)
            print(f"✓ Force stopped (SIGKILL)")
        else:
            print(f"✓ Gracefully stopped (SIGTERM)")
            
    except ProcessLookupError:
        print(f"Process {pid} already stopped")
    except Exception as e:
        print(f"Error stopping process: {e}")
        return 1
    
    # Clean up
    if PID_FILE.exists():
        PID_FILE.unlink()
    
    # Update state
    state = load_state()
    state.update({
        "running": False,
        "stopped_at": datetime.now().isoformat(),
        "status": "stopped"
    })
    save_state(state)
    
    return 0

def show_status():
    """Show enrichment status"""
    state = load_state()
    pid = get_pid()
    
    print("Weave Enrichment Status")
    print("=" * 40)
    
    if state.get("running") and pid and is_process_running(pid):
        print(f"Status: RUNNING (PID: {pid})")
        print(f"Started: {state.get('started_at', 'Unknown')}")
        print(f"Duration: {state.get('duration', 'Unknown')}")
        
        # Calculate remaining time
        started = datetime.fromisoformat(state["started_at"])
        duration_str = state.get("duration", "0h")
        
        # Parse duration
        import re
        hours = 0
        minutes = 0
        if "h" in duration_str:
            match = re.search(r'(\d+)h', duration_str)
            if match:
                hours = int(match.group(1))
        if "m" in duration_str:
            match = re.search(r'(\d+)m', duration_str)
            if match:
                minutes = int(match.group(1))
        
        total_seconds = hours * 3600 + minutes * 60
        if total_seconds > 0:
            elapsed = (datetime.now() - started).total_seconds()
            remaining = max(0, total_seconds - elapsed)
            remaining_hours = remaining / 3600
            
            if remaining > 0:
                print(f"Remaining: {remaining_hours:.1f} hours")
                expires = started + timedelta(seconds=total_seconds)
                print(f"Expires: {expires.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print("Status: EXPIRING (will stop soon)")
        
        # Show progress
        print(f"Targets processed: {state.get('targets_processed', 0)}")
        print(f"Current batch: {state.get('current_batch', 0)}")
        if state.get('last_target'):
            print(f"Last target: {state['last_target']}")
            
    else:
        print("Status: STOPPED")
        if state.get('completed_at'):
            print(f"Last completed: {state['completed_at']}")
        if state.get('final_processed'):
            print(f"Total processed: {state['final_processed']}")
    
    # Show log location
    if LOG_FILE.exists():
        size_mb = LOG_FILE.stat().st_size / (1024 * 1024)
        print(f"\nLog: {LOG_FILE} ({size_mb:.1f} MB)")
    
    return 0

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 weave_enrichment_control.py start --duration 8h")
        print("  python3 weave_enrichment_control.py stop")
        print("  python3 weave_enrichment_control.py status")
        print("\nDurations: 1h, 5h, 8h, 5h30m, 30m")
        return 1
    
    command = sys.argv[1].lower()
    
    if command == "start":
        if len(sys.argv) < 4 or sys.argv[2] != "--duration":
            print("Error: Missing duration")
            print("Usage: python3 weave_enrichment_control.py start --duration 8h")
            return 1
        return start_enrichment(sys.argv[3])
    
    elif command == "stop":
        return stop_enrichment()
    
    elif command == "status":
        return show_status()
    
    else:
        print(f"Unknown command: {command}")
        print("Use: start, stop, status")
        return 1

if __name__ == "__main__":
    from datetime import timedelta
    sys.exit(main())
