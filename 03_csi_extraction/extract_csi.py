#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np
from scipy.signal import correlate, fftconvolve, find_peaks

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from common.frame_design import ProbeConfig, make_waveforms

def normalized_corr_metric(x: np.ndarray, template: np.ndarray) -> np.ndarray:
    corr = correlate(x, template, mode="valid", method="fft")
    win_energy = fftconvolve(np.abs(x) ** 2, np.ones(len(template), dtype=np.float32), mode="valid")
    template_energy = float(np.sum(np.abs(template) ** 2))
    return (np.abs(corr) ** 2 / (win_energy * template_energy + 1e-12)).astype(np.float32)


def extract_one_frame(
    rx: np.ndarray,
    start: int,
    cfg: ProbeConfig,
    reference_freq: np.ndarray,
    meta: dict | None = None,
):
    s, nfft, cp = cfg.sym_len, cfg.fft_len, cfg.cp_len
    meta = meta or {}

    if "ht_ltf_offsets" in meta:
        ht_offsets = [int(x) for x in meta["ht_ltf_offsets"]]
        required_len = max(ht_offsets) + s
        ltf1_offset = int(meta["ltf1_offset"])
        ltf2_offset = int(meta["ltf2_offset"])
        a = rx[start + ltf1_offset:start + ltf1_offset + nfft]
        b = rx[start + ltf2_offset:start + ltf2_offset + nfft]
        if len(a) != nfft or len(b) != nfft:
            raise ValueError("Truncated LTF")
        omega = float(np.angle(np.vdot(a, b)) / nfft)
        seg = rx[start:start + required_len]
        if len(seg) != required_len:
            raise ValueError("Truncated frame")
        idx = np.arange(len(seg), dtype=np.float64)
        seg = seg * np.exp(-1j * omega * idx)

        y_symbols = []
        for offset in ht_offsets[:2]:
            symbol = seg[offset + cp:offset + cp + nfft]
            if len(symbol) != nfft:
                raise ValueError("Truncated HT-LTF")
            y_symbols.append(np.fft.fft(symbol))
        y1, y2 = y_symbols
        bins = np.array([int(k) % nfft for k in cfg.active_carriers], dtype=np.int32)
        x = reference_freq[bins]
        tx_chain_mode = str(getattr(cfg, "tx_chain_mode", "both"))
        if tx_chain_mode == "both":
            h_tx0 = (y1[bins] + y2[bins]) / (2.0 * x + 1e-12)
            h_tx1 = (y1[bins] - y2[bins]) / (2.0 * x + 1e-12)
        elif tx_chain_mode == "tx0_only":
            h_tx0 = (y1[bins] + y2[bins]) / (2.0 * x + 1e-12)
            h_tx1 = np.zeros_like(h_tx0)
        elif tx_chain_mode == "tx1_only":
            h_tx0 = np.zeros_like(y1[bins])
            h_tx1 = (y1[bins] - y2[bins]) / (2.0 * x + 1e-12)
        else:
            raise ValueError(f"Unsupported tx_chain_mode: {tx_chain_mode}")
        return np.stack([h_tx0, h_tx1], axis=0).astype(np.complex64), omega

    if "ltf1_offset" in meta and "ltf2_offset" in meta:
        ltf1_offset = int(meta["ltf1_offset"])
        ltf2_offset = int(meta["ltf2_offset"])
        tx0_pilot_offsets = [int(x) for x in meta.get("tx0_pilot_offsets", [meta["tx0_pilot_offset"]])]
        tx1_pilot_offsets = [int(x) for x in meta.get("tx1_pilot_offsets", [meta["tx1_pilot_offset"]])]
        required_len = max(tx1_pilot_offsets) + s
        cfo_spacing = nfft
        a = rx[start + ltf1_offset:start + ltf1_offset + nfft]
        b = rx[start + ltf2_offset:start + ltf2_offset + nfft]
    else:
        tx0_pilot_offsets = [2 * s]
        tx1_pilot_offsets = [3 * s]
        required_len = 4 * s
        cfo_spacing = s
        a = rx[start + cp:start + cp + nfft]
        b = rx[start + s + cp:start + s + cp + nfft]
    if len(a) != nfft or len(b) != nfft: raise ValueError("Truncated preamble")
    omega = float(np.angle(np.vdot(a, b)) / cfo_spacing)
    seg = rx[start:start + required_len]
    if len(seg) != required_len: raise ValueError("Truncated frame")
    idx = np.arange(len(seg), dtype=np.float64)
    seg = seg * np.exp(-1j * omega * idx)
    y0_repeats = [
        np.fft.fft(seg[offset + cp:offset + cp + nfft])
        for offset in tx0_pilot_offsets
    ]
    y1_repeats = [
        np.fft.fft(seg[offset + cp:offset + cp + nfft])
        for offset in tx1_pilot_offsets
    ]
    y0 = np.mean(np.stack(y0_repeats, axis=0), axis=0)
    y1 = np.mean(np.stack(y1_repeats, axis=0), axis=0)
    bins = np.array([int(k) % nfft for k in cfg.active_carriers], dtype=np.int32)
    x = reference_freq[bins]
    return np.stack([y0[bins] / (x + 1e-12), y1[bins] / (x + 1e-12)], axis=0).astype(np.complex64), omega

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--capture-dir", type=Path, required=True)
    p.add_argument("--threshold", type=float, default=0.35)
    p.add_argument("--min-frame-ratio", type=float, default=0.80)
    p.add_argument(
        "--free-running-peaks",
        action="store_true",
        help="Use every detected peak. Default locks to the first peak and uses the fixed frame_len grid.",
    )
    return p.parse_args()

