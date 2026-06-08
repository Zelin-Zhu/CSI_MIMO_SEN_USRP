# 02 IQ Capture

Goal: transmit a known 2x2 OFDM probe and capture raw dual-RX IQ. This stage does
not decide whether CSI is good; it only ensures the raw data and metadata are
complete.

Start TX:

```bash
bash 02_iq_capture/start_tx.sh
```

Start realtime monitor:

```bash
bash 02_iq_capture/start_rx_monitor.sh
```

Capture raw IQ:

```bash
bash 02_iq_capture/capture_raw_iq.sh data/captures/raw_iq_001 5
```

One-shot 0.2 s capture. This is the preferred quick capture path because it
starts TX first, waits for the TX ready log line, then starts RX:

```bash
bash 02_iq_capture/capture_once_tx_then_rx.sh data/captures/one_shot_tx0 tx0_only 0.2
```

Arguments:

```text
1. output directory, default data/captures/one_shot_<timestamp>
2. tx_chain_mode: both, tx0_only, or tx1_only, default tx0_only
3. capture seconds, default 0.2
```

The script writes `tx.log`, `rx.log`, `rx0.fc32`, `rx1.fc32`,
`capture_config.json`, and `probe_metadata.json` into the output directory.

TX waveform self-check:

```bash
python3 02_iq_capture/check_tx_waveform_regions.py --tx-chain-mode tx0_only
python3 02_iq_capture/check_tx_waveform_regions.py --tx-chain-mode both
```

This writes `results/tx_waveform_check/tx_waveform_region_summary.json` and a
region plot. Use it to confirm whether the generated HT-LTF power is actually
lower than L-LTF before debugging the RF path.

Each capture directory should contain:

```text
rx0.fc32
rx1.fc32
capture_config.json
probe_metadata.json
```

For final validation captures, save TX/RX logs too:

```bash
bash 02_iq_capture/start_tx.sh 2>&1 | tee data/captures/raw_iq_001/tx.log
bash 02_iq_capture/capture_raw_iq.sh data/captures/raw_iq_001 5 2>&1 | tee data/captures/raw_iq_001/rx.log
```

Validation rule:

```text
TX log: no underflow and no command time error during capture
RX log: no overflow during capture
```
