# Frame Design

The project currently uses a WiFi-like OFDM probe frame generated in
`common/frame_design.py`.

Current defaults:

```text
sample_rate = 2 MS/s
fft_len = 64
cp_len = 16
active carriers = -26..-1 and 1..26
probe_rate = 1000 Hz
pilot_repeats_per_tx = 4
```

The previous frame used common STF/LTF training followed by TDM TX pilots. The
next intended frame update is 2x2 orthogonal MIMO LTF training:

```text
STF / packet detection
L-LTF-like common timing and CFO
MIMO-LTF1: TX0 +LTF, TX1 +LTF
MIMO-LTF2: TX0 +LTF, TX1 -LTF
pilot or sensing symbols
guard
```

This allows each RX to solve for TX0 and TX1 channels from simultaneous MIMO
training symbols, closer to commercial WiFi MIMO training than identical LTF
transmission on both TX antennas.
