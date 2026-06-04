#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture-dir",
        type=Path,
        default=Path("data/captures/raw_iq_001"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("results/raw_iq_001"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    h = np.load(args.capture_dir / "H.npy")
    info = json.loads((args.capture_dir / "csi_info.json").read_text(encoding="utf-8"))
    cap = json.loads((args.capture_dir / "capture_config.json").read_text(encoding="utf-8"))

    active_carriers = np.asarray(info["active_carriers"], dtype=np.int32)
    frame_count = int(h.shape[0])
    probe_rate = float(info["probe_rate_hz"])
    time_s = np.arange(frame_count, dtype=np.float64) / probe_rate

    amp_db = 20.0 * np.log10(np.abs(h) + 1e-12)
    mean_amp_db = np.mean(amp_db, axis=(0, 3))
    std_amp_db = np.std(amp_db, axis=(0, 3))

    plot_heatmaps(amp_db, time_s, active_carriers, args.out_dir)
    plot_mean_amplitude(amp_db, time_s, args.out_dir)
    plot_relative_phase(h, time_s, args.out_dir)
    stability = compute_adjacent_stability(h, amp_db)
    plot_adjacent_stability(stability, time_s[1:], args.out_dir)
    sanitization = compare_sanitization(h)
    plot_sanitization_comparison(sanitization, args.out_dir)
    smoothing = compare_smoothed_mean_amplitude(amp_db)
    plot_smoothing_comparison(smoothing, args.out_dir)

    summary = {
        "capture_dir": str(args.capture_dir),
        "H_shape": list(h.shape),
        "layout": "[frame, rx, tx, active_carrier]",
        "center_freq_hz": float(cap["center_freq"]),
        "sample_rate_hz": float(cap["sample_rate"]),
        "probe_rate_hz": probe_rate,
        "duration_s_from_frames": float(frame_count / probe_rate),
        "active_carrier_count": int(len(active_carriers)),
        "active_carriers": active_carriers.tolist(),
        "mean_amplitude_db_by_rx_tx": mean_amp_db.tolist(),
        "std_amplitude_db_by_rx_tx": std_amp_db.tolist(),
        "mean_cfo_hz_per_rx": info.get("mean_cfo_hz_per_rx"),
        "std_cfo_hz_per_rx": info.get("std_cfo_hz_per_rx"),
        "adjacent_frame_stability": stability["summary"],
        "sanitization_comparison": sanitization,
        "smoothed_mean_amplitude_stability": smoothing,
    }
    (args.out_dir / "analysis_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def plot_heatmaps(amp_db: np.ndarray, time_s: np.ndarray, carriers: np.ndarray, out_dir: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True, sharey=True)
    labels = [
        "|H| RX0<-TX0",
        "|H| RX0<-TX1",
        "|H| RX1<-TX0",
        "|H| RX1<-TX1",
    ]
    vmin = float(np.percentile(amp_db, 5))
    vmax = float(np.percentile(amp_db, 95))
    extent = [float(carriers[0]), float(carriers[-1]), float(time_s[-1]), float(time_s[0])]

    for index, ax in enumerate(axes.flat):
        rx = index // 2
        tx = index % 2
        im = ax.imshow(
            amp_db[:, rx, tx, :],
            aspect="auto",
            interpolation="nearest",
            extent=extent,
            vmin=vmin,
            vmax=vmax,
            cmap="viridis",
        )
        ax.set_title(labels[index])
        ax.set_ylabel("Time (s)")
        ax.grid(False)
    for ax in axes[-1, :]:
        ax.set_xlabel("Active carrier index")
    fig.colorbar(im, ax=axes, label="Amplitude (dB)")
    fig.suptitle("CSI Amplitude Heatmaps")
    fig.savefig(out_dir / "csi_amplitude_heatmaps.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_mean_amplitude(amp_db: np.ndarray, time_s: np.ndarray, out_dir: Path) -> None:
    mean_over_carriers = np.mean(amp_db, axis=3)
    fig, ax = plt.subplots(figsize=(12, 5))
    for rx in range(2):
        for tx in range(2):
            ax.plot(time_s, mean_over_carriers[:, rx, tx], label=f"RX{rx}<-TX{tx}", linewidth=1.0)
    ax.set_title("Mean CSI Amplitude Over Active Carriers")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Mean amplitude (dB)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.savefig(out_dir / "mean_amplitude_timeseries.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_relative_phase(h: np.ndarray, time_s: np.ndarray, out_dir: Path) -> None:
    # Relative RX phase is more stable than absolute single-link phase.
    rel_phase = np.angle(h[:, 0, :, :] * np.conj(h[:, 1, :, :]))
    rel_phase_unwrapped = np.unwrap(rel_phase, axis=0)
    mean_rel_phase = np.mean(rel_phase_unwrapped, axis=2)
    mean_rel_phase -= np.mean(mean_rel_phase, axis=0, keepdims=True)

    fig, ax = plt.subplots(figsize=(12, 5))
    for tx in range(2):
        ax.plot(time_s, mean_rel_phase[:, tx], label=f"TX{tx}: angle(RX0 * conj(RX1))", linewidth=1.0)
    ax.set_title("Mean Relative RX Phase Over Active Carriers")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Demeaned relative phase (rad)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.savefig(out_dir / "relative_rx_phase_timeseries.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def compute_adjacent_stability(h: np.ndarray, amp_db: np.ndarray) -> dict[str, object]:
    previous = h[:-1]
    current = h[1:]
    diff = current - previous
    flat_previous = previous.reshape(previous.shape[0], -1)
    flat_current = current.reshape(current.shape[0], -1)
    flat_diff = diff.reshape(diff.shape[0], -1)

    relative_change = np.linalg.norm(flat_diff, axis=1) / (
        np.linalg.norm(flat_previous, axis=1) + 1e-12
    )
    complex_corr = np.abs(np.sum(flat_current * np.conj(flat_previous), axis=1)) / (
        np.linalg.norm(flat_current, axis=1) * np.linalg.norm(flat_previous, axis=1) + 1e-12
    )
    amp_diff_abs_db = np.mean(np.abs(amp_db[1:] - amp_db[:-1]), axis=(1, 2, 3))
    phase_step_abs_rad = np.mean(np.abs(np.angle(current * np.conj(previous))), axis=(1, 2, 3))

    per_link_amp_diff_db = np.mean(np.abs(amp_db[1:] - amp_db[:-1]), axis=(0, 3))
    per_link_phase_step_rad = np.mean(np.abs(np.angle(current * np.conj(previous))), axis=(0, 3))

    summary = {
        "relative_complex_change_mean": float(np.mean(relative_change)),
        "relative_complex_change_median": float(np.median(relative_change)),
        "relative_complex_change_p95": float(np.percentile(relative_change, 95)),
        "complex_correlation_mean": float(np.mean(complex_corr)),
        "complex_correlation_median": float(np.median(complex_corr)),
        "complex_correlation_p05": float(np.percentile(complex_corr, 5)),
        "mean_abs_amplitude_step_db": float(np.mean(amp_diff_abs_db)),
        "median_abs_amplitude_step_db": float(np.median(amp_diff_abs_db)),
        "p95_abs_amplitude_step_db": float(np.percentile(amp_diff_abs_db, 95)),
        "mean_abs_phase_step_rad": float(np.mean(phase_step_abs_rad)),
        "median_abs_phase_step_rad": float(np.median(phase_step_abs_rad)),
        "p95_abs_phase_step_rad": float(np.percentile(phase_step_abs_rad, 95)),
        "mean_abs_amplitude_step_db_by_rx_tx": per_link_amp_diff_db.tolist(),
        "mean_abs_phase_step_rad_by_rx_tx": per_link_phase_step_rad.tolist(),
    }
    return {
        "relative_change": relative_change,
        "complex_corr": complex_corr,
        "amp_diff_abs_db": amp_diff_abs_db,
        "phase_step_abs_rad": phase_step_abs_rad,
        "summary": summary,
    }


def plot_adjacent_stability(stability: dict[str, object], time_s: np.ndarray, out_dir: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(time_s, stability["complex_corr"], linewidth=0.8)
    axes[0].set_title("Adjacent-Frame Complex CSI Correlation")
    axes[0].set_ylabel("|corr(H[t], H[t-1])|")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(time_s, stability["amp_diff_abs_db"], label="mean |amplitude step|", linewidth=0.8)
    axes[1].plot(time_s, stability["phase_step_abs_rad"], label="mean |phase step|", linewidth=0.8)
    axes[1].set_title("Adjacent-Frame CSI Step Size")
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("dB or rad")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    fig.savefig(out_dir / "adjacent_frame_stability.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def linear_phase_sanitize(h: np.ndarray) -> np.ndarray:
    carriers = np.array(list(range(-26, 0)) + list(range(1, 27)), dtype=np.float64)
    design = np.vstack([carriers, np.ones_like(carriers)]).T
    out = np.empty_like(h)
    for rx in range(h.shape[1]):
        for tx in range(h.shape[2]):
            phase = np.unwrap(np.angle(h[:, rx, tx, :]), axis=1)
            coeff = np.linalg.lstsq(design, phase.T, rcond=None)[0].T
            fit = coeff[:, 0, None] * carriers[None, :] + coeff[:, 1, None]
            out[:, rx, tx, :] = np.abs(h[:, rx, tx, :]) * np.exp(1j * (phase - fit))
    return out


def stability_summary_only(h: np.ndarray) -> dict[str, float]:
    amp_db = 20.0 * np.log10(np.abs(h) + 1e-12)
    return compute_adjacent_stability(h, amp_db)["summary"]


def compare_sanitization(h: np.ndarray) -> dict[str, dict[str, float]]:
    return {
        "raw_complex_h": stability_summary_only(h),
        "linear_phase_sanitized_h": stability_summary_only(linear_phase_sanitize(h)),
        "amplitude_only_abs_h": stability_summary_only(np.abs(h).astype(np.complex64)),
    }


def plot_sanitization_comparison(results: dict[str, dict[str, float]], out_dir: Path) -> None:
    labels = list(results.keys())
    corr = [results[label]["complex_correlation_mean"] for label in labels]
    amp_step = [results[label]["mean_abs_amplitude_step_db"] for label in labels]
    phase_step = [results[label]["mean_abs_phase_step_rad"] for label in labels]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    axes[0].bar(labels, corr)
    axes[0].set_title("Mean Adjacent Correlation")
    axes[0].set_ylabel("correlation")
    axes[1].bar(labels, amp_step)
    axes[1].set_title("Mean Amplitude Step")
    axes[1].set_ylabel("dB")
    axes[2].bar(labels, phase_step)
    axes[2].set_title("Mean Phase Step")
    axes[2].set_ylabel("rad")
    for ax in axes:
        ax.tick_params(axis="x", rotation=25)
        ax.grid(True, axis="y", alpha=0.3)
    fig.suptitle("Raw CSI vs Simple Sanitization")
    fig.savefig(out_dir / "sanitization_stability_comparison.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def compare_smoothed_mean_amplitude(amp_db: np.ndarray) -> dict[str, dict[str, float]]:
    mean_amp = np.mean(amp_db, axis=3)
    results: dict[str, dict[str, float]] = {}
    for window in (1, 5, 10, 20, 50, 100):
        if window == 1:
            smoothed = mean_amp
        else:
            kernel = np.ones(window, dtype=np.float64) / window
            smoothed = np.empty((mean_amp.shape[0] - window + 1, 2, 2), dtype=np.float64)
            for rx in range(2):
                for tx in range(2):
                    smoothed[:, rx, tx] = np.convolve(mean_amp[:, rx, tx], kernel, mode="valid")
        step = np.abs(smoothed[1:] - smoothed[:-1])
        results[str(window)] = {
            "mean_step_db": float(np.mean(step)),
            "median_step_db": float(np.median(step)),
            "p95_step_db": float(np.percentile(step, 95)),
            "mean_std_over_time_db": float(np.mean(np.std(smoothed, axis=0))),
        }
    return results


def plot_smoothing_comparison(results: dict[str, dict[str, float]], out_dir: Path) -> None:
    windows = np.array([int(key) for key in results.keys()])
    mean_step = np.array([results[str(window)]["mean_step_db"] for window in windows])
    std_time = np.array([results[str(window)]["mean_std_over_time_db"] for window in windows])

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax1.plot(windows, mean_step, marker="o", label="mean adjacent step")
    ax1.set_xlabel("Moving-average window (frames)")
    ax1.set_ylabel("Mean adjacent amplitude step (dB)")
    ax1.grid(True, alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(windows, std_time, marker="s", color="tab:orange", label="mean std over time")
    ax2.set_ylabel("Mean std over time (dB)")
    ax1.set_title("Smoothed Mean-Amplitude Stability")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")
    fig.savefig(out_dir / "smoothed_mean_amplitude_stability.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
