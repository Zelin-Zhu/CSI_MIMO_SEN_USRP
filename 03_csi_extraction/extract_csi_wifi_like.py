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

from common.frame_design import ProbeConfig, make_waveforms, runtime_defaults


def normalized_corr_metric(x: np.ndarray, template: np.ndarray) -> np.ndarray:
    corr = correlate(x, template, mode="valid", method="fft")
    win_energy = fftconvolve(
        np.abs(x) ** 2, np.ones(len(template), dtype=np.float32), mode="valid"
    )
    template_energy = float(np.sum(np.abs(template) ** 2))
    return (np.abs(corr) ** 2 / (win_energy * template_energy + 1e-12)).astype(np.float32)


def moving_sum(x: np.ndarray, window: int) -> np.ndarray:
    if window < 1:
        raise ValueError("window must be positive")
    kernel = np.ones(window, dtype=np.float32)
    return np.convolve(x, kernel, mode="valid")


def stf_delay_autocorr_metric(
    x: np.ndarray,
    period: int = 16,
    window: int = 128,
) -> np.ndarray:
    """WiFi-style STF delay autocorrelation metric.

    The local short training field is 16-sample periodic. This detector is less
    sensitive to the exact channel impulse response than full-template matching.
    """
    if len(x) < period + window:
        return np.empty(0, dtype=np.float32)
    a = x[:-period]
    b = x[period:]
    prod = a * np.conj(b)
    p = moving_sum(prod, window)
    e0 = moving_sum(np.abs(a) ** 2, window)
    e1 = moving_sum(np.abs(b) ** 2, window)
    return (np.abs(p) ** 2 / (e0 * e1 + 1e-12)).astype(np.float32)


def ltf_matched_timing_delta(
    sig: np.ndarray,
    coarse_start: int,
    meta: dict,
    ltf_template: np.ndarray,
    search: int,
) -> tuple[int, float]:
    ltf_start = int(meta["short_training_len"])
    best_delta = 0
    best_metric = -1.0
    template_energy = float(np.sum(np.abs(ltf_template) ** 2))
    for delta in range(-search, search + 1):
        start = coarse_start + delta + ltf_start
        end = start + len(ltf_template)
        if start < 0 or end > len(sig):
            continue
        y = sig[start:end]
        metric = float(
            np.abs(np.vdot(ltf_template, y)) ** 2
            / ((np.sum(np.abs(y) ** 2) * template_energy) + 1e-12)
        )
        if metric > best_metric:
            best_metric = metric
            best_delta = delta
    return best_delta, best_metric


def build_fixed_frame_grid(
    base_start: int,
    total_len: int,
    required_len: int,
    frame_len: int,
    max_frames: int | None,
) -> np.ndarray:
    if base_start < 0:
        base_start = int(base_start % frame_len)
    starts = np.arange(base_start, total_len - required_len + 1, frame_len, dtype=np.int64)
    if max_frames is not None:
        starts = starts[:max_frames]
    return starts


def interval_summary(starts: np.ndarray) -> dict[str, float | int | None]:
    if len(starts) < 2:
        return {
            "count": int(len(starts)),
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
        }
    diff = np.diff(starts)
    return {
        "count": int(len(starts)),
        "mean": float(np.mean(diff)),
        "std": float(np.std(diff)),
        "min": int(np.min(diff)),
        "max": int(np.max(diff)),
    }


def candidate_grid_bases(
    peaks: np.ndarray,
    refined: list[int],
    frame_len: int,
    candidate_count: int = 12,
) -> list[int]:
    residues = []
    if len(peaks):
        residues.extend((np.asarray(peaks, dtype=np.int64) % frame_len).tolist())
    if refined:
        residues.extend((np.asarray(refined, dtype=np.int64) % frame_len).tolist())
    if not residues:
        return []
    counts = np.bincount(np.asarray(residues, dtype=np.int64), minlength=frame_len)
    top = np.argsort(counts)[-candidate_count:][::-1]
    candidates: list[int] = []
    for base in top:
        # Search a small neighborhood because STF autocorrelation often returns
        # a plateau, not a single sharp packet boundary.
        for delta in (0, -32, -16, -8, 8, 16, 32):
            candidate = int((int(base) + delta) % frame_len)
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def score_fixed_grid_base(
    sig: np.ndarray,
    base: int,
    cfg: ProbeConfig,
    meta: dict,
    required_len: int,
    timing_search: int,
    score_frames: int,
) -> tuple[float, int]:
    starts = build_fixed_frame_grid(base, len(sig), required_len, cfg.frame_len, score_frames)
    if len(starts) == 0:
        return -1.0, 0
    metrics = []
    for start in starts:
        _, metric = ltf_timing_delta(sig, int(start), meta, cfg.fft_len, timing_search)
        metrics.append(metric)
    return float(np.median(metrics)), int(len(metrics))


