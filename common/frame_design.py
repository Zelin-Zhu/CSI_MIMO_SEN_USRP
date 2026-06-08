#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Any
import numpy as np

@dataclass(frozen=True)
class ProbeConfig:
    sample_rate: float = 20e6
    center_freq: float = 1890e6  # Change to a locally permitted RF frequency.
    fft_len: int = 64
    cp_len: int = 16
    active_carrier_count: int = 52
    probe_rate_hz: float = 50.0
    tx_scale: float = 0.20
    pilot_repeats_per_tx: int = 4
    frame_format: str = "wifi_ht20_2x2_ltf_sounding"
    sync_tx_mode: str = "both"
    tx_chain_mode: str = "both"
    tx1_cyclic_shift_samples: int = 4
    seed: int = 20260602

    @property
    def sym_len(self) -> int:
        return self.fft_len + self.cp_len

    @property
    def frame_len(self) -> int:
        return int(round(self.sample_rate / self.probe_rate_hz))

    @property
    def active_carriers(self) -> np.ndarray:
        if self.active_carrier_count % 2 != 0:
            raise ValueError("active_carrier_count must be even.")
        if self.active_carrier_count < 2 or self.active_carrier_count > 52:
            raise ValueError("active_carrier_count must be in the range [2, 52].")
        half = self.active_carrier_count // 2
        return np.array(list(range(-half, 0)) + list(range(1, half + 1)), dtype=np.int32)

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
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "default_config.json"
LOCAL_CONFIG_PATH = REPO_ROOT / "config" / "devices.local.json"
SHORT_TRAINING_REPEATS = 10
SHORT_TRAINING_LEN = 16
LONG_TRAINING_CP_LEN = 32

