#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import sys
import threading
from collections import deque
from pathlib import Path

import numpy as np
from PyQt5 import Qt, QtCore, QtGui
from gnuradio import gr, uhd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from common.frame_design import CFG, ProbeConfig, make_waveforms, runtime_defaults


def db10(value: np.ndarray | float) -> np.ndarray | float:
    return 10.0 * np.log10(np.maximum(value, 1e-20))


class DualChannelRingSink(gr.sync_block):
    def __init__(self, max_samples: int):
        super().__init__(
            name="frame_observer_dual_channel_ring_sink",
            in_sig=[np.complex64, np.complex64],
            out_sig=None,
        )
        self._rx0: deque[np.ndarray] = deque()
        self._rx1: deque[np.ndarray] = deque()
        self._max_samples = max_samples
        self._sample_count = 0
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        chunk0 = np.asarray(input_items[0], dtype=np.complex64).copy()
        chunk1 = np.asarray(input_items[1], dtype=np.complex64).copy()
        with self._lock:
            self._rx0.append(chunk0)
            self._rx1.append(chunk1)
            self._sample_count += len(chunk0)
            while self._sample_count > self._max_samples and self._rx0:
                old0 = self._rx0.popleft()
                self._rx1.popleft()
                self._sample_count -= len(old0)
        return len(chunk0)

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            rx0_parts = list(self._rx0)
            rx1_parts = list(self._rx1)
        if not rx0_parts:
            return np.empty(0, dtype=np.complex64), np.empty(0, dtype=np.complex64)
        rx0 = np.concatenate(rx0_parts).astype(np.complex64, copy=False)
        rx1 = np.concatenate(rx1_parts).astype(np.complex64, copy=False)
        n = min(len(rx0), len(rx1), self._max_samples)
        return rx0[-n:], rx1[-n:]


class FrameObserverTopBlock(gr.top_block):
    def __init__(self, args: str, freq: float, rate: float, gain: float, antenna: str, buffer_seconds: float):
        super().__init__("dual-RX raw-IQ frame observer")
        max_samples = max(1, int(round(buffer_seconds * rate)))
        self.usrp = uhd.usrp_source(
            args,
            uhd.stream_args(cpu_format="fc32", otw_format="", channels=[0, 1]),
        )
        self.usrp.set_samp_rate(rate)
        for ch in (0, 1):
            self.usrp.set_center_freq(freq, ch)
            self.usrp.set_gain(gain, ch)
            self.usrp.set_antenna(antenna, ch)
        self.ring = DualChannelRingSink(max_samples)
        self.connect((self.usrp, 0), (self.ring, 0))
        self.connect((self.usrp, 1), (self.ring, 1))


