#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from common.frame_design import CFG, ProbeConfig, make_waveforms, runtime_defaults


def db10(value: float) -> float:
    return float(10.0 * np.log10(max(value, 1e-20)))


def region_stats(samples: np.ndarray, start: int, length: int) -> dict[str, float]:
    seg = samples[start : start + length]
    power = np.abs(seg) ** 2
    return {
        "start": int(start),
        "length": int(length),
        "rms": float(np.sqrt(np.mean(power))) if len(seg) else 0.0,
        "peak": float(np.max(np.abs(seg))) if len(seg) else 0.0,
        "power_db": db10(float(np.mean(power))) if len(seg) else -200.0,
    }


def fft_symbol_stats(samples: np.ndarray, start: int, fft_len: int, active_carriers: list[int]) -> dict[str, float]:
    seg = samples[start : start + fft_len]
    if len(seg) != fft_len:
        return {"active_power_db": -200.0, "inactive_power_db": -200.0, "active_to_inactive_db": 0.0}
    spec = np.fft.fft(seg)
    active_idx = np.array([carrier % fft_len for carrier in active_carriers], dtype=np.int32)
    inactive_mask = np.ones(fft_len, dtype=bool)
    inactive_mask[active_idx] = False
    inactive_mask[0] = False
    active_power = float(np.mean(np.abs(spec[active_idx]) ** 2))
    inactive_power = float(np.mean(np.abs(spec[inactive_mask]) ** 2))
    return {
        "active_power_db": db10(active_power),
        "inactive_power_db": db10(inactive_power),
        "active_to_inactive_db": db10(active_power) - db10(inactive_power),
    }


def parse_args() -> argparse.Namespace:
    defaults = runtime_defaults("tx")
    p = argparse.ArgumentParser(
        description="Inspect locally generated TX waveform power in STF/L-LTF/HT-LTF regions."
    )
    p.add_argument("--freq", type=float, default=float(defaults["freq"]))
    p.add_argument("--rate", type=float, default=float(defaults["rate"]))
    p.add_argument("--probe-rate", type=float, default=float(defaults["probe_rate"]))
    p.add_argument("--active-carrier-count", type=int, default=int(defaults["active_carrier_count"]))
    p.add_argument("--tx-scale", type=float, default=float(defaults["tx_scale"]))
    p.add_argument("--pilot-repeats-per-tx", type=int, default=int(defaults["pilot_repeats_per_tx"]))
    p.add_argument("--frame-format", default=str(defaults["frame_format"]))
    p.add_argument("--sync-tx-mode", choices=["both", "tx0_only"], default=str(defaults["sync_tx_mode"]))
    p.add_argument(
        "--tx-chain-mode",
        choices=["both", "tx0_only", "tx1_only"],
        default=str(defaults["tx_chain_mode"]),
    )
    p.add_argument("--out-dir", type=Path, default=Path("results/tx_waveform_check"))
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = ProbeConfig(
        sample_rate=args.rate,
        center_freq=args.freq,
        fft_len=CFG.fft_len,
        cp_len=CFG.cp_len,
        active_carrier_count=args.active_carrier_count,
        probe_rate_hz=args.probe_rate,
        tx_scale=args.tx_scale,
        pilot_repeats_per_tx=args.pilot_repeats_per_tx,
        frame_format=args.frame_format,
        sync_tx_mode=args.sync_tx_mode,
        tx_chain_mode=args.tx_chain_mode,
        seed=CFG.seed,
    )
    tx0, tx1, meta = make_waveforms(cfg)

    regions: dict[str, tuple[int, int]] = {
        "stf": (0, int(meta["short_training_len"])),
        "l_ltf_cp": (int(meta["short_training_len"]), int(meta["long_training_cp_len"])),
        "l_ltf1_useful": (int(meta["ltf1_offset"]), cfg.fft_len),
        "l_ltf2_useful": (int(meta["ltf2_offset"]), cfg.fft_len),
        "ht_ltf1_symbol": (int(meta["ht_ltf1_offset"]), cfg.sym_len),
        "ht_ltf1_useful": (int(meta["ht_ltf1_offset"]) + cfg.cp_len, cfg.fft_len),
        "ht_ltf2_symbol": (int(meta["ht_ltf2_offset"]), cfg.sym_len),
        "ht_ltf2_useful": (int(meta["ht_ltf2_offset"]) + cfg.cp_len, cfg.fft_len),
        "guard": (int(meta["occupied_len"]), int(meta["guard_len"])),
    }

    summary = {
        "config": meta,
        "tx0": {},
        "tx1": {},
        "frequency_symbol_stats": {"tx0": {}, "tx1": {}},
    }
    for label, wave in (("tx0", tx0), ("tx1", tx1)):
        for name, (start, length) in regions.items():
            summary[label][name] = region_stats(wave, start, length)
        for name in ("l_ltf1_useful", "l_ltf2_useful", "ht_ltf1_useful", "ht_ltf2_useful"):
            start, _ = regions[name]
            summary["frequency_symbol_stats"][label][name] = fft_symbol_stats(
                wave, start, cfg.fft_len, meta["active_carriers"]
            )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.out_dir / "tx_waveform_region_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    try:
        import matplotlib.pyplot as plt

        t_us = np.arange(len(tx0), dtype=np.float64) / cfg.sample_rate * 1e6
        fig, ax = plt.subplots(1, 1, figsize=(12, 4), constrained_layout=True)
        ax.plot(t_us, np.abs(tx0), label="TX0 |IQ|", linewidth=1.0)
        ax.plot(t_us, np.abs(tx1), label="TX1 |IQ|", linewidth=1.0, alpha=0.8)
        for name, (start, length) in regions.items():
            if name == "guard":
                continue
            ax.axvspan(start / cfg.sample_rate * 1e6, (start + length) / cfg.sample_rate * 1e6, alpha=0.08)
            ax.text(start / cfg.sample_rate * 1e6, ax.get_ylim()[1] * 0.88, name, fontsize=8, rotation=90)
        ax.set_title("Generated TX waveform regions")
        ax.set_xlabel("Time (us)")
        ax.set_ylabel("|IQ|")
        ax.legend(loc="upper right")
        fig.savefig(args.out_dir / "tx_waveform_regions.png", dpi=160)
        plt.close(fig)
    except Exception as exc:
        summary["plot_error"] = str(exc)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"TX waveform check written to {summary_path}")
    for tx in ("tx0", "tx1"):
        print(f"{tx}:")
        for name in ("l_ltf1_useful", "l_ltf2_useful", "ht_ltf1_useful", "ht_ltf2_useful", "guard"):
            item = summary[tx][name]
            print(f"  {name:15s} rms={item['rms']:.6f}, power={item['power_db']:.1f} dB")
        for name in ("l_ltf1_useful", "ht_ltf1_useful", "ht_ltf2_useful"):
            item = summary["frequency_symbol_stats"][tx][name]
            print(f"  {name:15s} active={item['active_power_db']:.1f} dB, inactive={item['inactive_power_db']:.1f} dB")


if __name__ == "__main__":
    main()
