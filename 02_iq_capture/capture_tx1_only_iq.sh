#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
OUT_DIR="${1:-data/captures/single_tx_tx1}"
if [[ $# -ge 1 ]]; then
  shift
fi
python3 02_iq_capture/rx_capture_2ch.py \
  --out-dir "$OUT_DIR" \
  --tx-chain-mode tx1_only \
  "$@"
