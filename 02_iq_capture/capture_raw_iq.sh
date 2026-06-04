#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

OUT_DIR="${1:-data/captures/raw_iq_001}"
SECONDS_TO_CAPTURE="${2:-5}"
if [[ $# -ge 1 ]]; then shift; fi
if [[ $# -ge 1 ]]; then shift; fi

python3 02_iq_capture/rx_capture_2ch.py --seconds "$SECONDS_TO_CAPTURE" --out-dir "$OUT_DIR" "$@"
