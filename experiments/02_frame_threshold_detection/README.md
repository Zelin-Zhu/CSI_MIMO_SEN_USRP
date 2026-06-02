# Experiment 02: Frame Threshold And Detection

Goal: tune RX/TX parameters until preamble frame detection is stable.

The realtime monitor performs:

```text
short IQ buffer -> preamble correlation -> frame start detection -> CSI preview
```

The current frame uses a WiFi-like training structure:

```text
STF-like repeated short training
LTF-like repeated long training
TX0 pilot
TX1 pilot
guard
```

The training section is sent on both TX channels to make synchronization more
robust. The TX pilots are still separated in time for 2x2 CSI estimation.

Run:

```bash
bash start_rx_monitor.sh
bash start_tx.sh
```

Good detection:

```text
corr max is above threshold
frames is close to expected frames
detected rate is close to probe_rate
extracted frames is nonzero
CSI heatmaps are stable
```

Final stable condition observed after increasing TX strength:

```text
probe_rate: 1000 Hz
buffer: 0.5 s
expected frames: about 500
detected frames: about 497
detected rate: about 998 Hz
threshold: 0.35
```

Evidence:

```text
evidence/initial_monitor_threshold_005.png
evidence/threshold_035_low_detection.png
evidence/threshold_025_low_detection.png
evidence/threshold_035_stable_detection_after_tx_gain.png
```

Use this experiment before saving long captures. If detection is unstable,
increase TX gain or `tx_scale`, then check for RX overflows and TX underflows.
