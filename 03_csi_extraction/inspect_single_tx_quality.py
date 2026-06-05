#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_csi_wifi_like import load_capture


def db10(x: float) -> float:
    return float(10.0 * np.log10(x + 1e-12))


def adjacent_corr(h: np.ndarray) -> dict[str, float]:
    if h.shape[0] < 2:
        return {"complex_corr_mean": 0.0, "complex_corr_median": 0.0, "amp_step_db": 0.0}
    a = h[:-1].reshape(h.shape[0] - 1, -1)
    b = h[1:].reshape(h.shape[0] - 1, -1)
    corr = np.abs(np.sum(b * np.conj(a), axis=1)) / (
        np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-12
    )
    amp = 20.0 * np.log10(np.abs(h) + 1e-12)
    amp_step = np.mean(np.abs(amp[1:] - amp[:-1]), axis=tuple(range(1, amp.ndim)))
    return {
        "complex_corr_mean": float(np.mean(corr)),
        "complex_corr_median": float(np.median(corr)),
        "amp_step_db": float(np.mean(amp_step)),
    }


def region_power(rx: np.ndarray, starts: np.ndarray, meta: dict, cfg, max_frames: int) -> dict[str, float]:
    starts = starts[:max_frames]
    regions = {
        "stf": (0, int(meta["short_training_len"])),
        "lltf": (int(meta["short_training_len"]), int(meta["sync_training_len"])),
        "ht1": (int(meta["ht_ltf1_offset"]), int(meta["ht_ltf1_offset"]) + cfg.sym_len),
        "ht2": (int(meta["ht_ltf2_offset"]), int(meta["ht_ltf2_offset"]) + cfg.sym_len),
        "guard": (int(meta["occupied_len"]), min(int(meta["occupied_len"]) + 400, cfg.frame_len)),
    }
    out = {}
    for name, (a, b) in regions.items():
        p = []
        for start in starts:
            seg = rx[int(start) + a : int(start) + b]
            if len(seg):
                p.append(float(np.mean(np.abs(seg) ** 2)))
        out[f"{name}_dbfs"] = db10(float(np.mean(p))) if p else 0.0
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--capture-dir", type=Path, required=True)
    p.add_argument("--h-file", type=Path, required=True)
    p.add_argument("--summary-file", type=Path, required=True)
    p.add_argument("--max-power-frames", type=int, default=500)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    rx0, rx1, _, meta, cfg = load_capture(args.capture_dir)
    summary = json.loads(args.summary_file.read_text(encoding="utf-8"))
    starts = np.asarray(summary["frame_starts_samples"], dtype=np.int64)
    h = np.load(args.h_file)
    tx_chain_mode = str(meta.get("tx_chain_mode", "both"))
    if tx_chain_mode == "tx0_only":
        active_h = h[:, :, 0:1, :]
        active_tx = ["tx0"]
    elif tx_chain_mode == "tx1_only":
        active_h = h[:, :, 1:2, :]
        active_tx = ["tx1"]
    else:
        active_h = h
        active_tx = ["tx0", "tx1"]

    out = {
        "capture_dir": str(args.capture_dir),
        "tx_chain_mode": tx_chain_mode,
        "active_tx": active_tx,
        "frame_count": int(h.shape[0]),
        "active_h_shape": list(active_h.shape),
        "rx0_region_power": region_power(rx0, starts, meta, cfg, args.max_power_frames),
        "rx1_region_power": region_power(rx1, starts, meta, cfg, args.max_power_frames),
        "active_h_stability": adjacent_corr(active_h),
        "notes": [
            "For tx0_only/tx1_only, only the active TX slice is included in H stability.",
            "HT1/HT2 should both be clearly above guard for a reliable single-TX check.",
        ],
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
