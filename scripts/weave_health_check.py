#!/usr/bin/env python3
"""
Weave enrichment health check.
Checks if enrichment process is running, checks recent logs, and reports status.
"""
import subprocess
import sys
import os

def main():
    skill_path = "<hermes-root>/skills/ocas-weave"
    
    # Check if enrichment control script exists
    control_script = os.path.join(skill_path, "scripts", "enrichment_control.py")
    if not os.path.exists(control_script):
        print(f"ERROR: Enrichment control script not found: {control_script}")
        return False
    
    # Run health check
    result = subprocess.run(
        ["python3", control_script, "status"],
        capture_output=True, text=True,
        cwd=skill_path
    )
    
    if result.returncode == 0:
        print("Enrichment health check passed")
        if result.stdout.strip():
            print(result.stdout.strip())
        return True
    else:
        print(f"ERROR: Health check failed")
        if result.stderr.strip():
            print(result.stderr.strip())
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
