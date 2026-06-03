# Experiment 04 Analysis: Multi-Pilot WiFi-Like CSI Frame

## Data Identity

This capture is the latest multi-pilot frame experiment.

```text
capture_dir = experiments/04_wifi_like_csi_estimation/data/test_rx_5s
sample_rate = 2 MS/s
probe_rate = 1000 Hz
frame_len = 2000 samples
frame_format = wifi_like_stf_ltf_tdm_mimo
pilot_repeats_per_tx = 4
H shape = [4999, 2, 2, 52]
```

The frame layout in `probe_metadata.json` is:

```text
sync_training_len = 320 samples
TX0 pilot offsets = [320, 400, 480, 560]
TX1 pilot offsets = [640, 720, 800, 880]
occupied_len = 960 samples
guard_len = 1040 samples
```

Therefore, this data is the intended 2 MS/s multi-pilot frame result.

## Synchronization Result

Frame detection is good.

```text
sync_channel = rx1
sync_metric_max_rx0 = 0.854
sync_metric_max_rx1 = 0.917
extracted frames = 4999
duration from frames = 4.999 s
frame_start_mode = fixed_grid_from_first_peak
```

The extracted frame starts are spaced by 2000 samples, which matches:

```text
sample_rate / probe_rate = 2e6 / 1000 = 2000 samples
```

So the main problem is not missing frame detection.

## CSI Stability

The adjacent-frame stability of the extracted averaged H is:

```text
complex_correlation_mean = 0.188
complex_correlation_median = 0.194
mean_abs_amplitude_step_db = 5.879 dB
median_abs_amplitude_step_db = 5.876 dB
mean_abs_phase_step_rad = 1.569 rad
```

For comparison, the previous experiment 03 result was:

```text
complex_correlation_mean = 0.390
mean_abs_amplitude_step_db = 4.840 dB
amplitude_only_correlation_mean = 0.839
```

The latest experiment 04 result is:

```text
complex_correlation_mean = 0.188
mean_abs_amplitude_step_db = 5.879 dB
amplitude_only_correlation_mean = 0.799
```

So the latest multi-pilot frame did not improve raw frame-to-frame H stability
in this capture. It improved the frame design, but the measured CSI still has
large frame-to-frame variation.

## Within-Frame Pilot Repeat Check

The important diagnostic is the consistency among the 4 repeated pilots inside
the same frame. If the channel is static over 160 us, these 4 pilots should be
very similar.

Measured from the raw IQ:

```text
frames_used = 2000
within_frame_repeat_amp_std_db_mean = 4.277 dB
within_frame_repeat_amp_std_db_median = 3.904 dB
within_frame_repeat_amp_range_db_mean = 10.979 dB
within_frame_adjacent_repeat_complex_corr_mean = 0.125
```

This is too unstable. It means the 4 pilot repeats inside the same frame are
already inconsistent before cross-frame comparison.

Using only the first pilot versus averaging all 4 pilots:

```text
single_first_pilot:
  complex_corr_mean = 0.087
  mean_abs_amp_step_db = 6.003 dB

four_pilot_average:
  complex_corr_mean = 0.187
  mean_abs_amp_step_db = 5.867 dB
```

The 4-pilot average improves over a single pilot, but the improvement is small
because the repeated pilots are not mutually consistent.

## Interpretation

The current 04 data confirms that the multi-pilot frame is being transmitted
and extracted, but it does not yet prove that the extracted H is a stable
estimate of a static channel.

The likely issue is not simply frame detection threshold. The detector finds a
stable frame grid. A sweep over candidate frame-start offsets did not find a
substantially better OFDM boundary.

The remaining likely causes are:

```text
TX/RX underrun or overflow during capture
two independent B210 clocks causing residual phase/frequency instability
TX/RX chain gain or AGC-like amplitude variation
MIMO channel timing mismatch between USRP channels
pilot power/SNR imbalance across TX/RX paths
non-WiFi-standard training not yet strong enough for fine timing/channel estimation
```

## Practical Conclusion

Experiment 04 is the correct latest multi-pilot frame experiment, but this data
does not yet show the desired static-channel behavior.

The next experiment should not add cross-frame smoothing. Instead, it should
validate signal-chain consistency in this order:

```text
1. log TX underflows and RX overflows during capture
2. capture with one TX channel only and verify repeated pilot stability
3. capture with one RX channel only and verify repeated pilot stability
4. lower probe_rate, for example 100 Hz, to increase guard and processing margin
5. test common-clock or shared-reference mode if available
6. add explicit per-frame LTF-based channel estimate and compare it with pilot H
```

The current multi-pilot averaging is useful, but the raw repeat inconsistency
shows that the hardware/stream/timing path still needs to be debugged before
the result can be treated as stable WiFi-like CSI.
