#!/bin/bash
# Overnight discovery run. caffeinate keeps the Mac awake (idle + system assertions)
# for the lifetime of the pipeline. Safe to run manually: ./run_overnight.sh --demo
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Overnight discovery starting"
UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"
caffeinate -i -s "$UV_BIN" run python main.py "$@"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Overnight discovery finished"