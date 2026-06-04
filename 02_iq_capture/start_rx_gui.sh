#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 01_spectrum_survey/rx_spectrum_gui.py "$@"
