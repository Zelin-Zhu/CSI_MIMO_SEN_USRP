# CSI MIMO Sensing With USRP B210

This repository is a USRP B210 2x2 MIMO CSI sensing prototype. It separates the
workflow into three stages:

```text
01_spectrum_survey  -> find a clean RF band
02_iq_capture       -> transmit probes and save raw dual-RX IQ
03_csi_extraction   -> extract and analyze CSI offline
```

The current target setup is two USRP B210 devices, one for 2-channel TX and one
for 2-channel RX. The default configuration uses a clean 5 GHz test point at
5825 MHz, 20 MS/s sampling, and a WiFi-like HT-LTF sounding frame for 2x2 CSI.

## Repository Layout

```text
config/              shared experiment configuration
common/              shared frame generation and config helpers
01_spectrum_survey/  RX spectrum tools for selecting a clean band
02_iq_capture/       TX/RX scripts and raw IQ capture tools
03_csi_extraction/   offline CSI extraction, correction diagnostics, analysis
data/                local raw captures, ignored by Git
results/             local CSI outputs and plots, ignored by Git
docs/                setup notes and design documentation
examples/            small example metadata files
```

## Quick Start

Copy and edit the local device config if your B210 serials differ:

```bash
cp config/devices.example.json config/devices.local.json
```

Find a clean band:

```bash
python3 01_spectrum_survey/rx_spectrum_gui.py
```

Start TX:

```bash
bash 02_iq_capture/start_tx.sh
```

Optionally monitor CSI detection:

```bash
bash 02_iq_capture/start_rx_monitor.sh
```

If packet correlation stays near the noise floor, test single-chain sync while
keeping 2x2 HT-LTF sounding:

```bash
bash 02_iq_capture/start_rx_monitor.sh --probe-rate 10000 --sync-tx-mode tx0_only --threshold 0.03
bash 02_iq_capture/start_tx.sh --probe-rate 10000 --sync-tx-mode tx0_only --gain 40 --tx-scale 0.5
```

Capture raw IQ:

```bash
bash 02_iq_capture/capture_raw_iq.sh data/captures/raw_iq_001 5
```

Extract CSI offline:

```bash
python3 03_csi_extraction/extract_csi_wifi_like.py \
  --capture-dir data/captures/raw_iq_001 \
  --out-dir results/raw_iq_001/wifi_like_debug
```

Run basic CSI analysis:

```bash
python3 03_csi_extraction/analyze_csi_basic.py \
  --capture-dir data/captures/raw_iq_001 \
  --h-file results/raw_iq_001/wifi_like_debug/H_wifi_ht_ltf_raw.npy \
  --info-file results/raw_iq_001/wifi_like_debug/wifi_ht_ltf_extraction_summary.json \
  --out-dir results/raw_iq_001
```

## Data Policy

Raw IQ and CSI matrices are intentionally ignored by Git:

```text
*.fc32
*.npy
data/captures/**
results/**
```

Use external storage, Git LFS, or a one-off `git add -f` only when a capture must
be shared for debugging.

## Current Research Status

This project is not yet equivalent to a commercial WiFi CSI NIC. It captures raw
IQ and provides an offline WiFi-like CSI extraction pipeline with diagnostics for:

```text
frame detection
LTF timing search
CFO estimation
2x2 HT-LTF orthogonal channel decoding
phase-slope sanitization diagnostics
adjacent-frame CSI stability
```

The default frame is now `wifi_ht20_2x2_ltf_sounding`: a WiFi-like CSI sounding
frame that keeps the standard WiFi CSI-estimation idea, but is not a complete
standards-decodable WiFi PPDU.

## Documentation

```text
docs/hardware_setup.md
docs/frame_design.md
docs/wifi_csi_principle.md
docs/troubleshooting.md
```
