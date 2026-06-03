# Experiment 03: 5s CSI Extraction And Analysis

Goal: save a 5-second dual-RX IQ capture and extract CSI into `H.npy`.

Run with TX already active:

```bash
bash capture_5s_and_extract.sh
```

Output data:

```text
data/test_rx_5s/rx0.fc32
data/test_rx_5s/rx1.fc32
data/test_rx_5s/H.npy
data/test_rx_5s/csi_info.json
data/test_rx_5s/capture_config.json
data/test_rx_5s/probe_metadata.json
```

`H.npy` layout:

```text
[frame, rx, tx, active_carrier]
```

The current active carrier count is 52. Use `csi_info.json` for extracted frame
starts, active carrier indices, and CFO statistics.

For the current WiFi-like CSI mode, the common config uses:

```text
sample_rate = 2 MS/s
probe_rate = 1000 Hz
frame_len = 2000 samples
pilot_repeats_per_tx = 4
```

The extractor averages the repeated pilots inside each frame. It does not smooth
CSI across neighboring frames.

Basic analysis:

```bash
python3 analyze_csi_basic.py
```

Generated outputs:

```text
analysis/csi_amplitude_heatmaps.png
analysis/mean_amplitude_timeseries.png
analysis/relative_rx_phase_timeseries.png
analysis/adjacent_frame_stability.png
analysis/sanitization_stability_comparison.png
analysis/smoothed_mean_amplitude_stability.png
analysis/analysis_summary.json
EXPERIMENT_ANALYSIS.md
SANITIZATION_AND_STABILITY_ANALYSIS.md
```