class FramePowerPlot(Qt.QWidget):
    def __init__(self, title: str, regions: dict[str, tuple[int, int]], sample_rate: float):
        super().__init__()
        self.title = title
        self.regions = regions
        self.sample_rate = sample_rate
        self.power_db: np.ndarray | None = None
        self.floor_db = -120.0
        self.gate_db = 6.0
        self.setMinimumHeight(240)

    def set_data(self, power_db: np.ndarray | None, floor_db: float, gate_db: float) -> None:
        self.power_db = power_db
        self.floor_db = floor_db
        self.gate_db = gate_db
        self.update()

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(250, 250, 250))
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QColor(20, 20, 20))
        painter.drawText(10, 18, self.title)
        if self.power_db is None or len(self.power_db) < 2:
            painter.drawText(12, 48, "Waiting for IQ samples...")
            return

        plot = self.rect().adjusted(58, 30, -18, -34)
        y_min = min(float(np.percentile(self.power_db, 2)) - 1.0, self.floor_db - 2.0)
        y_max = max(float(np.percentile(self.power_db, 98)) + 1.0, self.floor_db + self.gate_db + 2.0)
        if y_max <= y_min:
            y_max = y_min + 1.0

        def xp(index: float) -> float:
            return plot.left() + index / (len(self.power_db) - 1) * plot.width()

        def yp(value: float) -> float:
            return plot.bottom() - (value - y_min) / (y_max - y_min) * plot.height()

        painter.setPen(QtGui.QPen(QtGui.QColor(220, 220, 220), 1))
        for tick in np.linspace(y_min, y_max, 5):
            y = yp(float(tick))
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
            painter.setPen(QtGui.QColor(80, 80, 80))
            painter.drawText(4, int(y) + 4, f"{tick:.1f}")
            painter.setPen(QtGui.QPen(QtGui.QColor(220, 220, 220), 1))

        fill_colors = [
            QtGui.QColor(210, 232, 245, 95),
            QtGui.QColor(225, 220, 245, 95),
            QtGui.QColor(220, 245, 220, 95),
            QtGui.QColor(245, 232, 210, 95),
            QtGui.QColor(245, 220, 220, 95),
        ]
        for idx, (name, (start, length)) in enumerate(self.regions.items()):
            if name == "guard":
                continue
            x0 = xp(start)
            x1 = xp(start + length)
            painter.fillRect(
                QtCore.QRectF(x0, plot.top(), max(1.0, x1 - x0), plot.height()),
                fill_colors[idx % len(fill_colors)],
            )
            painter.setPen(QtGui.QColor(50, 50, 50))
            painter.drawText(QtCore.QPointF(x0 + 2, plot.top() + 14), name)

        painter.setPen(QtGui.QPen(QtGui.QColor(180, 40, 40), 1, Qt.Qt.DashLine))
        painter.drawLine(plot.left(), int(yp(self.floor_db + self.gate_db)), plot.right(), int(yp(self.floor_db + self.gate_db)))
        painter.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1, Qt.Qt.DashLine))
        painter.drawLine(plot.left(), int(yp(self.floor_db)), plot.right(), int(yp(self.floor_db)))

        path = QtGui.QPainterPath()
        for i, value in enumerate(self.power_db):
            point = QtCore.QPointF(xp(i), yp(float(value)))
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QtGui.QPen(QtGui.QColor(15, 95, 180), 1.2))
        painter.drawPath(path)
        painter.setPen(QtGui.QColor(20, 20, 20))
        painter.drawRect(plot)
        painter.drawText(plot.left(), self.height() - 8, "0 us")
        painter.drawText(plot.right() - 72, self.height() - 8, f"{(len(self.power_db)-1)/self.sample_rate*1e6:.1f} us")


def fold_frame_power(samples: np.ndarray, frame_len: int) -> np.ndarray:
    power = np.abs(samples) ** 2
    folded = np.zeros(frame_len, dtype=np.float64)
    counts = np.zeros(frame_len, dtype=np.float64)
    residues = np.arange(len(power), dtype=np.int64) % frame_len
    np.add.at(folded, residues, power)
    np.add.at(counts, residues, 1.0)
    return folded / np.maximum(counts, 1.0)


def score_base(folded_power: np.ndarray, regions: dict[str, tuple[int, int]], base: int) -> float:
    frame_len = len(folded_power)

    def take(start: int, length: int) -> np.ndarray:
        idx = (base + start + np.arange(length)) % frame_len
        return folded_power[idx]

    occupied = take(0, regions["guard"][0])
    guard = take(*regions["guard"])
    return float(db10(np.mean(occupied)) - db10(np.mean(guard)))


def rotate_to_best_base(folded_power: np.ndarray, regions: dict[str, tuple[int, int]]) -> tuple[np.ndarray, int, float]:
    scores = np.array([score_base(folded_power, regions, base) for base in range(len(folded_power))])
    base = int(np.argmax(scores))
    return np.roll(folded_power, -base), base, float(scores[base])


def region_power_db(power: np.ndarray, regions: dict[str, tuple[int, int]]) -> dict[str, float]:
    out = {}
    for name, (start, length) in regions.items():
        out[name] = float(db10(np.mean(power[start : start + length])))
    return out


def high_power_len(power_db: np.ndarray, floor_db: float, gate_db: float) -> int:
    mask = power_db > floor_db + gate_db
    best = 0
    index = 0
    while index < len(mask):
        if not mask[index]:
            index += 1
            continue
        end = index
        while end < len(mask) and mask[end]:
            end += 1
        best = max(best, end - index)
        index = end
    return int(best)


