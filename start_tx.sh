#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 tx_mimo_probe.py "$@"
