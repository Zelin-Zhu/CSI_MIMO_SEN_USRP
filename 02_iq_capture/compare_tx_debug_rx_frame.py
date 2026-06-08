#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def db10(value: np.ndarray | float) -> np.ndarray | float:
    return 10.0 * np.log10(np.maximum(value, 1e-20))


def fold_power(samples: np.ndarray, frame_len: int) -> np.ndarray:
    frame_count = len(samples) // frame_len
    if frame_count < 1:
        raise ValueError("Not enough samples for one frame.")
    frames = samples[: frame_count * frame_len].reshape(frame_count, frame_len)
    return np.mean(np.abs(frames) ** 2, axis=0)


def top_segments(power_db: np.ndarray, floor_db: float, margins: list[float]) -> dict[str, list[dict[str, float]]]:
    result = {}
    for margin in margins:
        mask = power_db > floor_db + margin
        segments = []
        idx = 0
        while idx < len(mask):
            if not mask[idx]:
                idx += 1
                continue
            end = idx
            while end < len(mask) and mask[end]:
                end += 1
            segments.append(
                {
                    "start": int(idx),
                    "end": int(end),
                    "length": int(end - idx),
                    "mean_db": float(np.mean(power_db[idx:end])),
                }
            )
            idx = end
        segments.sort(key=lambda item: item["length"], reverse=True)
        result[f"floor_plus_{margin:g}db"] = segments[:8]
    return result


def region_summary(power_db: np.ndarray, meta: dict) -> dict[str, dict[str, float]]:
    regions = {
        "stf": (0, int(meta["short_training_len"])),
        "l_ltf_cp": (int(meta["short_training_len"]), int(meta["long_training_cp_len"])),
        "l_ltf1": (int(meta["ltf1_offset"]), int(meta["fft_len"])),
        "l_ltf2": (int(meta["ltf2_offset"]), int(meta["fft_len"])),
        "ht_ltf1": (int(meta["ht_ltf1_offset"]), int(meta["sym_len"])),
        "ht_ltf2": (int(meta["ht_ltf2_offset"]), int(meta["sym_len"])),
        "guard": (int(meta["occupied_len"]), min(512, int(meta["guard_len"]))),
    }
    out = {}
    for name, (start, length) in regions.items():
        seg = power_db[start : start + length]
        out[name] = {
            "start": int(start),
            "end": int(start + length),
            "length": int(length),
            "mean_db": float(np.mean(seg)),
            "max_db": float(np.max(seg)),
        }
    return out


def analyze_file(path: Path, meta: dict, floor_slice: slice) -> dict[str, object]:
    samples = np.fromfile(path, dtype=np.complex64)
    power = fold_power(samples, int(meta["frame_len"]))
    power_db = db10(power)
    floor_db = float(np.median(power_db[floor_slice]))
    return {
        "path": str(path),
        "sample_count": int(len(samples)),
        "folded_frame_count": int(len(samples) // int(meta["frame_len"])),
        "floor_db": floor_db,
        "peak_db": float(np.max(power_db)),
        "region_summary": region_summary(power_db, meta),
        "top_high_power_segments": top_segments(power_db, floor_db, [0.5, 1.0, 2.0, 3.0, 6.0]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--out-file", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    meta = json.loads((args.capture_dir / "probe_metadata.json").read_text(encoding="utf-8"))
    floor_slice = slice(int(meta["occupied_len"]) + 120, int(meta["frame_len"]))
    files = {}
    for name in ("tx_debug_tx0", "tx_debug_tx1", "rx0", "rx1"):
        path = args.capture_dir / f"{name}.fc32"
        if path.exists():
            files[name] = analyze_file(path, meta, floor_slice)
    summary = {
        "capture_dir": str(args.capture_dir),
        "expected_layout_samples": {
            "stf": [0, int(meta["short_training_len"])],
            "l_ltf": [int(meta["short_training_len"]), int(meta["sync_training_len"])],
            "ht_ltf1": [int(meta["ht_ltf1_offset"]), int(meta["ht_ltf1_offset"]) + int(meta["sym_len"])],
            "ht_ltf2": [int(meta["ht_ltf2_offset"]), int(meta["ht_ltf2_offset"]) + int(meta["sym_len"])],
            "occupied": [0, int(meta["occupied_len"])],
            "guard": [int(meta["occupied_len"]), int(meta["frame_len"])],
        },
        "files": files,
    }
    out_file = args.out_file or (args.capture_dir / "tx_debug_rx_frame_compare.json")
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
