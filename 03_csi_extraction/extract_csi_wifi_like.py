#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import correlate, fftconvolve, find_peaks

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from common.frame_design import ProbeConfig, make_waveforms


def normalized_corr_metric(x: np.ndarray, template: np.ndarray) -> np.ndarray:
    corr = correlate(x, template, mode="valid", method="fft")
    win_energy = fftconvolve(
        np.abs(x) ** 2, np.ones(len(template), dtype=np.float32), mode="valid"
    )
    template_energy = float(np.sum(np.abs(template) ** 2))
    return (np.abs(corr) ** 2 / (win_energy * template_energy + 1e-12)).astype(np.float32)


def load_capture(capture_dir: Path):
    rx0 = np.fromfile(capture_dir / "rx0.fc32", dtype=np.complex64)
    rx1 = np.fromfile(capture_dir / "rx1.fc32", dtype=np.complex64)
    n = min(len(rx0), len(rx1))
    rx0, rx1 = rx0[:n], rx1[:n]
    capture_cfg = json.loads((capture_dir / "capture_config.json").read_text(encoding="utf-8"))
    meta0 = json.loads((capture_dir / "probe_metadata.json").read_text(encoding="utf-8"))
    cfg = ProbeConfig(
        sample_rate=float(capture_cfg["sample_rate"]),
        center_freq=float(capture_cfg["center_freq"]),
        fft_len=int(meta0["fft_len"]),
        cp_len=int(meta0["cp_len"]),
        probe_rate_hz=float(meta0["probe_rate_hz"]),
        tx_scale=float(meta0["tx_scale"]),
        pilot_repeats_per_tx=int(meta0.get("pilot_repeats_per_tx", 1)),
        seed=int(meta0["seed"]),
    )
    _, _, meta = make_waveforms(cfg)
    return rx0, rx1, capture_cfg, meta, cfg


def find_frame_grid(
    rx0: np.ndarray,
    rx1: np.ndarray,
    template: np.ndarray,
    cfg: ProbeConfig,
    required_len: int,
    threshold: float,
    min_frame_ratio: float,
    max_frames: int | None,
):
    metric0 = normalized_corr_metric(rx0, template)
    metric1 = normalized_corr_metric(rx1, template)
    if float(np.max(metric1)) > float(np.max(metric0)):
        metric = metric1
        sync_channel = "rx1"
    else:
        metric = metric0
        sync_channel = "rx0"
    peaks, _ = find_peaks(
        metric,
        height=threshold,
        distance=int(round(min_frame_ratio * cfg.frame_len)),
    )
    peaks = peaks[peaks + required_len <= min(len(rx0), len(rx1))]
    if not len(peaks):
        raise RuntimeError("No frame peaks found. Lower --threshold or check RX/TX.")
    starts = np.arange(int(peaks[0]), min(len(rx0), len(rx1)) - required_len + 1, cfg.frame_len)
    if max_frames is not None:
        starts = starts[:max_frames]
    return starts.astype(np.int64), {
        "sync_channel": sync_channel,
        "sync_metric_max_rx0": float(np.max(metric0)),
        "sync_metric_max_rx1": float(np.max(metric1)),
        "first_peak": int(peaks[0]),
        "detected_peak_count": int(len(peaks)),
    }


