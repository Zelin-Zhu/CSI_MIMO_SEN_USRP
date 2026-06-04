# Frame Design

The project currently uses a WiFi-like HT-LTF sounding frame generated in
`common/frame_design.py`. The goal is CSI estimation, not full WiFi packet
decoding.

Current defaults:

```text
center_freq = 5825 MHz
sample_rate = 20 MS/s
fft_len = 64
cp_len = 16
active carriers = -26..-1 and 1..26
subcarrier_spacing = 312.5 kHz
probe_rate = 1000 Hz
frame_format = wifi_ht20_2x2_ltf_sounding
sync_tx_mode = both
```

The previous frame used common STF/LTF training followed by TDM TX pilots. The
current default frame uses a 2x2 orthogonal MIMO LTF sounding core:

```text
STF / packet detection
L-LTF-like common timing and CFO
MIMO-LTF1: TX0 +LTF, TX1 +LTF
MIMO-LTF2: TX0 +LTF, TX1 -LTF
guard
```

This allows each RX to solve for TX0 and TX1 channels from simultaneous MIMO
training symbols, closer to commercial WiFi MIMO training than identical LTF
transmission on both TX antennas.

For debugging low packet-correlation cases, use `--sync-tx-mode tx0_only` so
only TX0 sends STF/L-LTF while both TX chains still send the orthogonal HT-LTF
CSI sounding symbols. This helps determine whether simultaneous identical sync
training from TX0/TX1 is causing destructive combining at the RX.

For each RX antenna and each active subcarrier, the two HT-LTF observations are:

```text
Y1[k] = H0[k] X[k] + H1[k] X[k]
Y2[k] = H0[k] X[k] - H1[k] X[k]
```

The extractor estimates:

```text
H0[k] = (Y1[k] + Y2[k]) / (2 X[k])
H1[k] = (Y1[k] - Y2[k]) / (2 X[k])
```

This is the important difference from the old custom TDM pilot frame. Both TX
chains are active during the MIMO training field, and the TX streams are
separated by the known orthogonal LTF mapping.
