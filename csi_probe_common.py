#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Any
import numpy as np

@dataclass(frozen=True)
class ProbeConfig:
    sample_rate: float = 1e6
    center_freq: float = 1800e6  # Change to a locally permitted RF frequency.
    fft_len: int = 64
    cp_len: int = 16
    probe_rate_hz: float = 50.0
    tx_scale: float = 0.20
    seed: int = 20260602

    @property
    def sym_len(self) -> int:
        return self.fft_len + self.cp_len

    @property
    def frame_len(self) -> int:
        return int(round(self.sample_rate / self.probe_rate_hz))

    @property
    def active_carriers(self) -> np.ndarray:
        return np.array(list(range(-26, 0)) + list(range(1, 27)), dtype=np.int32)

    @property
    def subcarrier_spacing_hz(self) -> float:
        return self.sample_rate / self.fft_len

    @property
    def active_carrier_offsets_hz(self) -> np.ndarray:
        return self.active_carriers.astype(np.float64) * self.subcarrier_spacing_hz

    @property
    def active_carrier_range_hz(self) -> tuple[float, float]:
        offsets = self.active_carrier_offsets_hz
        return float(np.min(offsets)), float(np.max(offsets))

    @property
    def active_carrier_center_span_hz(self) -> float:
        low_hz, high_hz = self.active_carrier_range_hz
        return high_hz - low_hz

    @property
    def active_carrier_edge_span_hz(self) -> float:
        return self.active_carrier_center_span_hz + self.subcarrier_spacing_hz

CFG = ProbeConfig()
CONFIG_PATH = Path(__file__).with_name("usrp_config.json")

DEFAULT_RUNTIME_CONFIG: dict[str, dict[str, Any]] = {
    "radio": {
        "args": "",
        "freq": CFG.center_freq,
        "rate": CFG.sample_rate,
    },
    "rx_gui": {
        "gain": 20.0,
        "antenna": "TX/RX",
        "fft_size": 2048,
    },
    "rx_capture": {
        "gain": 20.0,
        "antenna": "TX/RX",
        "seconds": 60.0,
        "out_dir": "capture_001",
        "probe_rate": CFG.probe_rate_hz,
        "tx_scale": CFG.tx_scale,
    },
    "rx_monitor": {
        "gain": 20.0,
        "antenna": "TX/RX",
        "buffer_seconds": 0.5,
        "update_interval_ms": 250,
        "threshold": 0.35,
        "min_frame_ratio": 0.80,
        "max_frames_display": 80,
    },
    "tx": {
        "gain": 10.0,
        "antenna": "TX/RX",
        "probe_rate": CFG.probe_rate_hz,
        "tx_scale": CFG.tx_scale,
    },
}


def load_runtime_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    merged: dict[str, Any] = {
        section: values.copy() for section, values in DEFAULT_RUNTIME_CONFIG.items()
    }
    config_path = Path(path)
    if not config_path.exists():
        return merged
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for section, values in config.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values
    return merged


def runtime_defaults(section: str, path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    config = load_runtime_config(path)
    defaults = dict(config.get("radio", {}))
    defaults.update(config.get(section, {}))
    return defaults

def _freq_vector(values: np.ndarray, cfg: ProbeConfig = CFG) -> np.ndarray:
    freq = np.zeros(cfg.fft_len, dtype=np.complex64)
    carriers = cfg.active_carriers
    if len(values) != len(carriers):
        raise ValueError(f"Expected {len(carriers)} values, got {len(values)}")
    for carrier, value in zip(carriers, values):
        freq[int(carrier) % cfg.fft_len] = value
    return freq

def _with_cp(useful_td: np.ndarray, cfg: ProbeConfig = CFG) -> np.ndarray:
    useful_td = np.asarray(useful_td, dtype=np.complex64)
    return np.concatenate([useful_td[-cfg.cp_len:], useful_td]).astype(np.complex64)

def make_waveforms(cfg: ProbeConfig = CFG):
    rng = np.random.default_rng(cfg.seed)
    k = len(cfg.active_carriers)
    preamble_values = rng.choice([-1.0, 1.0], size=k).astype(np.complex64)
    pilot_values = rng.choice([-1.0, 1.0], size=k).astype(np.complex64)
    preamble_freq_raw = _freq_vector(preamble_values, cfg)
    pilot_freq_raw = _freq_vector(pilot_values, cfg)
    preamble_useful_raw = np.fft.ifft(preamble_freq_raw).astype(np.complex64)
    pilot_useful_raw = np.fft.ifft(pilot_freq_raw).astype(np.complex64)
    peak = max(float(np.max(np.abs(preamble_useful_raw))), float(np.max(np.abs(pilot_useful_raw))))
    if peak <= 0:
        raise RuntimeError("Generated an invalid all-zero waveform.")
    digital_scale = cfg.tx_scale / peak
    preamble_useful = (digital_scale * preamble_useful_raw).astype(np.complex64)
    pilot_useful = (digital_scale * pilot_useful_raw).astype(np.complex64)
    preamble_symbol = _with_cp(preamble_useful, cfg)
    pilot_symbol = _with_cp(pilot_useful, cfg)
    zero_symbol = np.zeros(cfg.sym_len, dtype=np.complex64)
    preamble_pair = np.concatenate([preamble_symbol, preamble_symbol]).astype(np.complex64)
    occupied = len(preamble_pair) + 2 * cfg.sym_len
    guard_len = cfg.frame_len - occupied
    if guard_len < 0:
        raise ValueError(f"Probe period too short: frame_len={cfg.frame_len}, occupied={occupied}")
    guard = np.zeros(guard_len, dtype=np.complex64)
    tx0 = np.concatenate([preamble_pair, pilot_symbol, zero_symbol, guard]).astype(np.complex64)
    tx1 = np.concatenate([np.zeros_like(preamble_pair), zero_symbol, pilot_symbol, guard]).astype(np.complex64)
    pilot_freq_scaled = (digital_scale * pilot_freq_raw).astype(np.complex64)
    preamble_freq_scaled = (digital_scale * preamble_freq_raw).astype(np.complex64)
    meta = {
        **asdict(cfg),
        "sym_len": cfg.sym_len,
        "frame_len": cfg.frame_len,
        "active_carriers": cfg.active_carriers.tolist(),
        "digital_scale": float(digital_scale),
        "preamble_freq_real": preamble_freq_scaled.real.tolist(),
        "preamble_freq_imag": preamble_freq_scaled.imag.tolist(),
        "pilot_freq_real": pilot_freq_scaled.real.tolist(),
        "pilot_freq_imag": pilot_freq_scaled.imag.tolist(),
    }
    return tx0, tx1, meta

def save_probe_metadata(path: str | Path, cfg: ProbeConfig = CFG) -> None:
    _, _, meta = make_waveforms(cfg)
    Path(path).write_text(json.dumps(meta, indent=2), encoding="utf-8")
