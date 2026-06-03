# Experiment 04: WiFi-Like CSI Estimation

Goal: estimate 2x2 CSI with a frame structure closer to a real WiFi training
packet, while avoiding cross-frame smoothing that can hide short-term channel
events.

## Frame Design

The transmitted frame is:

```text
STF-like repeated short training
LTF-like repeated long training
TX0 pilot block: 4 repeated OFDM pilot symbols
TX1 pilot block: 4 repeated OFDM pilot symbols
guard
```

Current shared config:

```text
center frequency = 1800 MHz
sample rate = 2 MS/s
probe rate = 1000 Hz
FFT length = 64
CP length = 16
active carriers = 52
subcarrier spacing = 31.25 kHz
frame length = 2000 samples
pilot repeats per TX = 4
```

At 2 MS/s, one OFDM symbol with CP is 80 samples, or 40 us. Four repeated
pilot symbols occupy 160 us per TX. This is short enough to treat the channel as
approximately unchanged inside one frame, so the extractor can average repeated
pilots without averaging across frames.

## Why This Is Closer To WiFi

Real WiFi packets use short training for coarse detection, long training for
fine timing/CFO/channel estimation, and known OFDM training symbols for CSI.
This experiment keeps that structure in a simplified SDR-friendly form:

```text
STF-like section -> frame detection
LTF-like section -> timing and CFO estimate
known OFDM pilots -> per-subcarrier H estimate
TDM TX blocks -> 2x2 MIMO separation
```

It is still not a full IEEE 802.11 PHY packet. The main simplification is that
TX0 and TX1 use time-division pilot blocks instead of standard HT/VHT/HE MIMO
training fields.

## Run Order

Terminal 1, start realtime monitor first:

```bash
bash start_rx_monitor.sh
```

Terminal 2, start TX:

```bash
bash start_tx.sh
```

After the monitor shows stable frame detection, capture 5 seconds:

```bash
bash capture_5s_and_extract.sh experiments/04_wifi_like_csi_estimation/data/test_rx_5s
```

Analyze the extracted CSI:

```bash
python3 analyze_csi_basic.py --capture-dir experiments/04_wifi_like_csi_estimation/data/test_rx_5s --out-dir experiments/04_wifi_like_csi_estimation/analysis
```

## Expected Checks

Good synchronization should show:

```text
detected rate close to 1000 Hz
frames close to expected frames
sync metric clearly above threshold
few or no RX overflows
few or no TX underflows
```

Good CSI stability for a static short capture should show:

```text
smaller adjacent-frame amplitude steps than the old single-pilot frame
higher adjacent-frame amplitude correlation
no need for cross-frame smoothing to make amplitude look stable
```

Complex CSI phase may still drift because the two B210 devices do not share a
hardware clock. For sensing, start from amplitude and relative RX phase, then
apply per-frame CSI sanitization only when the analysis needs phase.
