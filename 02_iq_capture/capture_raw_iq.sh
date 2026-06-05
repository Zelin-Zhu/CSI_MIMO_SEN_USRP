#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRA_ARGS=()
if [[ $# -ge 1 ]]; then
  EXTRA_ARGS+=(--out-dir "$1")
  shift
fi
if [[ $# -ge 1 ]]; then
  EXTRA_ARGS+=(--seconds "$1")
  shift
fi

python3 02_iq_capture/rx_capture_2ch.py "${EXTRA_ARGS[@]}" "$@"
