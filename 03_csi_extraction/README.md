# 03 CSI Extraction

Goal: convert raw IQ into CSI offline and diagnose synchronization/correction
quality.

Baseline extractor:

```bash
python3 03_csi_extraction/extract_csi.py --capture-dir data/captures/raw_iq_001
```

WiFi-like diagnostic extractor:

```bash
python3 03_csi_extraction/extract_csi_wifi_like.py \
  --capture-dir data/captures/raw_iq_001 \
  --out-dir results/raw_iq_001/wifi_like_debug
```

Basic analysis:

```bash
python3 03_csi_extraction/analyze_csi_basic.py \
  --capture-dir data/captures/raw_iq_001 \
  --out-dir results/raw_iq_001
```

Important diagnostics:

```text
LTF timing metric
CFO mean/std
within-frame pilot repeat consistency
adjacent-frame complex CSI correlation
adjacent-frame amplitude step
```

The current extractor is diagnostic. It does not yet fully implement a
commercial WiFi receiver chain.