def main():
    a = parse_args(); cap = a.capture_dir
    rx0 = np.fromfile(cap / "rx0.fc32", dtype=np.complex64)
    rx1 = np.fromfile(cap / "rx1.fc32", dtype=np.complex64)
    n = min(len(rx0), len(rx1)); rx0, rx1 = rx0[:n], rx1[:n]
    capture_cfg = json.loads((cap / "capture_config.json").read_text())
    meta0 = json.loads((cap / "probe_metadata.json").read_text())
    cfg = ProbeConfig(sample_rate=float(capture_cfg["sample_rate"]), center_freq=float(capture_cfg["center_freq"]),
                      fft_len=int(meta0["fft_len"]), cp_len=int(meta0["cp_len"]),
                      active_carrier_count=int(meta0.get("active_carrier_count", capture_cfg.get("active_carrier_count", 52))),
                      probe_rate_hz=float(meta0["probe_rate_hz"]), tx_scale=float(meta0["tx_scale"]),
                      pilot_repeats_per_tx=int(meta0.get("pilot_repeats_per_tx", 1)),
                      frame_format=str(meta0.get("frame_format", "wifi_like_stf_ltf_tdm_mimo")),
                      sync_tx_mode=str(meta0.get("sync_tx_mode", "both")),
                      tx_chain_mode=str(meta0.get("tx_chain_mode", "both")),
                      seed=int(meta0["seed"]))
    tx0, _, meta = make_waveforms(cfg)
    template = tx0[: int(meta["sync_training_len"])]
    if meta0.get("frame_format") == "wifi_like_stf_ltf_tdm_mimo":
        reference_freq = (np.array(meta["pilot_freq_real"], dtype=np.float32) + 1j * np.array(meta["pilot_freq_imag"], dtype=np.float32)).astype(np.complex64)
    else:
        reference_freq = (np.array(meta["training_freq_real"], dtype=np.float32) + 1j * np.array(meta["training_freq_imag"], dtype=np.float32)).astype(np.complex64)
    metric0 = normalized_corr_metric(rx0, template)
    metric1 = normalized_corr_metric(rx1, template)
    if float(np.max(metric1)) > float(np.max(metric0)):
        metric = metric1
        sync_channel = "rx1"
    else:
        metric = metric0
        sync_channel = "rx0"
    peaks, _ = find_peaks(metric, height=a.threshold, distance=int(round(a.min_frame_ratio * cfg.frame_len)))
    if "ht_ltf_offsets" in meta:
        required_len = max(int(x) for x in meta["ht_ltf_offsets"]) + cfg.sym_len
    elif "tx1_pilot_offsets" in meta:
        required_len = max(int(x) for x in meta["tx1_pilot_offsets"]) + cfg.sym_len
    else:
        required_len = int(meta.get("tx1_pilot_offset", 3 * cfg.sym_len)) + cfg.sym_len
    peaks = peaks[peaks + required_len <= n]
    if not a.free_running_peaks and len(peaks):
        first = int(peaks[0])
        peaks = np.arange(first, n - required_len + 1, cfg.frame_len, dtype=np.int64)
    frames, starts, cfo = [], [], []
    for start in peaks:
        try:
            h0, w0 = extract_one_frame(rx0, int(start), cfg, reference_freq, meta)
            h1, w1 = extract_one_frame(rx1, int(start), cfg, reference_freq, meta)
        except ValueError:
            continue
        frames.append(np.stack([h0, h1], axis=0)); starts.append(int(start)); cfo.append([w0, w1])
    if not frames:
        raise RuntimeError("No frames extracted. Check RF/gains/antennas or try --threshold 0.15")
    H = np.stack(frames, axis=0).astype(np.complex64)
    np.save(cap / "H.npy", H)
    cfo = np.asarray(cfo, dtype=np.float64) * cfg.sample_rate / (2 * np.pi)
    info = {"H_shape": list(H.shape), "layout": "[frame, rx, tx, active_carrier]", "active_carriers": cfg.active_carriers.tolist(),
            "frame_starts_samples": starts, "probe_rate_hz": cfg.probe_rate_hz,
            "sync_channel": sync_channel,
            "frame_format": str(meta.get("frame_format", "unknown")),
            "sync_metric_max_rx0": float(np.max(metric0)),
            "sync_metric_max_rx1": float(np.max(metric1)),
            "frame_start_mode": "free_running_peaks" if a.free_running_peaks else "fixed_grid_from_first_peak",
            "mean_cfo_hz_per_rx": np.mean(cfo, axis=0).tolist(), "std_cfo_hz_per_rx": np.std(cfo, axis=0).tolist(),
            "notes": ["Use abs(H) first.", "Do not assume single-link absolute phase is calibrated across frames.",
                      "A useful relative phase is angle(H[:,0,:,:] * conj(H[:,1,:,:]))."]}
    (cap / "csi_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Extracted CSI shape: {H.shape}")
    print(f"Saved: {cap / 'H.npy'}")

if __name__ == "__main__": main()
