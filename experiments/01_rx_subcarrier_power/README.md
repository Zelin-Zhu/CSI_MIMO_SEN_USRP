# Experiment 01: RX Observes TX Subcarrier Power

Goal: verify that the RX B210 can observe the TX OFDM probe power over the
active subcarrier band.

Key settings:

```text
center frequency: 1800 MHz
sample rate: 1 MS/s
OFDM FFT length: 64
active carriers: -26..-1 and 1..26
subcarrier spacing: 15.625 kHz
active RF span: 1799.593750 MHz..1800.406250 MHz
RX antenna: TX/RX
TX antenna: TX/RX
```

Run:

```bash
bash start_rx_gui.sh
bash start_tx.sh
```

Evidence:

```text
evidence/spectrum_power_visible_01.png
evidence/spectrum_power_visible_02.png
```

Data:

```text
data/test_rx_1800/
```

This experiment is for visual RF validation only. It does not prove frame
synchronization or CSI extraction quality.
