#!/bin/bash
# Refresh the weave visualizer database snapshot
set -e

WEAVE_DB="<hermes-root>/commons/db/ocas-weave/weave.lbug"
WEAVE_WAL="<hermes-root>/commons/db/ocas-weave/weave.lbug.wal"
SNAPSHOT="<hermes-root>/commons/db/ocas-weave/snapshots/weave_viz_copy.lbug"
SNAPSHOT_WAL="<hermes-root>/commons/db/ocas-weave/snapshots/weave_viz_copy.lbug.wal"

# Force kill bridge
systemctl kill --signal=SIGKILL ladybug-bridge-weave.service 2>/dev/null || true
sleep 1

# Copy DB and WAL
cp "$WEAVE_DB" "$SNAPSHOT"
cp "$WEAVE_WAL" "$SNAPSHOT_WAL" 2>/dev/null || true

# Restart bridge
systemctl start ladybug-bridge-weave.service

echo "Snapshot refreshed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
