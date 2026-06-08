# CSI MIMO Sensing With USRP B210

This repository is a USRP B210 2x2 MIMO CSI sensing prototype. It separates the
workflow into three stages:

```text
01_spectrum_survey  -> find a clean RF band
02_iq_capture       -> transmit probes and save raw dual-RX IQ
03_csi_extraction   -> extract and analyze CSI offline
```

The current target setup is two USRP B210 devices, one for 2-channel TX and one
for 2-channel RX. The default configuration uses a clean VERT900-compatible test
point at 1890 MHz, 20 MS/s sampling, and a WiFi-like HT-LTF sounding frame for
2x2 CSI. The default active subcarrier count is reduced to 24 carriers for the
current low-SNR RF bring-up tests; set `frame.active_carrier_count` back to 52
for full-width WiFi-like experiments.

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
bash 02_iq_capture/start_rx_monitor.sh --sync-tx-mode tx0_only --threshold 0.2
bash 02_iq_capture/start_tx.sh --sync-tx-mode tx0_only
```

Capture raw IQ:

```bash
bash 02_iq_capture/capture_raw_iq.sh
```

One-shot 0.2 s capture. This starts TX first, waits until the TX log contains
`Transmitting. Press Ctrl+C to stop.`, then captures dual-RX IQ:

```bash
# Default: tx0_only, 0.2 s, timestamped output directory.
bash 02_iq_capture/capture_once_tx_then_rx.sh

# Explicit output directory, TX-chain mode, and duration.
bash 02_iq_capture/capture_once_tx_then_rx.sh data/captures/one_shot_tx0 tx0_only 0.2
bash 02_iq_capture/capture_once_tx_then_rx.sh data/captures/one_shot_both both 0.2
```

Before blaming the RF link, verify the locally generated TX frame power. This
checks whether STF/L-LTF/HT-LTF are generated with the expected power:

```bash
python3 02_iq_capture/check_tx_waveform_regions.py --tx-chain-mode tx0_only
python3 02_iq_capture/check_tx_waveform_regions.py --tx-chain-mode both
```

To test a narrower occupied band, edit `config/default_config.json`:

```json
"active_carrier_count": 24
```

With 20 MS/s and FFT=64, 24 active carriers occupy about 7.5 MHz instead of the
52-carrier span of about 16.25 MHz.

Single-TX isolation test:

```bash
# Terminal 1: transmit only from TX0, keep TX1 silent.
bash 02_iq_capture/start_tx0_only.sh

# Terminal 2: monitor RX power/SNR first. Save IQ only if the quality table is OK.
bash 02_iq_capture/start_rx_monitor_tx0_only.sh

# Terminal 3: save 2-RX raw IQ with matching metadata.
bash 02_iq_capture/capture_tx0_only_iq.sh

# Terminal 1: stop TX0, then transmit only from TX1.
bash 02_iq_capture/start_tx1_only.sh

# Terminal 2: monitor RX power/SNR first. Save IQ only if the quality table is OK.
bash 02_iq_capture/start_rx_monitor_tx1_only.sh

# Terminal 3: save 2-RX raw IQ with matching metadata.
bash 02_iq_capture/capture_tx1_only_iq.sh
```

In the monitor UI, use the quality table as the pre-capture gate:

```text
HT1 SNR and HT2 SNR should both be clearly above guard, preferably > 6 dB.
The active |H| TX row should not sit at the noise floor.
Capture gate should show OK before saving raw IQ.
```

At 20 MS/s, the monitor is intentionally lightweight. If the monitor still causes
USRP overflow/underflow on a slow host, reduce its analysis load:

```bash
bash 02_iq_capture/start_rx_monitor.sh --analysis-seconds 0.005 --update-interval-ms 1500
```

The default single-TX output directories are:

```text
data/captures/single_tx_tx0
data/captures/single_tx_tx1
```

Extract CSI offline:

```bash
python3 03_csi_extraction/extract_csi_wifi_like.py \
  --capture-dir data/captures/raw_iq_001 \
  --out-dir results/raw_iq_001/wifi_like_debug
```

For single-TX captures, use matching output names:

```bash
python3 03_csi_extraction/extract_csi_wifi_like.py \
  --capture-dir data/captures/single_tx_tx0 \
  --out-dir results/single_tx_tx0/wifi_like_debug

python3 03_csi_extraction/extract_csi_wifi_like.py \
  --capture-dir data/captures/single_tx_tx1 \
  --out-dir results/single_tx_tx1/wifi_like_debug
```

Check single-TX quality:

```bash
python3 03_csi_extraction/inspect_single_tx_quality.py \
  --capture-dir data/captures/single_tx_tx0 \
  --h-file results/single_tx_tx0/wifi_like_debug/H_wifi_ht_ltf_raw.npy \
  --summary-file results/single_tx_tx0/wifi_like_debug/wifi_ht_ltf_extraction_summary.json

python3 03_csi_extraction/inspect_single_tx_quality.py \
  --capture-dir data/captures/single_tx_tx1 \
  --h-file results/single_tx_tx1/wifi_like_debug/H_wifi_ht_ltf_raw.npy \
  --summary-file results/single_tx_tx1/wifi_like_debug/wifi_ht_ltf_extraction_summary.json
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
fixed frame-grid recovery
low-quality frame dropping
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