def frequency_snr(samples: np.ndarray, base: int, frame_len: int, regions: dict[str, tuple[int, int]], meta: dict) -> dict[str, float]:
    nfft = int(meta["fft_len"])
    cp = int(meta["cp_len"])
    active = np.array([int(carrier) % nfft for carrier in meta["active_carriers"]], dtype=np.int32)
    inactive = np.ones(nfft, dtype=bool)
    inactive[active] = False
    inactive[0] = False
    frame_count = min(200, max(0, (len(samples) - base - frame_len) // frame_len))
    if frame_count <= 0:
        return {}
    starts = base + np.arange(frame_count, dtype=np.int64) * frame_len
    offsets = {
        "l_ltf1": regions["l_ltf1"][0],
        "l_ltf2": regions["l_ltf2"][0],
        "ht_ltf1": regions["ht_ltf1"][0] + cp,
        "ht_ltf2": regions["ht_ltf2"][0] + cp,
    }
    out = {}
    for name, offset in offsets.items():
        active_power = []
        inactive_power = []
        for start in starts:
            symbol = samples[start + offset : start + offset + nfft]
            if len(symbol) != nfft:
                continue
            spec = np.fft.fft(symbol)
            active_power.append(float(np.mean(np.abs(spec[active]) ** 2)))
            inactive_power.append(float(np.mean(np.abs(spec[inactive]) ** 2)))
        if active_power:
            out[name] = float(db10(np.mean(active_power)) - db10(np.mean(inactive_power)))
    return out


class FrameObserverWindow(Qt.QWidget):
    def __init__(
        self,
        args: str,
        freq: float,
        rate: float,
        gain: float,
        antenna: str,
        buffer_seconds: float,
        update_interval_ms: int,
        gate_db: float,
        probe_rate: float,
        active_carrier_count: int,
        tx_scale: float,
        pilot_repeats_per_tx: int,
        frame_format: str,
        sync_tx_mode: str,
        tx_chain_mode: str,
    ):
        super().__init__()
        self.setWindowTitle("USRP B210 raw-IQ frame observer")
        self.resize(1280, 860)
        self.rate = rate
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
            f"gain={gain:.1f} dB, buffer={buffer_seconds:.3f}s, frame={self.cfg.frame_len} samples, "
            f"occupied={self.meta['occupied_len']} samples, active={active_carrier_count}"
        )
        layout.addWidget(self.status)

        controls = Qt.QHBoxLayout()
        controls.addWidget(Qt.QLabel("Gate over guard (dB)"))
        self.gate_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.gate_slider.setRange(0, 120)
        self.gate_slider.setValue(int(round(gate_db * 10)))
        controls.addWidget(self.gate_slider)
        self.gate_label = Qt.QLabel(f"{gate_db:.1f}")
        controls.addWidget(self.gate_label)
        layout.addLayout(controls)
        self.gate_slider.valueChanged.connect(self._on_gate_changed)

        self.metrics = Qt.QTableWidget(10, 5)
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
        rows = []
        plot_data = []
        for rx in (rx0, rx1):
            folded = fold_frame_power(rx, self.cfg.frame_len)
            aligned, base, score = rotate_to_best_base(folded, self.regions)
            power_db = db10(aligned)
            rp = region_power_db(aligned, self.regions)
            guard = rp["guard"]
            snr = frequency_snr(rx, base, self.cfg.frame_len, self.regions, self.meta)
            rows.append(
                {
                    "base": base,
                    "score": score,
                    "over": {key: value - guard for key, value in rp.items()},
                    "high_len": high_power_len(power_db, guard, self.gate_db),
                    "snr": snr,
                    "guard": guard,
                }
            )
            plot_data.append((power_db, guard))
        self.plot0.set_data(plot_data[0][0], plot_data[0][1], self.gate_db)
        self.plot1.set_data(plot_data[1][0], plot_data[1][1], self.gate_db)
        self._update_metrics(rows)

    def _update_metrics(self, rows: list[dict[str, object]]) -> None:
        metric_defs = [
            ("Best phase", lambda row: f"{int(row['base'])}", "stable"),
            ("Occupied score", lambda row: f"{float(row['score']):.1f} dB", "> 6 dB"),
            ("Max high segment", lambda row: f"{int(row['high_len'])} samples", "~480"),
            ("STF over guard", lambda row: f"{row['over']['stf']:.1f} dB", "> 6 dB"),
            ("L-LTF1 over guard", lambda row: f"{row['over']['l_ltf1']:.1f} dB", "> 6 dB"),
            ("L-LTF2 over guard", lambda row: f"{row['over']['l_ltf2']:.1f} dB", "> 6 dB"),
            ("HT-LTF1 over guard", lambda row: f"{row['over']['ht_ltf1']:.1f} dB", "> 6 dB"),
            ("HT-LTF2 over guard", lambda row: f"{row['over']['ht_ltf2']:.1f} dB", "> 6 dB"),
            ("HT1 freq SNR", lambda row: f"{row['snr'].get('ht_ltf1', float('nan')):.1f} dB", "> 6 dB"),
            ("HT2 freq SNR", lambda row: f"{row['snr'].get('ht_ltf2', float('nan')):.1f} dB", "> 6 dB"),
        ]
        self.metrics.setRowCount(len(metric_defs))
        for i, (name, formatter, target) in enumerate(metric_defs):
            self.metrics.setItem(i, 0, Qt.QTableWidgetItem(name))
            self.metrics.setItem(i, 1, Qt.QTableWidgetItem(formatter(rows[0])))
            self.metrics.setItem(i, 2, Qt.QTableWidgetItem(formatter(rows[1])))
            self.metrics.setItem(i, 3, Qt.QTableWidgetItem(target))
            ok = self._ok(name, rows)
            status = Qt.QTableWidgetItem("OK" if ok else "CHECK")
            status.setBackground(QtGui.QColor(210, 245, 215) if ok else QtGui.QColor(255, 230, 190))
            self.metrics.setItem(i, 4, status)
        self.metrics.resizeColumnsToContents()

    def _ok(self, name: str, rows: list[dict[str, object]]) -> bool:
        if name == "Best phase":
            return True
        if name == "Max high segment":
            return all(360 <= int(row["high_len"]) <= 600 for row in rows)
        if name == "Occupied score":
            return all(float(row["score"]) >= 6.0 for row in rows)
        region_keys = {
            "STF over guard": "stf",
            "L-LTF1 over guard": "l_ltf1",
            "L-LTF2 over guard": "l_ltf2",
            "HT-LTF1 over guard": "ht_ltf1",
            "HT-LTF2 over guard": "ht_ltf2",
        }
        if name in region_keys:
            key = region_keys[name]
            return all(float(row["over"][key]) >= 6.0 for row in rows)
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
    parser.add_argument("--gate-db", type=float, default=6.0)
    parser.add_argument("--probe-rate", type=float, default=float(defaults.get("probe_rate", tx_defaults["probe_rate"])))
    parser.add_argument("--active-carrier-count", type=int, default=int(defaults.get("active_carrier_count", tx_defaults["active_carrier_count"])))
    parser.add_argument("--tx-scale", type=float, default=float(defaults.get("tx_scale", tx_defaults["tx_scale"])))
    parser.add_argument("--pilot-repeats-per-tx", type=int, default=int(defaults.get("pilot_repeats_per_tx", tx_defaults["pilot_repeats_per_tx"])))
    parser.add_argument("--frame-format", default=str(defaults.get("frame_format", tx_defaults["frame_format"])))
    parser.add_argument("--sync-tx-mode", choices=["both", "tx0_only"], default=str(defaults.get("sync_tx_mode", tx_defaults["sync_tx_mode"])))
    parser.add_argument("--tx-chain-mode", choices=["both", "tx0_only", "tx1_only"], default=str(defaults.get("tx_chain_mode", tx_defaults["tx_chain_mode"])))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = Qt.QApplication(sys.argv)
    window = FrameObserverWindow(
        args=args.args,
        freq=args.freq,
        rate=args.rate,
        gain=args.gain,
        antenna=args.antenna,
        buffer_seconds=args.buffer_seconds,
        update_interval_ms=args.update_interval_ms,
        gate_db=args.gate_db,
        probe_rate=args.probe_rate,
        active_carrier_count=args.active_carrier_count,
        tx_scale=args.tx_scale,
        pilot_repeats_per_tx=args.pilot_repeats_per_tx,
        frame_format=args.frame_format,
        sync_tx_mode=args.sync_tx_mode,
        tx_chain_mode=args.tx_chain_mode,
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
