#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 02_iq_capture/tx_mimo_probe.py --tx-chain-mode tx1_only "$@"
