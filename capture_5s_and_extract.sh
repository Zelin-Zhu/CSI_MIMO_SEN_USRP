#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

OUT_DIR="${1:-test_rx_5s}"
python3 rx_capture_2ch.py --seconds 5 --out-dir "$OUT_DIR"
python3 extract_csi.py --capture-dir "$OUT_DIR"