DEFAULT_PROJECT_CONFIG: dict[str, dict[str, Any]] = {
    "devices": {
        "tx_args": "",
        "rx_args": "",
        "antenna": "TX/RX",
    },
    "radio": {
        "center_freq": CFG.center_freq,
        "sample_rate": CFG.sample_rate,
        "tx_gain": 30.0,
        "rx_gain": 20.0,
    },
    "frame": {
        "fft_len": CFG.fft_len,
        "cp_len": CFG.cp_len,
        "active_carrier_count": CFG.active_carrier_count,
        "probe_rate_hz": CFG.probe_rate_hz,
        "tx_scale": CFG.tx_scale,
        "pilot_repeats_per_tx": CFG.pilot_repeats_per_tx,
        "frame_format": CFG.frame_format,
        "sync_tx_mode": CFG.sync_tx_mode,
        "tx_chain_mode": CFG.tx_chain_mode,
        "tx1_cyclic_shift_samples": CFG.tx1_cyclic_shift_samples,
    },
    "capture": {
        "seconds": 5.0,
        "output_root": "data/captures",
        "default_capture_id": "raw_iq_001",
    },
    "spectrum": {
        "fft_size": 2048,
    },
    "monitor": {
        "buffer_seconds": 0.02,
        "update_interval_ms": 1000,
        "threshold": 0.35,
        "min_frame_ratio": 0.80,
        "max_frames_display": 40,
        "analysis_seconds": 0.01,
    },
    "csi": {
        "threshold": 0.35,
        "min_frame_ratio": 0.80,
        "detection_mode": "stf_delay",
        "frame_start_mode": "fixed_grid",
        "ltf_search_samples": 320,
        "grid_score_frames": 128,
        "ltf_quality_threshold": 0.25,
        "timing_search_samples": 24,
        "enable_cfo_correction": True,
        "enable_cpe_correction": True,
        "enable_phase_slope_correction": True,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = {key: value.copy() if isinstance(value, dict) else value for key, value in base.items()}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_project_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    merged: dict[str, Any] = {
        section: values.copy() for section, values in DEFAULT_PROJECT_CONFIG.items()
    }
    config_path = Path(path)
    if config_path.exists():
        merged = _deep_merge(merged, json.loads(config_path.read_text(encoding="utf-8")))
    if LOCAL_CONFIG_PATH.exists() and config_path == CONFIG_PATH:
        merged = _deep_merge(merged, json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8")))
    return merged


def runtime_defaults(section: str, path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    config = load_project_config(path)
    devices = config["devices"]
    radio = config["radio"]
    frame = config["frame"]
    capture = config["capture"]
    monitor = config["monitor"]
    spectrum = config["spectrum"]
    csi = config["csi"]

    common_radio = {
        "freq": radio["center_freq"],
        "rate": radio["sample_rate"],
    }
    frame_defaults = {
        "probe_rate": frame["probe_rate_hz"],
        "active_carrier_count": frame.get("active_carrier_count", CFG.active_carrier_count),
        "tx_scale": frame["tx_scale"],
        "pilot_repeats_per_tx": frame["pilot_repeats_per_tx"],
        "frame_format": frame["frame_format"],
        "sync_tx_mode": frame.get("sync_tx_mode", CFG.sync_tx_mode),
        "tx_chain_mode": frame.get("tx_chain_mode", CFG.tx_chain_mode),
        "tx1_cyclic_shift_samples": frame.get(
            "tx1_cyclic_shift_samples", CFG.tx1_cyclic_shift_samples
        ),
    }
    if section == "tx":
        return {
            **common_radio,
            **frame_defaults,
            "args": devices["tx_args"],
            "gain": radio["tx_gain"],
            "antenna": devices["antenna"],
        }
    if section == "rx_capture":
        default_capture = Path(capture["output_root"]) / capture["default_capture_id"]
        return {
            **common_radio,
            **frame_defaults,
            "args": devices["rx_args"],
            "gain": radio["rx_gain"],
            "antenna": devices["antenna"],
            "seconds": capture["seconds"],
            "out_dir": str(default_capture),
        }
    if section == "rx_monitor":
        return {
            **common_radio,
            **frame_defaults,
            "args": devices["rx_args"],
            "gain": radio["rx_gain"],
            "antenna": devices["antenna"],
            "buffer_seconds": monitor["buffer_seconds"],
            "update_interval_ms": monitor["update_interval_ms"],
            "threshold": monitor["threshold"],
            "min_frame_ratio": monitor["min_frame_ratio"],
            "max_frames_display": monitor["max_frames_display"],
            "analysis_seconds": monitor.get("analysis_seconds", 0.01),
        }
    if section == "rx_gui":
        return {
            **common_radio,
            "args": devices["rx_args"],
            "gain": radio["rx_gain"],
            "antenna": devices["antenna"],
            "fft_size": spectrum["fft_size"],
            "active_carrier_count": frame.get("active_carrier_count", CFG.active_carrier_count),
        }
    if section == "csi":
        return {
            "threshold": csi["threshold"],
            "min_frame_ratio": csi["min_frame_ratio"],
            "detection_mode": csi.get("detection_mode", "stf_delay"),
            "frame_start_mode": csi.get("frame_start_mode", "fixed_grid"),
            "ltf_search": csi.get("ltf_search_samples", 320),
            "grid_score_frames": csi.get("grid_score_frames", 128),
            "ltf_quality_threshold": csi.get("ltf_quality_threshold", 0.25),
            "timing_search": csi["timing_search_samples"],
        }
    raise KeyError(f"Unknown runtime defaults section: {section}")

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


def cyclic_shift_phase(
    cfg: ProbeConfig = CFG,
    shift_samples: int | None = None,
) -> np.ndarray:
    """Frequency-domain phase ramp for an OFDM cyclic time shift.

    With NumPy FFT conventions, multiplying subcarrier k by
    exp(-j*2*pi*k*n_cs/Nfft) is equivalent to a cyclic delay of n_cs samples.
    """
    n_cs = cfg.tx1_cyclic_shift_samples if shift_samples is None else int(shift_samples)
    bins = np.arange(cfg.fft_len, dtype=np.float64)
    carriers = np.where(bins <= cfg.fft_len // 2, bins, bins - cfg.fft_len)
    return np.exp(-1j * 2.0 * np.pi * carriers * n_cs / cfg.fft_len).astype(np.complex64)


def _cyclic_shift_useful(useful_td: np.ndarray, cfg: ProbeConfig = CFG) -> np.ndarray:
    if cfg.tx1_cyclic_shift_samples == 0:
        return np.asarray(useful_td, dtype=np.complex64)
    return np.roll(useful_td, int(cfg.tx1_cyclic_shift_samples)).astype(np.complex64)


def mimo_ht_training_matrix(meta: dict[str, Any], cfg: ProbeConfig = CFG) -> np.ndarray:
    """Return active-carrier 2x2 HT-LTF training matrices.

    Shape is [active_carrier, ht_symbol, tx_chain]. Each matrix row maps
    [H_TX0, H_TX1] into the normalized received HT-LTF observations.
    """
    nfft = int(meta.get("fft_len", cfg.fft_len))
    active = np.asarray(meta.get("active_carriers", cfg.active_carriers), dtype=np.int32)
    shift = int(meta.get("tx1_cyclic_shift_samples", 0))
    bins = np.array([int(k) % nfft for k in active], dtype=np.int32)
    carrier_numbers = np.where(bins <= nfft // 2, bins, bins - nfft).astype(np.float64)
    d = np.exp(-1j * 2.0 * np.pi * carrier_numbers * shift / nfft).astype(np.complex64)
    matrix = np.empty((len(active), 2, 2), dtype=np.complex64)
    matrix[:, 0, 0] = 1.0
    matrix[:, 0, 1] = d
    matrix[:, 1, 0] = 1.0
    matrix[:, 1, 1] = -d
    return matrix


def solve_mimo_ht_ltf(
    y1_active: np.ndarray,
    y2_active: np.ndarray,
    x_active: np.ndarray,
    meta: dict[str, Any],
    tx_chain_mode: str = "both",
    cfg: ProbeConfig = CFG,
) -> np.ndarray:
    """Decode two HT-LTF symbols into [tx, active_carrier] CSI."""
    z = np.stack(
        [
            y1_active / (x_active + 1e-12),
            y2_active / (x_active + 1e-12),
        ],
        axis=1,
    ).astype(np.complex64)
    matrix = mimo_ht_training_matrix(meta, cfg)
    h = np.empty((2, len(x_active)), dtype=np.complex64)
    for index in range(len(x_active)):
        h[:, index] = np.linalg.solve(matrix[index], z[index])
    if tx_chain_mode == "tx0_only":
        h[1, :] = 0.0
    elif tx_chain_mode == "tx1_only":
        h[0, :] = 0.0
    elif tx_chain_mode != "both":
        raise ValueError(f"Unsupported tx_chain_mode: {tx_chain_mode}")
    return h.astype(np.complex64)


def _training_values(cfg: ProbeConfig = CFG) -> np.ndarray:
    # 52-value BPSK pattern in the style of the 802.11 long training field.
    full_values = np.array(
        [
            1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1,
            1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1,
            1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1,
            -1, -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1,
        ],
        dtype=np.complex64,
    )
    full_carriers = np.array(list(range(-26, 0)) + list(range(1, 27)), dtype=np.int32)
    selected = np.isin(full_carriers, cfg.active_carriers)
    values = full_values[selected]
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


def _ltf_useful_raw(cfg: ProbeConfig = CFG) -> tuple[np.ndarray, np.ndarray]:
    training_values = _training_values(cfg)
    training_freq_raw = _freq_vector(training_values, cfg)
    training_useful_raw = np.fft.ifft(training_freq_raw).astype(np.complex64)
    return training_freq_raw, training_useful_raw


def _pilot_symbol_raw(cfg: ProbeConfig = CFG) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(cfg.seed)
    pilot_values = rng.choice([-1.0, 1.0], size=len(cfg.active_carriers)).astype(np.complex64)
    pilot_freq_raw = _freq_vector(pilot_values, cfg)
    pilot_useful_raw = np.fft.ifft(pilot_freq_raw).astype(np.complex64)
    return pilot_freq_raw, pilot_useful_raw


def _scale_waveform_parts(cfg: ProbeConfig, parts: list[np.ndarray]) -> float:
    peak = max(float(np.max(np.abs(part))) for part in parts if len(part))
    if peak <= 0:
        raise RuntimeError("Generated an invalid all-zero waveform.")
    return cfg.tx_scale / peak


def _match_rms(source: np.ndarray, reference: np.ndarray) -> np.ndarray:
    source_rms = float(np.sqrt(np.mean(np.abs(source) ** 2)))
    reference_rms = float(np.sqrt(np.mean(np.abs(reference) ** 2)))
    if source_rms <= 0 or reference_rms <= 0:
        raise RuntimeError("Cannot RMS-match an empty or all-zero waveform part.")
    return (source * (reference_rms / source_rms)).astype(np.complex64)


def make_legacy_tdm_waveforms(cfg: ProbeConfig = CFG):
    if cfg.tx_chain_mode not in {"both", "tx0_only", "tx1_only"}:
        raise ValueError("tx_chain_mode must be 'both', 'tx0_only', or 'tx1_only'")
    if cfg.pilot_repeats_per_tx < 1:
        raise ValueError("pilot_repeats_per_tx must be at least 1")
    training_freq_raw, training_useful_raw = _ltf_useful_raw(cfg)
    pilot_freq_raw, pilot_useful_raw = _pilot_symbol_raw(cfg)
    short_raw = _match_rms(_short_training(cfg), training_useful_raw)
    digital_scale = _scale_waveform_parts(cfg, [short_raw, training_useful_raw, pilot_useful_raw])
    short_training = (digital_scale * short_raw).astype(np.complex64)
    training_useful = (digital_scale * training_useful_raw).astype(np.complex64)
    pilot_useful = (digital_scale * pilot_useful_raw).astype(np.complex64)
    pilot_symbol = _with_cp(pilot_useful, cfg)
    long_training = np.concatenate(
        [
            training_useful[-LONG_TRAINING_CP_LEN:],
            training_useful,
            training_useful,
        ]
    ).astype(np.complex64)
    sync_training = np.concatenate([short_training, long_training]).astype(np.complex64)
    tx0_pilot_offset = len(sync_training)
    tx0_pilot_offsets = [
        tx0_pilot_offset + repeat * cfg.sym_len
        for repeat in range(cfg.pilot_repeats_per_tx)
    ]
    tx1_pilot_offset = tx0_pilot_offset + cfg.pilot_repeats_per_tx * cfg.sym_len
    tx1_pilot_offsets = [
        tx1_pilot_offset + repeat * cfg.sym_len
        for repeat in range(cfg.pilot_repeats_per_tx)
    ]
    occupied = len(sync_training) + 2 * cfg.pilot_repeats_per_tx * cfg.sym_len
    guard_len = cfg.frame_len - occupied
    if guard_len < 0:
        raise ValueError(f"Probe period too short: frame_len={cfg.frame_len}, occupied={occupied}")
    guard = np.zeros(guard_len, dtype=np.complex64)
    tx0_pilots = np.tile(pilot_symbol, cfg.pilot_repeats_per_tx).astype(np.complex64)
    tx1_pilots = np.tile(pilot_symbol, cfg.pilot_repeats_per_tx).astype(np.complex64)
    zero_pilots = np.zeros(cfg.pilot_repeats_per_tx * cfg.sym_len, dtype=np.complex64)
    tx0 = np.concatenate([sync_training, tx0_pilots, zero_pilots, guard]).astype(np.complex64)
    tx1 = np.concatenate([sync_training, zero_pilots, tx1_pilots, guard]).astype(np.complex64)
    if cfg.tx_chain_mode == "tx0_only":
        tx1 = np.zeros_like(tx1)
    elif cfg.tx_chain_mode == "tx1_only":
        tx0 = np.zeros_like(tx0)
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
        "short_training_rms_matched_to_ltf": True,
        "long_training_cp_len": LONG_TRAINING_CP_LEN,
        "long_training_len": len(long_training),
        "sync_training_len": len(sync_training),
        "ltf1_offset": ltf_start + LONG_TRAINING_CP_LEN,
        "ltf2_offset": ltf_start + LONG_TRAINING_CP_LEN + cfg.fft_len,
        "tx0_pilot_offset": tx0_pilot_offset,
        "tx1_pilot_offset": tx1_pilot_offset,
        "tx0_pilot_offsets": tx0_pilot_offsets,
        "tx1_pilot_offsets": tx1_pilot_offsets,
        "pilot_repeats_per_tx": cfg.pilot_repeats_per_tx,
        "occupied_len": occupied,
        "guard_len": guard_len,
        "training_freq_real": training_freq_scaled.real.tolist(),
        "training_freq_imag": training_freq_scaled.imag.tolist(),
        "pilot_freq_real": pilot_freq_scaled.real.tolist(),
        "pilot_freq_imag": pilot_freq_scaled.imag.tolist(),
    }
    return tx0, tx1, meta


def make_wifi_ht20_2x2_ltf_waveforms(cfg: ProbeConfig = CFG):
    if cfg.sync_tx_mode not in {"both", "tx0_only"}:
        raise ValueError("sync_tx_mode must be 'both' or 'tx0_only'")
    if cfg.tx_chain_mode not in {"both", "tx0_only", "tx1_only"}:
        raise ValueError("tx_chain_mode must be 'both', 'tx0_only', or 'tx1_only'")
    training_freq_raw, training_useful_raw = _ltf_useful_raw(cfg)
    short_raw = _match_rms(_short_training(cfg), training_useful_raw)
    digital_scale = _scale_waveform_parts(cfg, [short_raw, training_useful_raw])
    short_training = (digital_scale * short_raw).astype(np.complex64)
    ltf_useful = (digital_scale * training_useful_raw).astype(np.complex64)

    tx1_ltf_useful = _cyclic_shift_useful(ltf_useful, cfg)

    # Legacy L-LTF: common timing/CFO reference. Keep this sync field
    # unshifted in this experiment so packet detection and folded-frame
    # alignment remain stable; CSD is applied only to HT-LTF sounding below.
    legacy_ltf = np.concatenate(
        [
            ltf_useful[-LONG_TRAINING_CP_LEN:],
            ltf_useful,
            ltf_useful,
        ]
    ).astype(np.complex64)

    # HT/VHT-style 2-stream orthogonal MIMO training core. TX1 carries a CSD
    # phase ramp D[k], so the two observations are H0 +/- H1*D instead of a
    # fragile H0 +/- H1 difference across all carriers.
    ht_ltf1 = _with_cp(ltf_useful, cfg)
    ht_ltf2 = _with_cp(ltf_useful, cfg)
    tx1_ht_ltf1 = _with_cp(tx1_ltf_useful, cfg)
    tx1_ht_ltf2 = _with_cp(tx1_ltf_useful, cfg)
    ht_ltf_start = len(short_training) + len(legacy_ltf)
    ht_ltf1_offset = ht_ltf_start
    ht_ltf2_offset = ht_ltf_start + cfg.sym_len
    occupied = len(short_training) + len(legacy_ltf) + 2 * cfg.sym_len
    guard_len = cfg.frame_len - occupied
    if guard_len < 0:
        raise ValueError(f"Probe period too short: frame_len={cfg.frame_len}, occupied={occupied}")
    guard = np.zeros(guard_len, dtype=np.complex64)
    sync_training = np.concatenate([short_training, legacy_ltf]).astype(np.complex64)
    if cfg.sync_tx_mode == "both":
        tx1_sync = sync_training
    else:
        tx1_sync = np.zeros(len(sync_training), dtype=np.complex64)
    tx0 = np.concatenate([sync_training, ht_ltf1, ht_ltf2, guard]).astype(np.complex64)
    tx1 = np.concatenate([tx1_sync, tx1_ht_ltf1, -tx1_ht_ltf2, guard]).astype(np.complex64)
    if cfg.tx_chain_mode == "tx0_only":
        tx1 = np.zeros_like(tx1)
    elif cfg.tx_chain_mode == "tx1_only":
        tx0 = np.zeros_like(tx0)
    training_freq_scaled = (digital_scale * training_freq_raw).astype(np.complex64)
    stf_len = len(short_training)
    ltf_start = stf_len
    meta = {
        **asdict(cfg),
        "frame_format": "wifi_ht20_2x2_ltf_sounding",
        "sync_tx_mode": cfg.sync_tx_mode,
        "sym_len": cfg.sym_len,
        "frame_len": cfg.frame_len,
        "active_carriers": cfg.active_carriers.tolist(),
        "digital_scale": float(digital_scale),
        "short_training_len": stf_len,
        "short_training_repeats": SHORT_TRAINING_REPEATS,
        "short_training_rms_matched_to_ltf": True,
        "long_training_cp_len": LONG_TRAINING_CP_LEN,
        "legacy_ltf_len": len(legacy_ltf),
        "sync_training_len": len(short_training) + len(legacy_ltf),
        "ltf1_offset": ltf_start + LONG_TRAINING_CP_LEN,
        "ltf2_offset": ltf_start + LONG_TRAINING_CP_LEN + cfg.fft_len,
        "mimo_ltf_matrix": "carrier_dependent_csd_2x2",
        "mimo_ltf_formula": [
            "Y1[k] / X[k] = H0[k] + H1[k] * D[k]",
            "Y2[k] / X[k] = H0[k] - H1[k] * D[k]",
            "D[k] = exp(-j*2*pi*k*tx1_cyclic_shift_samples/Nfft)",
        ],
        "tx1_cyclic_shift_samples": int(cfg.tx1_cyclic_shift_samples),
        "tx1_cyclic_shift_seconds": float(cfg.tx1_cyclic_shift_samples / cfg.sample_rate),
        "tx1_cyclic_shift_applied_to": ["ht_ltf"],
        "ht_ltf1_offset": ht_ltf1_offset,
        "ht_ltf2_offset": ht_ltf2_offset,
        "ht_ltf_offsets": [ht_ltf1_offset, ht_ltf2_offset],
        "occupied_len": occupied,
        "guard_len": guard_len,
        "training_freq_real": training_freq_scaled.real.tolist(),
        "training_freq_imag": training_freq_scaled.imag.tolist(),
    }
    return tx0, tx1, meta


def make_waveforms(cfg: ProbeConfig = CFG):
    if cfg.frame_format == "wifi_like_stf_ltf_tdm_mimo":
        return make_legacy_tdm_waveforms(cfg)
    if cfg.frame_format == "wifi_ht20_2x2_ltf_sounding":
        return make_wifi_ht20_2x2_ltf_waveforms(cfg)
    raise ValueError(f"Unsupported frame_format: {cfg.frame_format}")

def save_probe_metadata(path: str | Path, cfg: ProbeConfig = CFG) -> None:
    _, _, meta = make_waveforms(cfg)
    Path(path).write_text(json.dumps(meta, indent=2), encoding="utf-8")