def select_fixed_grid_base(
    sig: np.ndarray,
    peaks: np.ndarray,
    refined: list[int],
    cfg: ProbeConfig,
    meta: dict,
    required_len: int,
    timing_search: int,
    score_frames: int,
) -> tuple[int, list[dict[str, float | int]]]:
    candidates = candidate_grid_bases(peaks, refined, cfg.frame_len)
    if not candidates:
        raise RuntimeError("No grid base candidates found.")
    scores = []
    for base in candidates:
        score, count = score_fixed_grid_base(
            sig, base, cfg, meta, required_len, timing_search, score_frames
        )
        scores.append({"base": int(base), "score": float(score), "score_frames": int(count)})
    scores.sort(key=lambda item: item["score"], reverse=True)
    return int(scores[0]["base"]), scores


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
        active_carrier_count=int(meta0.get("active_carrier_count", capture_cfg.get("active_carrier_count", 52))),
        probe_rate_hz=float(meta0["probe_rate_hz"]),
        tx_scale=float(meta0["tx_scale"]),
        pilot_repeats_per_tx=int(meta0.get("pilot_repeats_per_tx", 1)),
        frame_format=str(meta0.get("frame_format", "wifi_like_stf_ltf_tdm_mimo")),
        sync_tx_mode=str(meta0.get("sync_tx_mode", "both")),
        tx_chain_mode=str(meta0.get("tx_chain_mode", "both")),
        seed=int(meta0["seed"]),
    )
    _, _, meta = make_waveforms(cfg)
    return rx0, rx1, capture_cfg, meta, cfg