def ltf_timing_delta(
    sig: np.ndarray,
    start: int,
    meta: dict,
    nfft: int,
    search: int,
) -> tuple[int, float]:
    ltf1 = int(meta["ltf1_offset"])
    ltf2 = int(meta["ltf2_offset"])
    best_delta = 0
    best_metric = -1.0
    for delta in range(-search, search + 1):
        a0 = start + delta + ltf1
        b0 = start + delta + ltf2
        if a0 < 0 or b0 + nfft > len(sig):
            continue
        a = sig[a0 : a0 + nfft]
        b = sig[b0 : b0 + nfft]
        metric = float(np.abs(np.vdot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
        if metric > best_metric:
            best_metric = metric
            best_delta = delta
    return best_delta, best_metric


def estimate_cfo_rad_per_sample(sig: np.ndarray, start: int, meta: dict, nfft: int) -> float:
    ltf1 = int(meta["ltf1_offset"])
    ltf2 = int(meta["ltf2_offset"])
    a = sig[start + ltf1 : start + ltf1 + nfft]
    b = sig[start + ltf2 : start + ltf2 + nfft]
    if len(a) != nfft or len(b) != nfft:
        raise ValueError("Truncated LTF")
    return float(np.angle(np.vdot(a, b)) / nfft)


def remove_linear_phase_to_reference(
    current: np.ndarray,
    reference: np.ndarray,
    carriers: np.ndarray,
) -> np.ndarray:
    ratio = current / (reference + 1e-12)
    phase = np.unwrap(np.angle(ratio))
    design = np.vstack([carriers, np.ones_like(carriers)]).T
    slope, intercept = np.linalg.lstsq(design, phase, rcond=None)[0]
    return current * np.exp(-1j * (slope * carriers + intercept))


def extract_frame_repeats(
    sig: np.ndarray,
    start: int,
    cfg: ProbeConfig,
    meta: dict,
    pilot_freq: np.ndarray,
    timing_search: int,
):
    nfft, cp, sym_len = cfg.fft_len, cfg.cp_len, cfg.sym_len
    bins = np.array([int(k) % nfft for k in cfg.active_carriers], dtype=np.int32)
    tx_offsets = [
        [int(x) for x in meta["tx0_pilot_offsets"]],
        [int(x) for x in meta["tx1_pilot_offsets"]],
    ]
    required_len = max(tx_offsets[1]) + sym_len
    delta, ltf_metric = ltf_timing_delta(sig, start, meta, nfft, timing_search)
    refined_start = start + delta
    if refined_start < 0 or refined_start + required_len > len(sig):
        raise ValueError("Truncated refined frame")
    cfo = estimate_cfo_rad_per_sample(sig, refined_start, meta, nfft)
    seg = sig[refined_start : refined_start + required_len]
    seg = seg * np.exp(-1j * cfo * np.arange(required_len, dtype=np.float64))
    x = pilot_freq[bins]
    h = np.empty((2, len(tx_offsets[0]), len(bins)), dtype=np.complex64)
    for tx in range(2):
        for repeat, offset in enumerate(tx_offsets[tx]):
            symbol = seg[offset + cp : offset + cp + nfft]
            if len(symbol) != nfft:
                raise ValueError("Truncated pilot symbol")
            y = np.fft.fft(symbol)
            h[tx, repeat, :] = y[bins] / (x + 1e-12)
    return h, cfo, delta, ltf_metric


def align_repeats_to_first(hrep: np.ndarray, carriers: np.ndarray) -> np.ndarray:
    out = hrep.copy()
    for frame in range(out.shape[0]):
        for rx in range(out.shape[1]):
            for tx in range(out.shape[2]):
                reference = out[frame, rx, tx, 0]
                for repeat in range(1, out.shape[3]):
                    out[frame, rx, tx, repeat] = remove_linear_phase_to_reference(
                        out[frame, rx, tx, repeat], reference, carriers
                    )
    return out


def summarize_repeat_consistency(hrep: np.ndarray) -> dict[str, float]:
    amp_db = 20.0 * np.log10(np.abs(hrep) + 1e-12)
    repeat_corr = []
    for repeat in range(hrep.shape[3] - 1):
        a = hrep[:, :, :, repeat, :].reshape(hrep.shape[0], -1)
        b = hrep[:, :, :, repeat + 1, :].reshape(hrep.shape[0], -1)
        corr = np.abs(np.sum(b * np.conj(a), axis=1)) / (
            np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-12
        )
        repeat_corr.append(corr)
    repeat_corr = np.concatenate(repeat_corr)
    return {
        "repeat_amp_std_db_mean": float(np.mean(np.std(amp_db, axis=3))),
        "repeat_amp_std_db_median": float(np.median(np.std(amp_db, axis=3))),
        "adjacent_repeat_complex_corr_mean": float(np.mean(repeat_corr)),
        "adjacent_repeat_complex_corr_median": float(np.median(repeat_corr)),
    }


def summarize_adjacent_frames(h: np.ndarray) -> dict[str, float]:
    previous = h[:-1]
    current = h[1:]
    fp = previous.reshape(previous.shape[0], -1)
    fc = current.reshape(current.shape[0], -1)
    corr = np.abs(np.sum(fc * np.conj(fp), axis=1)) / (
        np.linalg.norm(fp, axis=1) * np.linalg.norm(fc, axis=1) + 1e-12
    )
    amp_db = 20.0 * np.log10(np.abs(h) + 1e-12)
    amp_step = np.mean(np.abs(amp_db[1:] - amp_db[:-1]), axis=(1, 2, 3))
    return {
        "complex_corr_mean": float(np.mean(corr)),
        "complex_corr_median": float(np.median(corr)),
        "mean_abs_amplitude_step_db": float(np.mean(amp_step)),
        "median_abs_amplitude_step_db": float(np.median(amp_step)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--min-frame-ratio", type=float, default=0.80)
    parser.add_argument("--timing-search", type=int, default=24)
    parser.add_argument("--max-frames", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    capture_dir = args.capture_dir
    out_dir = args.out_dir or (capture_dir / "wifi_like_debug")
    out_dir.mkdir(parents=True, exist_ok=True)

    rx0, rx1, capture_cfg, meta, cfg = load_capture(capture_dir)
    template, _, _ = make_waveforms(cfg)
    template = template[: int(meta["sync_training_len"])]
    required_len = max(int(x) for x in meta["tx1_pilot_offsets"]) + cfg.sym_len
    starts, sync_info = find_frame_grid(
        rx0,
        rx1,
        template,
        cfg,
        required_len,
        args.threshold,
        args.min_frame_ratio,
        args.max_frames,
    )
    pilot_freq = (
        np.array(meta["pilot_freq_real"], dtype=np.float32)
        + 1j * np.array(meta["pilot_freq_imag"], dtype=np.float32)
    ).astype(np.complex64)

    frames = []
    cfo = []
    timing_delta = []
    ltf_metric = []
    kept_starts = []
    for start in starts:
        try:
            rx_frames = []
            rx_cfo = []
            rx_delta = []
            rx_ltf = []
            for sig in (rx0, rx1):
                hrep, cfo_one, delta, ltf_one = extract_frame_repeats(
                    sig, int(start), cfg, meta, pilot_freq, args.timing_search
                )
                rx_frames.append(hrep)
                rx_cfo.append(cfo_one)
                rx_delta.append(delta)
                rx_ltf.append(ltf_one)
        except ValueError:
            continue
        # hrep per RX is [tx, repeat, carrier]. Store [rx, tx, repeat, carrier].
        frames.append(np.stack(rx_frames, axis=0))
        cfo.append(rx_cfo)
        timing_delta.append(rx_delta)
        ltf_metric.append(rx_ltf)
        kept_starts.append(int(start))

    if not frames:
        raise RuntimeError("No frames extracted.")

    hrep_raw = np.stack(frames, axis=0).astype(np.complex64)
    carriers = cfg.active_carriers.astype(np.float64)
    hrep_aligned = align_repeats_to_first(hrep_raw, carriers)
    h_raw = np.mean(hrep_raw, axis=3).astype(np.complex64)
    h_aligned = np.mean(hrep_aligned, axis=3).astype(np.complex64)

    np.save(out_dir / "H_wifi_like_raw_repeat_average.npy", h_raw)
    np.save(out_dir / "H_wifi_like_repeat_phase_aligned.npy", h_aligned)

    cfo_hz = np.asarray(cfo, dtype=np.float64) * cfg.sample_rate / (2.0 * np.pi)
    timing_delta = np.asarray(timing_delta, dtype=np.int32)
    ltf_metric = np.asarray(ltf_metric, dtype=np.float64)
    summary = {
        "capture_dir": str(capture_dir),
        "out_dir": str(out_dir),
        "H_shape": list(h_raw.shape),
        "H_repeat_shape": list(hrep_raw.shape),
        "sample_rate_hz": float(capture_cfg["sample_rate"]),
        "probe_rate_hz": float(meta["probe_rate_hz"]),
        "frame_len": int(meta["frame_len"]),
        "pilot_repeats_per_tx": int(meta["pilot_repeats_per_tx"]),
        "sync": sync_info,
        "frame_starts_samples": kept_starts,
        "timing_search_samples": int(args.timing_search),
        "timing_delta_mean_per_rx": np.mean(timing_delta, axis=0).tolist(),
        "timing_delta_std_per_rx": np.std(timing_delta, axis=0).tolist(),
        "ltf_metric_mean_per_rx": np.mean(ltf_metric, axis=0).tolist(),
        "mean_cfo_hz_per_rx": np.mean(cfo_hz, axis=0).tolist(),
        "std_cfo_hz_per_rx": np.std(cfo_hz, axis=0).tolist(),
        "repeat_consistency_raw": summarize_repeat_consistency(hrep_raw),
        "repeat_consistency_phase_slope_aligned": summarize_repeat_consistency(hrep_aligned),
        "adjacent_frame_raw_repeat_average": summarize_adjacent_frames(h_raw),
        "adjacent_frame_phase_slope_aligned": summarize_adjacent_frames(h_aligned),
        "notes": [
            "This is a diagnostic WiFi-like offline extractor, not a final CSI solution.",
            "It implements LTF timing refinement, LTF CFO correction, and repeat phase-slope alignment diagnostics.",
            "If repeat consistency remains low, the issue is not only common phase or linear phase slope.",
        ],
    }
    (out_dir / "wifi_like_extraction_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "frame_starts_samples"}, indent=2))


if __name__ == "__main__":
    main()
