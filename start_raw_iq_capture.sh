#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

OUT_DIR="${1:-experiments/05_raw_iq_offline_csi/data/raw_iq_001}"
SECONDS_TO_CAPTURE="${2:-5}"

python3 rx_capture_2ch.py --seconds "$SECONDS_TO_CAPTURE" --out-dir "$OUT_DIR"