def find_frame_starts(
    rx0: np.ndarray,
    rx1: np.ndarray,
    template: np.ndarray,
    cfg: ProbeConfig,
    meta: dict,
    required_len: int,
    threshold: float,
    min_frame_ratio: float,
    detection_mode: str,
    frame_start_mode: str,
    ltf_search: int,
    timing_search: int,
    grid_score_frames: int,
    max_frames: int | None,
):
    if detection_mode == "template":
        metric0 = normalized_corr_metric(rx0, template)
        metric1 = normalized_corr_metric(rx1, template)
    elif detection_mode == "stf_delay":
        metric0 = stf_delay_autocorr_metric(rx0)
        metric1 = stf_delay_autocorr_metric(rx1)
    else:
        raise ValueError(f"Unsupported detection mode: {detection_mode}")

    if float(np.max(metric1)) > float(np.max(metric0)):
        metric = metric1
        sig = rx1
        sync_channel = "rx1"
    else:
        metric = metric0
        sig = rx0
        sync_channel = "rx0"

    peaks, _ = find_peaks(
        metric,
        height=threshold,
        distance=int(round(min_frame_ratio * cfg.frame_len)),
    )
    if not len(peaks):
        raise RuntimeError("No frame peaks found. Lower --threshold or check RX/TX.")

    ltf_template = template[int(meta["short_training_len"]) : int(meta["sync_training_len"])]
    refined = []
    ltf_match = []
    for peak in peaks:
        delta, metric_ltf = ltf_matched_timing_delta(
            sig, int(peak), meta, ltf_template, ltf_search
        )
        start = int(peak) + delta
        if start < 0 or start + required_len > min(len(rx0), len(rx1)):
            continue
        refined.append(start)
        ltf_match.append(metric_ltf)

    if not refined:
        raise RuntimeError("No refined frame starts found. Increase --ltf-search or check RX/TX.")

    free_starts = np.asarray(refined, dtype=np.int64)
    grid_scores: list[dict[str, float | int]] = []
    grid_base = None
    if frame_start_mode == "free_peaks":
        starts = free_starts
        if max_frames is not None:
            starts = starts[:max_frames]
    elif frame_start_mode == "fixed_grid":
        grid_base, grid_scores = select_fixed_grid_base(
            sig,
            peaks,
            refined,
            cfg,
            meta,
            required_len,
            timing_search,
            grid_score_frames,
        )
        starts = build_fixed_frame_grid(
            grid_base,
            min(len(rx0), len(rx1)),
            required_len,
            cfg.frame_len,
            max_frames,
        )
    else:
        raise ValueError(f"Unsupported frame start mode: {frame_start_mode}")

    return starts.astype(np.int64), {
        "sync_channel": sync_channel,
        "detection_mode": detection_mode,
        "frame_start_mode": frame_start_mode,
        "sync_metric_max_rx0": float(np.max(metric0)),
        "sync_metric_max_rx1": float(np.max(metric1)),
        "first_peak": int(peaks[0]),
        "detected_peak_count": int(len(peaks)),
        "refined_start_count": int(len(refined)),
        "free_peak_interval_samples": interval_summary(free_starts),
        "fixed_grid_base_sample": int(grid_base) if grid_base is not None else None,
        "fixed_grid_interval_samples": interval_summary(starts),
        "grid_candidate_scores": grid_scores[:8],
        "ltf_match_mean": float(np.mean(ltf_match)) if ltf_match else 0.0,
        "ltf_match_median": float(np.median(ltf_match)) if ltf_match else 0.0,
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


def extract_frame_ht_ltf(
    sig: np.ndarray,
    start: int,
    cfg: ProbeConfig,
    meta: dict,
    training_freq: np.ndarray,
    timing_search: int,
):
    nfft, cp, sym_len = cfg.fft_len, cfg.cp_len, cfg.sym_len
    bins = np.array([int(k) % nfft for k in cfg.active_carriers], dtype=np.int32)
    ht_offsets = [int(x) for x in meta["ht_ltf_offsets"]]
    required_len = max(ht_offsets) + sym_len
    delta, ltf_metric = ltf_timing_delta(sig, start, meta, nfft, timing_search)
    refined_start = start + delta
    if refined_start < 0 or refined_start + required_len > len(sig):
        raise ValueError("Truncated refined frame")
    cfo = estimate_cfo_rad_per_sample(sig, refined_start, meta, nfft)
    seg = sig[refined_start : refined_start + required_len]
    seg = seg * np.exp(-1j * cfo * np.arange(required_len, dtype=np.float64))
    y_symbols = []
    for offset in ht_offsets[:2]:
        symbol = seg[offset + cp : offset + cp + nfft]
        if len(symbol) != nfft:
            raise ValueError("Truncated HT-LTF symbol")
        y_symbols.append(np.fft.fft(symbol))
    y1, y2 = y_symbols
    x = training_freq[bins]
    if cfg.tx_chain_mode == "both":
        h_tx0 = (y1[bins] + y2[bins]) / (2.0 * x + 1e-12)
        h_tx1 = (y1[bins] - y2[bins]) / (2.0 * x + 1e-12)
    elif cfg.tx_chain_mode == "tx0_only":
        h_tx0 = (y1[bins] + y2[bins]) / (2.0 * x + 1e-12)
        h_tx1 = np.zeros_like(h_tx0)
    elif cfg.tx_chain_mode == "tx1_only":
        h_tx0 = np.zeros_like(y1[bins])
        h_tx1 = (y1[bins] - y2[bins]) / (2.0 * x + 1e-12)
    else:
        raise ValueError(f"Unsupported tx_chain_mode: {cfg.tx_chain_mode}")
    h = np.stack([h_tx0, h_tx1], axis=0).astype(np.complex64)
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


def sanitize_frames_to_first(h: np.ndarray, carriers: np.ndarray) -> np.ndarray:
    out = h.copy()
    if out.shape[0] < 2:
        return out
    reference = out[0]
    for frame in range(1, out.shape[0]):
        for rx in range(out.shape[1]):
            for tx in range(out.shape[2]):
                out[frame, rx, tx] = remove_linear_phase_to_reference(
                    out[frame, rx, tx], reference[rx, tx], carriers
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
    defaults = runtime_defaults("csi")
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--threshold", type=float, default=float(defaults["threshold"]))
    parser.add_argument(
        "--min-frame-ratio",
        type=float,
        default=float(defaults["min_frame_ratio"]),
    )
    parser.add_argument(
        "--detection-mode",
        choices=["stf_delay", "template"],
        default=str(defaults["detection_mode"]),
        help="Frame coarse detection mode. stf_delay is closer to WiFi packet detection.",
    )
    parser.add_argument(
        "--frame-start-mode",
        choices=["fixed_grid", "free_peaks"],
        default=str(defaults["frame_start_mode"]),
        help="fixed_grid uses one packet timing grid after detection; free_peaks keeps the old per-frame peak starts.",
    )
    parser.add_argument(
        "--ltf-search",
        type=int,
        default=int(defaults["ltf_search"]),
        help="Search radius for L-LTF matched fine timing after coarse detection.",
    )
    parser.add_argument(
        "--grid-score-frames",
        type=int,
        default=int(defaults["grid_score_frames"]),
        help="Number of frames used to score candidate fixed-grid starts.",
    )
    parser.add_argument(
        "--ltf-quality-threshold",
        type=float,
        default=float(defaults["ltf_quality_threshold"]),
        help="Drop frames whose minimum RX L-LTF repeat metric is below this value.",
    )
    parser.add_argument("--timing-search", type=int, default=int(defaults["timing_search"]))
    parser.add_argument("--max-frames", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    capture_dir = args.capture_dir
    out_dir = args.out_dir or (capture_dir / "wifi_like_debug")
    out_dir.mkdir(parents=True, exist_ok=True)

    rx0, rx1, capture_cfg, meta, cfg = load_capture(capture_dir)
    template_cfg = ProbeConfig(
        sample_rate=cfg.sample_rate,
        center_freq=cfg.center_freq,
        fft_len=cfg.fft_len,
        cp_len=cfg.cp_len,
        active_carrier_count=cfg.active_carrier_count,
        probe_rate_hz=cfg.probe_rate_hz,
        tx_scale=cfg.tx_scale,
        pilot_repeats_per_tx=cfg.pilot_repeats_per_tx,
        frame_format=cfg.frame_format,
        sync_tx_mode=cfg.sync_tx_mode,
        tx_chain_mode="both",
        seed=cfg.seed,
    )
    template, _, _ = make_waveforms(template_cfg)
    template = template[: int(meta["sync_training_len"])]
    if "ht_ltf_offsets" in meta:
        required_len = max(int(x) for x in meta["ht_ltf_offsets"]) + cfg.sym_len
    else:
        required_len = max(int(x) for x in meta["tx1_pilot_offsets"]) + cfg.sym_len
    starts, sync_info = find_frame_starts(
        rx0,
        rx1,
        template,
        cfg,
        meta,
        required_len,
        args.threshold,
        args.min_frame_ratio,
        args.detection_mode,
        args.frame_start_mode,
        args.ltf_search,
        args.timing_search,
        args.grid_score_frames,
        args.max_frames,
    )

    if "ht_ltf_offsets" in meta:
        training_freq = (
            np.array(meta["training_freq_real"], dtype=np.float32)
            + 1j * np.array(meta["training_freq_imag"], dtype=np.float32)
        ).astype(np.complex64)
        frames = []
        cfo = []
        timing_delta = []
        ltf_metric = []
        kept_starts = []
        dropped_frames = []
        candidate_starts = starts.astype(np.int64).tolist()
        for start in starts:
            try:
                rx_frames = []
                rx_cfo = []
                rx_delta = []
                rx_ltf = []
                for sig in (rx0, rx1):
                    h_one, cfo_one, delta, ltf_one = extract_frame_ht_ltf(
                        sig, int(start), cfg, meta, training_freq, args.timing_search
                    )
                    rx_frames.append(h_one)
                    rx_cfo.append(cfo_one)
                    rx_delta.append(delta)
                    rx_ltf.append(ltf_one)
            except ValueError:
                dropped_frames.append(
                    {
                        "start_sample": int(start),
                        "reason": "truncated_or_invalid_frame",
                    }
                )
                continue
            if min(rx_ltf) < args.ltf_quality_threshold:
                dropped_frames.append(
                    {
                        "start_sample": int(start),
                        "reason": "low_ltf_quality",
                        "ltf_metric_per_rx": [float(x) for x in rx_ltf],
                    }
                )
                continue
            frames.append(np.stack(rx_frames, axis=0))
            cfo.append(rx_cfo)
            timing_delta.append(rx_delta)
            ltf_metric.append(rx_ltf)
            kept_starts.append(int(start))

        if not frames:
            raise RuntimeError("No frames extracted.")

        h_raw = np.stack(frames, axis=0).astype(np.complex64)
        carriers = cfg.active_carriers.astype(np.float64)
        h_sanitized = sanitize_frames_to_first(h_raw, carriers)
        np.save(out_dir / "H_wifi_ht_ltf_raw.npy", h_raw)
        np.save(out_dir / "H_wifi_ht_ltf_phase_sanitized.npy", h_sanitized)

        cfo_hz = np.asarray(cfo, dtype=np.float64) * cfg.sample_rate / (2.0 * np.pi)
        timing_delta = np.asarray(timing_delta, dtype=np.int32)
        ltf_metric = np.asarray(ltf_metric, dtype=np.float64)
        summary = {
            "capture_dir": str(capture_dir),
            "out_dir": str(out_dir),
            "frame_format": str(meta["frame_format"]),
            "tx_chain_mode": str(meta.get("tx_chain_mode", "both")),
            "H_shape": list(h_raw.shape),
            "layout": "[frame, rx, tx, active_carrier]",
            "active_carriers": cfg.active_carriers.tolist(),
            "sample_rate_hz": float(capture_cfg["sample_rate"]),
            "probe_rate_hz": float(meta["probe_rate_hz"]),
            "frame_len": int(meta["frame_len"]),
            "sync": sync_info,
            "candidate_frame_count": int(len(candidate_starts)),
            "kept_frame_count": int(len(kept_starts)),
            "dropped_frame_count": int(len(dropped_frames)),
            "dropped_frames_preview": dropped_frames[:20],
            "candidate_frame_starts_samples": candidate_starts,
            "frame_starts_samples": kept_starts,
            "detection_mode": args.detection_mode,
            "frame_start_mode": args.frame_start_mode,
            "ltf_search_samples": int(args.ltf_search),
            "grid_score_frames": int(args.grid_score_frames),
            "ltf_quality_threshold": float(args.ltf_quality_threshold),
            "timing_search_samples": int(args.timing_search),
            "timing_delta_mean_per_rx": np.mean(timing_delta, axis=0).tolist(),
            "timing_delta_std_per_rx": np.std(timing_delta, axis=0).tolist(),
            "ltf_metric_mean_per_rx": np.mean(ltf_metric, axis=0).tolist(),
            "mean_cfo_hz_per_rx": np.mean(cfo_hz, axis=0).tolist(),
            "std_cfo_hz_per_rx": np.std(cfo_hz, axis=0).tolist(),
            "adjacent_frame_raw": summarize_adjacent_frames(h_raw),
            "adjacent_frame_phase_sanitized": summarize_adjacent_frames(h_sanitized),
            "notes": [
                "The raw file is the main CSI output after packet detection, LTF timing, CFO correction, and 2x2 HT-LTF decoding.",
                "The phase-sanitized file removes frame-to-frame common phase and linear phase slope relative to the first frame for diagnostics.",
                "This frame is WiFi-like HT-LTF sounding, not a complete standards-decodable WiFi PPDU.",
            ],
        }
        (out_dir / "wifi_ht_ltf_extraction_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in summary.items()
                    if k not in {"frame_starts_samples", "candidate_frame_starts_samples"}
                },
                indent=2,
            )
        )
        return

    pilot_freq = (
        np.array(meta["pilot_freq_real"], dtype=np.float32)
        + 1j * np.array(meta["pilot_freq_imag"], dtype=np.float32)
    ).astype(np.complex64)

    frames = []
    cfo = []
    timing_delta = []
    ltf_metric = []
    kept_starts = []
    dropped_frames = []
    candidate_starts = starts.astype(np.int64).tolist()
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
            dropped_frames.append(
                {
                    "start_sample": int(start),
                    "reason": "truncated_or_invalid_frame",
                }
            )
            continue
        if min(rx_ltf) < args.ltf_quality_threshold:
            dropped_frames.append(
                {
                    "start_sample": int(start),
                    "reason": "low_ltf_quality",
                    "ltf_metric_per_rx": [float(x) for x in rx_ltf],
                }
            )
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
        "frame_format": str(meta.get("frame_format", "wifi_like_stf_ltf_tdm_mimo")),
        "tx_chain_mode": str(meta.get("tx_chain_mode", "both")),
        "H_shape": list(h_raw.shape),
        "H_repeat_shape": list(hrep_raw.shape),
        "active_carriers": cfg.active_carriers.tolist(),
        "sample_rate_hz": float(capture_cfg["sample_rate"]),
        "probe_rate_hz": float(meta["probe_rate_hz"]),
        "frame_len": int(meta["frame_len"]),
        "pilot_repeats_per_tx": int(meta["pilot_repeats_per_tx"]),
        "sync": sync_info,
        "candidate_frame_count": int(len(candidate_starts)),
        "kept_frame_count": int(len(kept_starts)),
        "dropped_frame_count": int(len(dropped_frames)),
        "dropped_frames_preview": dropped_frames[:20],
        "candidate_frame_starts_samples": candidate_starts,
        "frame_starts_samples": kept_starts,
        "detection_mode": args.detection_mode,
        "frame_start_mode": args.frame_start_mode,
        "ltf_search_samples": int(args.ltf_search),
        "grid_score_frames": int(args.grid_score_frames),
        "ltf_quality_threshold": float(args.ltf_quality_threshold),
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
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in {"frame_starts_samples", "candidate_frame_starts_samples"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
