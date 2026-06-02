#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
from scipy.signal import correlate, fftconvolve, find_peaks
from csi_probe_common import ProbeConfig, make_waveforms

def normalized_corr_metric(x: np.ndarray, template: np.ndarray) -> np.ndarray:
    corr = correlate(x, template, mode="valid", method="fft")
    win_energy = fftconvolve(np.abs(x) ** 2, np.ones(len(template), dtype=np.float32), mode="valid")
    template_energy = float(np.sum(np.abs(template) ** 2))
    return (np.abs(corr) ** 2 / (win_energy * template_energy + 1e-12)).astype(np.float32)

def extract_one_frame(rx: np.ndarray, start: int, cfg: ProbeConfig, pilot_freq: np.ndarray):
    s, nfft, cp = cfg.sym_len, cfg.fft_len, cfg.cp_len
    a = rx[start + cp:start + cp + nfft]
    b = rx[start + s + cp:start + s + cp + nfft]
    if len(a) != nfft or len(b) != nfft: raise ValueError("Truncated preamble")
    omega = float(np.angle(np.vdot(a, b)) / s)
    seg = rx[start:start + 4 * s]
    if len(seg) != 4 * s: raise ValueError("Truncated frame")
    idx = np.arange(len(seg), dtype=np.float64)
    seg = seg * np.exp(-1j * omega * idx)
    y0 = np.fft.fft(seg[2 * s + cp:2 * s + cp + nfft])
    y1 = np.fft.fft(seg[3 * s + cp:3 * s + cp + nfft])
    bins = np.array([int(k) % nfft for k in cfg.active_carriers], dtype=np.int32)
    x = pilot_freq[bins]
    return np.stack([y0[bins] / (x + 1e-12), y1[bins] / (x + 1e-12)], axis=0).astype(np.complex64), omega

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--capture-dir", type=Path, required=True)
    p.add_argument("--threshold", type=float, default=0.35)
    p.add_argument("--min-frame-ratio", type=float, default=0.80)
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
                      probe_rate_hz=float(meta0["probe_rate_hz"]), tx_scale=float(meta0["tx_scale"]), seed=int(meta0["seed"]))
    tx0, _, meta = make_waveforms(cfg)
    template = tx0[:2 * cfg.sym_len]
    metric = normalized_corr_metric(rx0, template)
    peaks, _ = find_peaks(metric, height=a.threshold, distance=int(round(a.min_frame_ratio * cfg.frame_len)))
    peaks = peaks[peaks + 4 * cfg.sym_len <= n]
    pilot_freq = (np.array(meta["pilot_freq_real"], dtype=np.float32) + 1j * np.array(meta["pilot_freq_imag"], dtype=np.float32)).astype(np.complex64)
    frames, starts, cfo = [], [], []
    for start in peaks:
        try:
            h0, w0 = extract_one_frame(rx0, int(start), cfg, pilot_freq)
            h1, w1 = extract_one_frame(rx1, int(start), cfg, pilot_freq)
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
            "mean_cfo_hz_per_rx": np.mean(cfo, axis=0).tolist(), "std_cfo_hz_per_rx": np.std(cfo, axis=0).tolist(),
            "notes": ["Use abs(H) first.", "Do not assume single-link absolute phase is calibrated across frames.",
                      "A useful relative phase is angle(H[:,0,:,:] * conj(H[:,1,:,:]))."]}
    (cap / "csi_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Extracted CSI shape: {H.shape}")
    print(f"Saved: {cap / 'H.npy'}")

if __name__ == "__main__": main()
