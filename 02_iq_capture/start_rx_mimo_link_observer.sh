#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 02_iq_capture/rx_mimo_link_observer_gui.py "$@"
