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
SHORT_TRAINING_REPEATS = 10
SHORT_TRAINING_LEN = 16
LONG_TRAINING_CP_LEN = 32

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
        "probe_rate": CFG.probe_rate_hz,
        "tx_scale": CFG.tx_scale,
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


def _training_values(cfg: ProbeConfig = CFG) -> np.ndarray:
    # 52-value BPSK pattern in the style of the 802.11 long training field.
    values = np.array(
        [
            1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1,
            1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1,
            1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1,
            -1, -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1,
        ],
        dtype=np.complex64,
    )
    if len(values) != len(cfg.active_carriers):
        raise ValueError("Training sequence length must match active carriers.")
    return values


def _short_training(cfg: ProbeConfig = CFG) -> np.ndarray:
    rng = np.random.default_rng(cfg.seed + 1)
    unit = (
        rng.choice([-1.0, 1.0], size=SHORT_TRAINING_LEN)
        + 1j * rng.choice([-1.0, 1.0], size=SHORT_TRAINING_LEN)
    ).astype(np.complex64)
    unit /= np.sqrt(np.mean(np.abs(unit) ** 2))
    return np.tile(unit, SHORT_TRAINING_REPEATS).astype(np.complex64)


def make_waveforms(cfg: ProbeConfig = CFG):
    rng = np.random.default_rng(cfg.seed)
    k = len(cfg.active_carriers)
    training_values = _training_values(cfg)
    pilot_values = rng.choice([-1.0, 1.0], size=k).astype(np.complex64)
    training_freq_raw = _freq_vector(training_values, cfg)
    pilot_freq_raw = _freq_vector(pilot_values, cfg)
    training_useful_raw = np.fft.ifft(training_freq_raw).astype(np.complex64)
    pilot_useful_raw = np.fft.ifft(pilot_freq_raw).astype(np.complex64)
    short_raw = _short_training(cfg)
    peak = max(
        float(np.max(np.abs(short_raw))),
        float(np.max(np.abs(training_useful_raw))),
        float(np.max(np.abs(pilot_useful_raw))),
    )
    if peak <= 0:
        raise RuntimeError("Generated an invalid all-zero waveform.")
    digital_scale = cfg.tx_scale / peak
    short_training = (digital_scale * short_raw).astype(np.complex64)
    training_useful = (digital_scale * training_useful_raw).astype(np.complex64)
    pilot_useful = (digital_scale * pilot_useful_raw).astype(np.complex64)
    pilot_symbol = _with_cp(pilot_useful, cfg)
    zero_symbol = np.zeros(cfg.sym_len, dtype=np.complex64)
    long_training = np.concatenate(
        [
            training_useful[-LONG_TRAINING_CP_LEN:],
            training_useful,
            training_useful,
        ]
    ).astype(np.complex64)
    sync_training = np.concatenate([short_training, long_training]).astype(np.complex64)
    tx0_pilot_offset = len(sync_training)
    tx1_pilot_offset = tx0_pilot_offset + cfg.sym_len
    occupied = len(sync_training) + 2 * cfg.sym_len
    guard_len = cfg.frame_len - occupied
    if guard_len < 0:
        raise ValueError(f"Probe period too short: frame_len={cfg.frame_len}, occupied={occupied}")
    guard = np.zeros(guard_len, dtype=np.complex64)
    tx0 = np.concatenate([sync_training, pilot_symbol, zero_symbol, guard]).astype(np.complex64)
    tx1 = np.concatenate([sync_training, zero_symbol, pilot_symbol, guard]).astype(np.complex64)
    pilot_freq_scaled = (digital_scale * pilot_freq_raw).astype(np.complex64)
    training_freq_scaled = (digital_scale * training_freq_raw).astype(np.complex64)
    stf_len = len(short_training)
    ltf_start = stf_len
    meta = {
        **asdict(cfg),
        "frame_format": "wifi_like_stf_ltf_tdm_mimo",
        "sym_len": cfg.sym_len,
        "frame_len": cfg.frame_len,
        "active_carriers": cfg.active_carriers.tolist(),
        "digital_scale": float(digital_scale),
        "short_training_len": stf_len,
        "short_training_repeats": SHORT_TRAINING_REPEATS,
        "long_training_cp_len": LONG_TRAINING_CP_LEN,
        "long_training_len": len(long_training),
        "sync_training_len": len(sync_training),
        "ltf1_offset": ltf_start + LONG_TRAINING_CP_LEN,
        "ltf2_offset": ltf_start + LONG_TRAINING_CP_LEN + cfg.fft_len,
        "tx0_pilot_offset": tx0_pilot_offset,
        "tx1_pilot_offset": tx1_pilot_offset,
        "occupied_len": occupied,
        "guard_len": guard_len,
        "training_freq_real": training_freq_scaled.real.tolist(),
        "training_freq_imag": training_freq_scaled.imag.tolist(),
        "pilot_freq_real": pilot_freq_scaled.real.tolist(),
        "pilot_freq_imag": pilot_freq_scaled.imag.tolist(),
    }
    return tx0, tx1, meta

def save_probe_metadata(path: str | Path, cfg: ProbeConfig = CFG) -> None:
    _, _, meta = make_waveforms(cfg)
    Path(path).write_text(json.dumps(meta, indent=2), encoding="utf-8")
