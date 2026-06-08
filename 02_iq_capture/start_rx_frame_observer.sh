#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 02_iq_capture/rx_frame_observer_gui.py "$@"
