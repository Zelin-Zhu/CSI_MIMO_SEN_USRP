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
