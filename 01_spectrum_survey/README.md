# 01 Spectrum Survey

Goal: find a clean RF segment before transmitting CSI probe frames.

Run:

```bash
python3 01_spectrum_survey/rx_spectrum_gui.py
```

Useful overrides:

```bash
python3 01_spectrum_survey/rx_spectrum_gui.py --freq 1.890e9 --rate 20e6 --gain 30
```

Current default survey settings:

```text
center_freq = 1890 MHz
sample_rate = 20 MS/s
active OFDM edge span ~= 16.56 MHz
sampled RF span = 1880..1900 MHz
```

Choose a frequency with low baseline power and minimal intermittent bursts. In
the current lab environment, 1880-1900 MHz was observed to be cleaner than the
nearby 2.4 GHz WiFi band and is compatible with VERT900 antennas.
