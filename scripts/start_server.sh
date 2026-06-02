#!/usr/bin/env bash
# Start the CyberGym PoC-submission server (uses the cybergym venv python).
set -euo pipefail

CYBERGYM_DIR="${CYBERGYM_DIR:-/data/seory0/projects/cybergym}"
CYBERGYM_PYTHON="${CYBERGYM_PYTHON:-$CYBERGYM_DIR/.venv/bin/python}"
PORT="${PORT:-8666}"
POC_SAVE_DIR="${POC_SAVE_DIR:-$CYBERGYM_DIR/server_poc}"

mkdir -p "$POC_SAVE_DIR"
cd "$CYBERGYM_DIR"
exec "$CYBERGYM_PYTHON" -m cybergym.server \
  --host 0.0.0.0 --port "$PORT" \
  --mask_map_path "$CYBERGYM_DIR/mask_map.json" \
  --log_dir "$POC_SAVE_DIR" \
  --db_path "$POC_SAVE_DIR/poc.db"
