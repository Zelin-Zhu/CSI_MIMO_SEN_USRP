#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 rx_csi_monitor_gui.py "$@"
