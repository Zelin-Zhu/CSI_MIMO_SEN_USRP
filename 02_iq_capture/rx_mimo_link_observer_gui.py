#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

import numpy as np
from PyQt5 import Qt, QtCore, QtGui

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from common.frame_design import CFG, ProbeConfig, make_waveforms, runtime_defaults, solve_mimo_ht_ltf
from rx_frame_observer_gui import (
    FrameObserverTopBlock,
    FramePowerPlot,
    db10,
    fold_frame_power,
    frequency_snr,
    region_power_db,
    rotate_to_best_base,
)


def db20(values: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(values), 1e-12))


def active_tx_indices(link_mode: str) -> list[int]:
    if link_mode in {"siso", "1x2"}:
        return [0]
    return [0, 1]


def active_rx_indices(link_mode: str) -> list[int]:
    if link_mode == "siso":
        return [0]
    return [0, 1]


def adjacent_corr(values: np.ndarray) -> float:
    if values.shape[0] < 2:
        return float("nan")
    a = values[:-1]
    b = values[1:]
    corr = np.abs(np.sum(np.conj(a) * b, axis=1)) / (
        np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-12
    )
    return float(np.mean(corr))


def estimate_rx_links(
    samples: np.ndarray,
    base: int,
    frame_len: int,
    regions: dict[str, tuple[int, int]],
    meta: dict,
    link_mode: str,
    max_frames: int = 200,
) -> np.ndarray:
    nfft = int(meta["fft_len"])
    cp = int(meta["cp_len"])
    bins = np.array([int(carrier) % nfft for carrier in meta["active_carriers"]], dtype=np.int32)
    training = (
        np.array(meta["training_freq_real"], dtype=np.float32)
        + 1j * np.array(meta["training_freq_imag"], dtype=np.float32)
    ).astype(np.complex64)
    x = training[bins]
    frame_count = min(max_frames, max(0, (len(samples) - base - frame_len) // frame_len))
    starts = base + np.arange(frame_count, dtype=np.int64) * frame_len
    h_frames = []
    for start in starts:
        ht1 = samples[start + regions["ht_ltf1"][0] + cp : start + regions["ht_ltf1"][0] + cp + nfft]
        ht2 = samples[start + regions["ht_ltf2"][0] + cp : start + regions["ht_ltf2"][0] + cp + nfft]
        if len(ht1) != nfft or len(ht2) != nfft:
            continue
        y1 = np.fft.fft(ht1)[bins]
        y2 = np.fft.fft(ht2)[bins]
        if link_mode in {"siso", "1x2"}:
            h = solve_mimo_ht_ltf(y1, y2, x, meta, tx_chain_mode="tx0_only")
            h[1, :] = np.nan + 1j * np.nan
        else:
            h = solve_mimo_ht_ltf(y1, y2, x, meta, tx_chain_mode="both")
        h_frames.append(h)
    if not h_frames:
        return np.empty((0, 2, len(bins)), dtype=np.complex64)
    return np.asarray(h_frames, dtype=np.complex64)


class LinkMatrixTable(Qt.QTableWidget):
    def __init__(self):
        super().__init__(2, 2)
        self.setHorizontalHeaderLabels(["TX0", "TX1"])
        self.setVerticalHeaderLabels(["RX0", "RX1"])
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setDefaultSectionSize(74)
        self.setMinimumHeight(190)

    def update_matrix(self, h_by_rx: list[np.ndarray], snr_by_rx: list[dict[str, float]], link_mode: str) -> None:
        active_rxs = active_rx_indices(link_mode)
        active_txs = active_tx_indices(link_mode)
        for rx in range(2):
            for tx in range(2):
                item = Qt.QTableWidgetItem()
                item.setTextAlignment(Qt.Qt.AlignCenter)
                if rx not in active_rxs or tx not in active_txs or rx >= len(h_by_rx):
                    item.setText("inactive")
                    item.setBackground(QtGui.QColor(235, 235, 235))
                    self.setItem(rx, tx, item)
                    continue
                h = h_by_rx[rx]
                if h.shape[0] == 0 or not np.isfinite(h[:, tx, :]).any():
                    item.setText("no frames")
                    item.setBackground(QtGui.QColor(255, 230, 190))
                    self.setItem(rx, tx, item)
                    continue
                path = h[:, tx, :]
                amp_mean = float(np.nanmean(db20(path)))
                amp_std = float(np.nanstd(db20(path)))
                corr = adjacent_corr(path)
                ht1_snr = snr_by_rx[rx].get("ht_ltf1", float("nan"))
                ht2_snr = snr_by_rx[rx].get("ht_ltf2", float("nan"))
                item.setText(
                    f"|H| {amp_mean:.1f} dB\n"
                    f"std {amp_std:.1f} dB\n"
                    f"corr {corr:.2f}\n"
                    f"HT {ht1_snr:.1f}/{ht2_snr:.1f} dB"
                )
                ok = ht1_snr >= 6.0 and ht2_snr >= 6.0 and corr >= 0.35
                item.setBackground(QtGui.QColor(210, 245, 215) if ok else QtGui.QColor(255, 230, 190))
                self.setItem(rx, tx, item)
        self.resizeColumnsToContents()


class MimoLinkObserverWindow(Qt.QWidget):
    def __init__(
        self,
        args: str,
        freq: float,
        rate: float,
        gain: float,
        antenna: str,
        buffer_seconds: float,
        update_interval_ms: int,
        link_mode: str,
        gate_db: float,
        probe_rate: float,
        active_carrier_count: int,
        tx_scale: float,
        pilot_repeats_per_tx: int,
        frame_format: str,
        sync_tx_mode: str,
        tx_chain_mode: str,
        tx1_cyclic_shift_samples: int,
    ):
        super().__init__()
        self.setWindowTitle("USRP B210 MIMO link observer")
        self.resize(1320, 940)
        self.link_mode = link_mode
        self.gate_db = gate_db
        self.cfg = ProbeConfig(
            sample_rate=rate,
            center_freq=freq,
            fft_len=CFG.fft_len,
            cp_len=CFG.cp_len,
            active_carrier_count=active_carrier_count,
            probe_rate_hz=probe_rate,
            tx_scale=tx_scale,
            pilot_repeats_per_tx=pilot_repeats_per_tx,
            frame_format=frame_format,
            sync_tx_mode=sync_tx_mode,
            tx_chain_mode=tx_chain_mode,
            tx1_cyclic_shift_samples=tx1_cyclic_shift_samples,
            seed=CFG.seed,
        )
        _, _, self.meta = make_waveforms(self.cfg)
        self.regions = {
            "stf": (0, int(self.meta["short_training_len"])),
            "l_ltf_cp": (int(self.meta["short_training_len"]), int(self.meta["long_training_cp_len"])),
            "l_ltf1": (int(self.meta["ltf1_offset"]), self.cfg.fft_len),
            "l_ltf2": (int(self.meta["ltf2_offset"]), self.cfg.fft_len),
            "ht_ltf1": (int(self.meta["ht_ltf1_offset"]), self.cfg.sym_len),
            "ht_ltf2": (int(self.meta["ht_ltf2_offset"]), self.cfg.sym_len),
            "guard": (int(self.meta["occupied_len"]), min(512, int(self.meta["guard_len"]))),
        }
        self.tb = FrameObserverTopBlock(args, freq, rate, gain, antenna, buffer_seconds)

        layout = Qt.QVBoxLayout(self)
        self.status = Qt.QLabel(
            f"RX args={args!r}, freq={freq/1e6:.6f} MHz, rate={rate/1e6:.3f} MS/s, "
            f"gain={gain:.1f} dB, buffer={buffer_seconds:.3f}s, mode={link_mode}, "
            f"active={active_carrier_count}, tx_scale={tx_scale:.2f}, "
            f"tx1_csd={tx1_cyclic_shift_samples} samples"
        )
        layout.addWidget(self.status)

        controls = Qt.QHBoxLayout()
        controls.addWidget(Qt.QLabel("Link mode"))
        self.mode_combo = Qt.QComboBox()
        self.mode_combo.addItems(["siso", "1x2", "2x2"])
        self.mode_combo.setCurrentText(link_mode)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        controls.addWidget(self.mode_combo)
        controls.addWidget(Qt.QLabel("Gate over guard (dB)"))
        self.gate_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.gate_slider.setRange(0, 120)
        self.gate_slider.setValue(int(round(gate_db * 10)))
        controls.addWidget(self.gate_slider)
        self.gate_label = Qt.QLabel(f"{gate_db:.1f}")
        controls.addWidget(self.gate_label)
        layout.addLayout(controls)
        self.gate_slider.valueChanged.connect(self._on_gate_changed)

        self.matrix = LinkMatrixTable()
        layout.addWidget(self.matrix)

        self.metrics = Qt.QTableWidget(8, 5)
        self.metrics.setHorizontalHeaderLabels(["Metric", "RX0", "RX1", "Target", "Status"])
        self.metrics.verticalHeader().hide()
        self.metrics.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.metrics)

        self.plot0 = FramePowerPlot("RX0 folded average frame power", self.regions, rate)
        self.plot1 = FramePowerPlot("RX1 folded average frame power", self.regions, rate)
        layout.addWidget(self.plot0)
        layout.addWidget(self.plot1)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(update_interval_ms)
        self.timer.timeout.connect(self.update_view)

    def _on_mode_changed(self, value: str) -> None:
        self.link_mode = value

    def _on_gate_changed(self, value: int) -> None:
        self.gate_db = value / 10.0
        self.gate_label.setText(f"{self.gate_db:.1f}")

    def start(self) -> None:
        self.tb.start()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        self.tb.stop()
        self.tb.wait()

    def closeEvent(self, event) -> None:
        self.stop()
        event.accept()

    def update_view(self) -> None:
        rx0, rx1 = self.tb.ring.snapshot()
        if len(rx0) < 4 * self.cfg.frame_len:
            return
        rx_samples = [rx0, rx1]
        rows = []
        h_by_rx = []
        snr_by_rx = []
        for samples in rx_samples:
            folded = fold_frame_power(samples, self.cfg.frame_len)
            aligned, base, score = rotate_to_best_base(folded, self.regions)
            power_db = db10(aligned)
            rp = region_power_db(aligned, self.regions)
            guard = rp["guard"]
            snr = frequency_snr(samples, base, self.cfg.frame_len, self.regions, self.meta)
            h = estimate_rx_links(samples, base, self.cfg.frame_len, self.regions, self.meta, self.link_mode)
            rows.append(
                {
                    "base": base,
                    "score": score,
                    "over": {key: value - guard for key, value in rp.items()},
                    "guard": guard,
                    "power_db": power_db,
                    "snr": snr,
                }
            )
            h_by_rx.append(h)
            snr_by_rx.append(snr)
        self.plot0.set_data(rows[0]["power_db"], rows[0]["guard"], self.gate_db)
        self.plot1.set_data(rows[1]["power_db"], rows[1]["guard"], self.gate_db)
        self.matrix.update_matrix(h_by_rx, snr_by_rx, self.link_mode)
        self._update_metrics(rows)

    def _update_metrics(self, rows: list[dict[str, object]]) -> None:
        metric_defs = [
            ("Best phase", lambda row: f"{int(row['base'])}", "stable"),
            ("Occupied score", lambda row: f"{float(row['score']):.1f} dB", "> 6 dB"),
            ("STF over guard", lambda row: f"{row['over']['stf']:.1f} dB", "> 6 dB"),
            ("L-LTF over guard", lambda row: f"{min(row['over']['l_ltf1'], row['over']['l_ltf2']):.1f} dB", "> 6 dB"),
            ("HT-LTF over guard", lambda row: f"{min(row['over']['ht_ltf1'], row['over']['ht_ltf2']):.1f} dB", "> 6 dB"),
            ("HT1 freq SNR", lambda row: f"{row['snr'].get('ht_ltf1', float('nan')):.1f} dB", "> 6 dB"),
            ("HT2 freq SNR", lambda row: f"{row['snr'].get('ht_ltf2', float('nan')):.1f} dB", "> 6 dB"),
            ("Mode", lambda row: self.link_mode, "siso/1x2/2x2"),
        ]
        self.metrics.setRowCount(len(metric_defs))
        for i, (name, formatter, target) in enumerate(metric_defs):
            self.metrics.setItem(i, 0, Qt.QTableWidgetItem(name))
            self.metrics.setItem(i, 1, Qt.QTableWidgetItem(formatter(rows[0])))
            self.metrics.setItem(i, 2, Qt.QTableWidgetItem(formatter(rows[1])))
            self.metrics.setItem(i, 3, Qt.QTableWidgetItem(target))
            ok = self._metric_ok(name, rows)
            item = Qt.QTableWidgetItem("OK" if ok else "CHECK")
            item.setBackground(QtGui.QColor(210, 245, 215) if ok else QtGui.QColor(255, 230, 190))
            self.metrics.setItem(i, 4, item)
        self.metrics.resizeColumnsToContents()

    def _metric_ok(self, name: str, rows: list[dict[str, object]]) -> bool:
        if name in {"Best phase", "Mode"}:
            return True
        if name == "Occupied score":
            return all(float(row["score"]) >= 6.0 for row in rows)
        if name == "STF over guard":
            return all(float(row["over"]["stf"]) >= 6.0 for row in rows)
        if name == "L-LTF over guard":
            return all(min(float(row["over"]["l_ltf1"]), float(row["over"]["l_ltf2"])) >= 6.0 for row in rows)
        if name == "HT-LTF over guard":
            return all(min(float(row["over"]["ht_ltf1"]), float(row["over"]["ht_ltf2"])) >= 6.0 for row in rows)
        if name == "HT1 freq SNR":
            return all(float(row["snr"].get("ht_ltf1", -999.0)) >= 6.0 for row in rows)
        if name == "HT2 freq SNR":
            return all(float(row["snr"].get("ht_ltf2", -999.0)) >= 6.0 for row in rows)
        return False


def parse_args() -> argparse.Namespace:
    defaults = runtime_defaults("rx_monitor")
    tx_defaults = runtime_defaults("tx")
    parser = argparse.ArgumentParser()
    parser.add_argument("--args", default=defaults["args"], help='UHD args, e.g. "serial=3271260"')
    parser.add_argument("--freq", type=float, default=float(defaults["freq"]))
    parser.add_argument("--rate", type=float, default=float(defaults["rate"]))
    parser.add_argument("--gain", type=float, default=float(defaults["gain"]))
    parser.add_argument("--antenna", default=str(defaults["antenna"]))
    parser.add_argument("--buffer-seconds", type=float, default=0.05)
    parser.add_argument("--update-interval-ms", type=int, default=int(defaults["update_interval_ms"]))
    parser.add_argument("--link-mode", choices=["siso", "1x2", "2x2"], default="2x2")
    parser.add_argument("--gate-db", type=float, default=6.0)
    parser.add_argument("--probe-rate", type=float, default=float(defaults.get("probe_rate", tx_defaults["probe_rate"])))
    parser.add_argument("--active-carrier-count", type=int, default=int(defaults.get("active_carrier_count", tx_defaults["active_carrier_count"])))
    parser.add_argument("--tx-scale", type=float, default=float(defaults.get("tx_scale", tx_defaults["tx_scale"])))
    parser.add_argument("--pilot-repeats-per-tx", type=int, default=int(defaults.get("pilot_repeats_per_tx", tx_defaults["pilot_repeats_per_tx"])))
    parser.add_argument("--frame-format", default=str(defaults.get("frame_format", tx_defaults["frame_format"])))
    parser.add_argument("--sync-tx-mode", choices=["both", "tx0_only"], default=str(defaults.get("sync_tx_mode", tx_defaults["sync_tx_mode"])))
    parser.add_argument("--tx-chain-mode", choices=["both", "tx0_only", "tx1_only"], default=str(defaults.get("tx_chain_mode", tx_defaults["tx_chain_mode"])))
    parser.add_argument("--tx1-cyclic-shift-samples", type=int, default=int(defaults.get("tx1_cyclic_shift_samples", tx_defaults["tx1_cyclic_shift_samples"])))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = Qt.QApplication(sys.argv)
    window = MimoLinkObserverWindow(
        args=args.args,
        freq=args.freq,
        rate=args.rate,
        gain=args.gain,
        antenna=args.antenna,
        buffer_seconds=args.buffer_seconds,
        update_interval_ms=args.update_interval_ms,
        link_mode=args.link_mode,
        gate_db=args.gate_db,
        probe_rate=args.probe_rate,
        active_carrier_count=args.active_carrier_count,
        tx_scale=args.tx_scale,
        pilot_repeats_per_tx=args.pilot_repeats_per_tx,
        frame_format=args.frame_format,
        sync_tx_mode=args.sync_tx_mode,
        tx_chain_mode=args.tx_chain_mode,
        tx1_cyclic_shift_samples=args.tx1_cyclic_shift_samples,
    )
    window.start()
    window.show()

    def stop_handler(*_):
        window.stop()
        app.quit()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)
    app.exec_()


if __name__ == "__main__":
    main()
