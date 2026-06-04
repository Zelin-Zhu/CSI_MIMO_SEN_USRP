# 01 Spectrum Survey

Goal: find a clean RF segment before transmitting CSI probe frames.

Run:

```bash
python3 01_spectrum_survey/rx_spectrum_gui.py
```

Useful overrides:

```bash
python3 01_spectrum_survey/rx_spectrum_gui.py --freq 2.484e9 --rate 2e6 --gain 30
```

The default active OFDM span at 2 MS/s is approximately:

```text
center frequency ± 0.8125 MHz
```

Choose a frequency with low baseline power and minimal intermittent bursts. In
the current lab environment, 2484 MHz was observed to be cleaner than several
nearby 2.4 GHz points.
