# Experiment 05: Raw IQ Capture For Offline CSI Extraction

Goal: save raw dual-RX IQ first, then pull the data to another host and improve
the offline CSI extractor without needing repeated USRP captures.

This experiment intentionally does not generate `H.npy` during capture.

## Why Raw IQ First

The current multi-pilot frame can be detected reliably, but the extracted H is
not yet stable. The next receiver-side work is offline synchronization and
correction:

```text
STF detection
coarse CFO correction
LTF fine timing
fine CFO correction
per-symbol CPE correction
SFO or phase-slope correction
pilot-repeat consistency check
CSI estimation
```

All of these can be improved from the same `rx0.fc32` and `rx1.fc32` files.

## Run Order On The Linux USRP Host

Terminal 1, start realtime monitor first:

```bash
bash start_rx_monitor.sh
```

Terminal 2, start TX:

```bash
bash start_tx.sh
```

Check that the monitor reports stable detection:

```text
rate = 2.000 MS/s
pilot_repeats_per_tx = 4
corr max > threshold
detected rate close to 1000 Hz
frames close to expected frames
```

Terminal 3, capture raw IQ only:

```bash
bash start_raw_iq_capture.sh experiments/05_raw_iq_offline_csi/data/raw_iq_001 5
```

The second argument is capture duration in seconds. For example, 10 seconds:

```bash
bash start_raw_iq_capture.sh experiments/05_raw_iq_offline_csi/data/raw_iq_002 10
```

## Output Files

Each raw capture directory contains:

```text
rx0.fc32
rx1.fc32
capture_config.json
probe_metadata.json
```

The `.fc32` files are ignored by Git by default because they are large. If a
single raw capture must be uploaded through Git for offline debugging, force-add
only that capture:

```bash
git add -f experiments/05_raw_iq_offline_csi/data/raw_iq_001/rx0.fc32
git add -f experiments/05_raw_iq_offline_csi/data/raw_iq_001/rx1.fc32
git add experiments/05_raw_iq_offline_csi/data/raw_iq_001/capture_config.json
git add experiments/05_raw_iq_offline_csi/data/raw_iq_001/probe_metadata.json
git commit -m "Add raw IQ capture for offline CSI extraction"
git push origin main
```

Prefer Git LFS, GitHub Release, or external storage if captures become large or
frequent.

## Offline Extraction On The Analysis Host

After pulling the raw capture:

```bash
python3 extract_csi.py --capture-dir experiments/05_raw_iq_offline_csi/data/raw_iq_001
python3 analyze_csi_basic.py --capture-dir experiments/05_raw_iq_offline_csi/data/raw_iq_001 --out-dir experiments/05_raw_iq_offline_csi/analysis/raw_iq_001
```

Later, use the improved WiFi-like extractor instead of `extract_csi.py` when it
is ready.
