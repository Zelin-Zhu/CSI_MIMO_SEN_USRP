#!/usr/bin/env python3
"""
Realtime dual-RX CSI monitor for tuning.

This tool keeps only a short in-memory IQ buffer. It does not save samples.
Use it to tune RF parameters before running rx_capture_2ch.py for long captures.
"""

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
sys.path.insert(0, str(REPO_ROOT / "03_csi_extraction"))

from common.frame_design import CFG, ProbeConfig, make_waveforms, runtime_defaults
from extract_csi import extract_one_frame, normalized_corr_metric


def db10(value: float) -> float:
    return float(10.0 * np.log10(value + 1e-20))


def db20_abs(values: np.ndarray) -> float:
    return float(20.0 * np.log10(float(np.mean(np.abs(values))) + 1e-12))


class DualChannelRingSink(gr.sync_block):
    def __init__(self, max_samples: int):
        super().__init__(
            name="dual_channel_ring_sink",
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


class HeatmapWidget(Qt.QWidget):
    def __init__(self, title: str):
        super().__init__()
        self._title = title
        self._matrix: np.ndarray | None = None
        self.setMinimumHeight(180)

    def set_matrix(self, matrix: np.ndarray | None) -> None:
        self._matrix = matrix
        self.update()

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(250, 250, 250))
        painter.setPen(QtGui.QColor(20, 20, 20))
        painter.drawText(8, 18, self._title)

        if self._matrix is None or self._matrix.size == 0:
            painter.drawText(8, 46, "Waiting for detected frames...")
            return

        mat = np.asarray(self._matrix, dtype=np.float32)
        mat = mat[np.all(np.isfinite(mat), axis=1)]
        if mat.size == 0:
            painter.drawText(8, 46, "No finite CSI values.")
            return

        low = float(np.percentile(mat, 5))
        high = float(np.percentile(mat, 95))
        if high <= low:
            high = low + 1.0
        norm = np.clip((mat - low) / (high - low), 0.0, 1.0)

        height, width = norm.shape
        image = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
        for y in range(height):
            for x in range(width):
                color = self._color(float(norm[y, x]))
                image.setPixelColor(x, y, color)

        target = self.rect().adjusted(8, 30, -8, -24)
        painter.drawImage(target, image)
        painter.setPen(QtGui.QColor(80, 80, 80))
        painter.drawText(8, self.height() - 6, f"Frames: {height}, carriers: {width}, scale: {low:.1f}..{high:.1f} dB")

    @staticmethod
    def _color(value: float) -> QtGui.QColor:
        value = max(0.0, min(1.0, value))
        r = int(255 * max(0.0, min(1.0, 1.5 * value - 0.2)))
        g = int(255 * max(0.0, min(1.0, 1.5 - abs(value - 0.55) * 2.0)))
        b = int(255 * max(0.0, min(1.0, 1.2 - 1.5 * value)))
        return QtGui.QColor(r, g, b)


class CsiMonitorTopBlock(gr.top_block):
    def __init__(
        self,
        device_args: str,
        center_freq: float,
        sample_rate: float,
        gain: float,
        antenna: str,
        buffer_seconds: float,
    ):
        super().__init__("dual-RX realtime CSI monitor")
        max_samples = max(1, int(round(buffer_seconds * sample_rate)))
        self.usrp = uhd.usrp_source(
            device_args,
            uhd.stream_args(cpu_format="fc32", otw_format="", channels=[0, 1]),
        )
        self.usrp.set_samp_rate(sample_rate)
        for channel in (0, 1):
            self.usrp.set_center_freq(center_freq, channel)
            self.usrp.set_gain(gain, channel)
            self.usrp.set_antenna(antenna, channel)
        self.ring = DualChannelRingSink(max_samples)
        self.connect((self.usrp, 0), (self.ring, 0))
        self.connect((self.usrp, 1), (self.ring, 1))


class CsiMonitorWindow(Qt.QWidget):
    def __init__(
        self,
        device_args: str,
        center_freq: float,
        sample_rate: float,
        gain: float,
        antenna: str,
        buffer_seconds: float,
        update_interval_ms: int,
        threshold: float,
        min_frame_ratio: float,
        max_frames_display: int,
        analysis_seconds: float,
        probe_rate: float,
        active_carrier_count: int,
        tx_scale: float,
        pilot_repeats_per_tx: int,
        frame_format: str,
        sync_tx_mode: str,
        tx_chain_mode: str,
    ):
        super().__init__()
        self.setWindowTitle("USRP B210 realtime CSI monitor")
        self.resize(1250, 900)

        self.center_freq = center_freq
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_frame_ratio = min_frame_ratio
        self.max_frames_display = max_frames_display
        self.cfg = ProbeConfig(
            sample_rate=sample_rate,
            center_freq=center_freq,
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
        self.analysis_samples = max(
            4 * self.cfg.frame_len,
            int(round(analysis_seconds * sample_rate)),
        )
        self.tx0, self.tx1, self.meta = make_waveforms(self.cfg)
        self.template_source = "tx0"
        self.template = self.tx0[: int(self.meta.get("sync_training_len", 2 * self.cfg.sym_len))]
        if float(np.sum(np.abs(self.template) ** 2)) <= 1e-12:
            self.template_source = "tx1"
            self.template = self.tx1[: int(self.meta.get("sync_training_len", 2 * self.cfg.sym_len))]
        if "training_freq_real" in self.meta and "ht_ltf_offsets" in self.meta:
            self.reference_freq = (
                np.array(self.meta["training_freq_real"], dtype=np.float32)
                + 1j * np.array(self.meta["training_freq_imag"], dtype=np.float32)
            ).astype(np.complex64)
        else:
            self.reference_freq = (
                np.array(self.meta["pilot_freq_real"], dtype=np.float32)
                + 1j * np.array(self.meta["pilot_freq_imag"], dtype=np.float32)
            ).astype(np.complex64)

        self.tb = CsiMonitorTopBlock(
            device_args=device_args,
            center_freq=center_freq,
            sample_rate=sample_rate,
            gain=gain,
            antenna=antenna,
            buffer_seconds=buffer_seconds,
        )

        layout = Qt.QVBoxLayout(self)
        self.status_label = Qt.QLabel()
        self.status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        controls = Qt.QHBoxLayout()
        layout.addLayout(controls)
        controls.addWidget(Qt.QLabel("Threshold"))
        self.threshold_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.threshold_slider.setMinimum(1)
        self.threshold_slider.setMaximum(95)
        self.threshold_slider.setValue(int(round(threshold * 100)))
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        controls.addWidget(self.threshold_slider)
        self.threshold_value = Qt.QLabel()
        controls.addWidget(self.threshold_value)

        self.quality_table = Qt.QTableWidget(7, 5)
        self.quality_table.setHorizontalHeaderLabels(
            ["Metric", "RX0", "RX1", "Target", "Status"]
        )
        self.quality_table.verticalHeader().setVisible(False)
        self.quality_table.setEditTriggers(Qt.QAbstractItemView.NoEditTriggers)
        self.quality_table.setMinimumHeight(170)
        self.quality_table.setMaximumHeight(190)
        layout.addWidget(self.quality_table)

        self.heatmaps = [
            HeatmapWidget("|H| RX0<-TX0"),
            HeatmapWidget("|H| RX0<-TX1"),
            HeatmapWidget("|H| RX1<-TX0"),
            HeatmapWidget("|H| RX1<-TX1"),
        ]
        grid = Qt.QGridLayout()
        layout.addLayout(grid)
        for index, widget in enumerate(self.heatmaps):
            grid.addWidget(widget, index // 2, index % 2)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update_analysis)
        self.timer.start(update_interval_ms)
        self._refresh_static_label(device_args, gain, antenna, buffer_seconds)

    def start(self) -> None:
        self.tb.start()

    def stop(self) -> None:
        self.timer.stop()
        self.tb.stop()
        self.tb.wait()

    def closeEvent(self, event) -> None:
        self.stop()
        event.accept()

    def _on_threshold_changed(self, value: int) -> None:
        self.threshold = value / 100.0
        self.threshold_value.setText(f"{self.threshold:.2f}")

    def _refresh_static_label(self, device_args: str, gain: float, antenna: str, buffer_seconds: float) -> None:
        low_hz, high_hz = self.cfg.active_carrier_range_hz
        self.static_text = (
            f"RX args={device_args!r}, freq={self.center_freq / 1e6:.6f} MHz, "
            f"rate={self.sample_rate / 1e6:.3f} MS/s, gain={gain:.1f} dB, antenna={antenna}, "
            f"buffer={buffer_seconds:.3f}s, analysis={self.analysis_samples / self.sample_rate * 1e3:.1f}ms\n"
            f"OFDM FFT={self.cfg.fft_len}, active carriers={len(self.cfg.active_carriers)}, "
            f"frame_format={self.cfg.frame_format}, "
            f"sync_tx_mode={self.cfg.sync_tx_mode}, "
            f"tx_chain_mode={self.cfg.tx_chain_mode}, template={self.template_source}, "
            f"probe_rate={self.cfg.probe_rate_hz:.1f} Hz, tx_scale={self.cfg.tx_scale:.2f}, "
            f"pilot_repeats_per_tx={self.cfg.pilot_repeats_per_tx}, "
            f"spacing={self.cfg.subcarrier_spacing_hz / 1e3:.3f} kHz, "
            f"RF active span={(self.center_freq + low_hz) / 1e6:.6f}.."
            f"{(self.center_freq + high_hz) / 1e6:.6f} MHz"
        )
        self.threshold_value.setText(f"{self.threshold:.2f}")

    def _frame_region_power(self, rx: np.ndarray, starts: np.ndarray, offset: int, length: int) -> float:
        values = []
        for start in starts:
            begin = int(start) + offset
            end = begin + length
            if begin >= 0 and end <= len(rx):
                seg = rx[begin:end]
                values.append(float(np.mean(np.abs(seg) ** 2)))
        return float(np.mean(values)) if values else 0.0

    def _ht_quality(self, rx: np.ndarray, starts: np.ndarray) -> dict[str, float]:
        if "ht_ltf_offsets" not in self.meta or len(starts) == 0:
            return {
                "ht1_snr_db": 0.0,
                "ht2_snr_db": 0.0,
                "ht2_rel_db": 0.0,
                "guard_dbfs": 0.0,
            }
        starts = starts[-min(len(starts), self.max_frames_display) :]
        ht1_offset = int(self.meta["ht_ltf1_offset"])
        ht2_offset = int(self.meta["ht_ltf2_offset"])
        guard_offset = int(self.meta["occupied_len"])
        guard_len = max(1, min(400, self.cfg.frame_len - guard_offset))
        guard = self._frame_region_power(rx, starts, guard_offset, guard_len)

        bins = np.array([int(k) % self.cfg.fft_len for k in self.cfg.active_carriers], dtype=np.int32)
        inactive = np.setdiff1d(np.arange(self.cfg.fft_len), bins)

        def active_inactive_power(offset: int) -> tuple[float, float]:
            active_power = []
            inactive_power = []
            for start in starts:
                begin = int(start) + offset + self.cfg.cp_len
                end = begin + self.cfg.fft_len
                if begin < 0 or end > len(rx):
                    continue
                symbol = rx[begin:end]
                spectrum = np.fft.fft(symbol)
                active_power.append(float(np.mean(np.abs(spectrum[bins]) ** 2)))
                inactive_power.append(float(np.mean(np.abs(spectrum[inactive]) ** 2)))
            if not active_power:
                return 0.0, 0.0
            return float(np.mean(active_power)), float(np.mean(inactive_power))

        ht1_active, ht1_inactive = active_inactive_power(ht1_offset)
        ht2_active, ht2_inactive = active_inactive_power(ht2_offset)
        return {
            "ht1_snr_db": db10(ht1_active / (ht1_inactive + 1e-20)),
            "ht2_snr_db": db10(ht2_active / (ht2_inactive + 1e-20)),
            "ht2_rel_db": db10(ht2_active / (ht1_active + 1e-20)),
            "guard_dbfs": db10(guard),
        }

    def _set_quality_row(
        self,
        row: int,
        metric: str,
        rx0_text: str,
        rx1_text: str,
        target: str,
        ok: bool,
    ) -> None:
        values = [metric, rx0_text, rx1_text, target, "OK" if ok else "CHECK"]
        for col, value in enumerate(values):
            item = Qt.QTableWidgetItem(value)
            if col == 4:
                color = QtGui.QColor(215, 245, 220) if ok else QtGui.QColor(255, 228, 190)
                item.setBackground(color)
            self.quality_table.setItem(row, col, item)

    def _update_quality_table(
        self,
        rx0_quality: dict[str, float],
        rx1_quality: dict[str, float],
        h_db: np.ndarray | None,
        frame_count: int,
    ) -> None:
        ht1_ok = min(rx0_quality["ht1_snr_db"], rx1_quality["ht1_snr_db"]) >= 6.0
        ht2_ok = min(rx0_quality["ht2_snr_db"], rx1_quality["ht2_snr_db"]) >= 6.0
        self._set_quality_row(
            0,
            "HT1 SNR",
            f"{rx0_quality['ht1_snr_db']:.1f} dB",
            f"{rx1_quality['ht1_snr_db']:.1f} dB",
            "> 6 dB",
            ht1_ok,
        )
        self._set_quality_row(
            1,
            "HT2 SNR",
            f"{rx0_quality['ht2_snr_db']:.1f} dB",
            f"{rx1_quality['ht2_snr_db']:.1f} dB",
            "> 6 dB",
            ht2_ok,
        )
        self._set_quality_row(
            2,
            "HT2/HT1 power",
            f"{rx0_quality['ht2_rel_db']:.1f} dB",
            f"{rx1_quality['ht2_rel_db']:.1f} dB",
            "> -6 dB",
            min(rx0_quality["ht2_rel_db"], rx1_quality["ht2_rel_db"]) >= -6.0,
        )
        self._set_quality_row(
            3,
            "Guard power",
            f"{rx0_quality['guard_dbfs']:.1f} dBFS",
            f"{rx1_quality['guard_dbfs']:.1f} dBFS",
            "lower is better",
            True,
        )

        path_names = ["RX0<-TX0", "RX0<-TX1", "RX1<-TX0", "RX1<-TX1"]
        if h_db is None:
            path_values = ["N/A"] * 4
            path_ok = False
        else:
            means = [
                float(np.mean(h_db[:, 0, 0, :])),
                float(np.mean(h_db[:, 0, 1, :])),
                float(np.mean(h_db[:, 1, 0, :])),
                float(np.mean(h_db[:, 1, 1, :])),
            ]
            path_values = [f"{name}: {value:.1f} dB" for name, value in zip(path_names, means)]
            active_indices = [0, 1, 2, 3]
            if self.cfg.tx_chain_mode == "tx0_only":
                active_indices = [0, 2]
            elif self.cfg.tx_chain_mode == "tx1_only":
                active_indices = [1, 3]
            path_ok = all(means[index] > -80.0 for index in active_indices)

        self._set_quality_row(
            4,
            "|H| TX0",
            path_values[0],
            path_values[2],
            "active > -80 dB",
            path_ok if self.cfg.tx_chain_mode != "tx1_only" else True,
        )
        self._set_quality_row(
            5,
            "|H| TX1",
            path_values[1],
            path_values[3],
            "active > -80 dB",
            path_ok if self.cfg.tx_chain_mode != "tx0_only" else True,
        )
        ready = frame_count > 0 and ht1_ok and ht2_ok and path_ok
        self._set_quality_row(
            6,
            "Capture gate",
            f"frames={frame_count}",
            "",
            "OK before saving IQ",
            ready,
        )

    def _update_analysis(self) -> None:
        rx0, rx1 = self.tb.ring.snapshot()
        if len(rx0) < self.cfg.frame_len:
            self.status_label.setText(self.static_text + "\nWaiting for buffer...")
            return

        n_analysis = min(len(rx0), len(rx1), self.analysis_samples)
        rx0_work = rx0[-n_analysis:]
        rx1_work = rx1[-n_analysis:]

        if "ht_ltf_offsets" in self.meta:
            required_len = max(int(x) for x in self.meta["ht_ltf_offsets"]) + self.cfg.sym_len
        elif "tx1_pilot_offsets" in self.meta:
            required_len = max(int(x) for x in self.meta["tx1_pilot_offsets"]) + self.cfg.sym_len
        else:
            required_len = int(self.meta.get("tx1_pilot_offset", 3 * self.cfg.sym_len)) + self.cfg.sym_len

        metric0 = normalized_corr_metric(rx0_work, self.template)
        metric1 = normalized_corr_metric(rx1_work, self.template)
        metric0_max = float(np.max(metric0)) if len(metric0) else 0.0
        metric1_max = float(np.max(metric1)) if len(metric1) else 0.0
        if metric1_max > metric0_max:
            metric = metric1
            sync_channel = "RX1"
        else:
            metric = metric0
            sync_channel = "RX0"

        distance = int(round(self.min_frame_ratio * self.cfg.frame_len))
        peaks = self._find_peaks(metric, self.threshold, distance)
        peaks = peaks[peaks + required_len <= len(rx0_work)]
        if len(peaks):
            grid_base = int(peaks[0])
            starts = np.arange(
                grid_base,
                len(rx0_work) - required_len + 1,
                self.cfg.frame_len,
                dtype=np.int64,
            )
        else:
            grid_base = None
            starts = np.empty(0, dtype=np.int64)

        frames = []
        cfo_hz = []
        for start in starts[-self.max_frames_display :]:
            try:
                h0, w0 = extract_one_frame(rx0_work, int(start), self.cfg, self.reference_freq, self.meta)
                h1, w1 = extract_one_frame(rx1_work, int(start), self.cfg, self.reference_freq, self.meta)
            except ValueError:
                continue
            frames.append(np.stack([h0, h1], axis=0))
            cfo_hz.append([w0, w1])

        rx0_db = 10.0 * np.log10(float(np.mean(np.abs(rx0) ** 2)) + 1e-20)
        rx1_db = 10.0 * np.log10(float(np.mean(np.abs(rx1) ** 2)) + 1e-20)
        expected = len(rx0_work) / self.cfg.frame_len
        detected_rate = len(starts) / max(len(rx0_work) / self.sample_rate, 1e-9)

        h_db = None
        if frames:
            h = np.stack(frames, axis=0).astype(np.complex64)
            h_db = 20.0 * np.log10(np.abs(h) + 1e-12)
            self.heatmaps[0].set_matrix(h_db[:, 0, 0, :])
            self.heatmaps[1].set_matrix(h_db[:, 0, 1, :])
            self.heatmaps[2].set_matrix(h_db[:, 1, 0, :])
            self.heatmaps[3].set_matrix(h_db[:, 1, 1, :])
            cfo = np.asarray(cfo_hz, dtype=np.float64) * self.sample_rate / (2.0 * np.pi)
            cfo_text = f"CFO mean RX0/RX1={np.mean(cfo, axis=0)[0]:.1f}/{np.mean(cfo, axis=0)[1]:.1f} Hz"
        else:
            for widget in self.heatmaps:
                widget.set_matrix(None)
            cfo_text = "CFO mean RX0/RX1=N/A"

        rx0_quality = self._ht_quality(rx0_work, starts)
        rx1_quality = self._ht_quality(rx1_work, starts)
        self._update_quality_table(rx0_quality, rx1_quality, h_db, len(frames))

        self.status_label.setText(
            self.static_text
            + "\n"
            + f"RX power RX0/RX1={rx0_db:.1f}/{rx1_db:.1f} dBFS, "
            + f"corr max RX0/RX1={metric0_max:.3f}/{metric1_max:.3f}, "
            + f"sync={sync_channel}, grid_base={grid_base}, "
            + f"threshold={self.threshold:.2f}, frames={len(starts)}/{expected:.1f}, "
            + f"detected rate={detected_rate:.1f} Hz, extracted={len(frames)}, {cfo_text}"
        )

    @staticmethod
    def _find_peaks(metric: np.ndarray, threshold: float, distance: int) -> np.ndarray:
        if len(metric) == 0:
            return np.empty(0, dtype=np.int64)
        candidate = np.flatnonzero(metric >= threshold)
        if len(candidate) == 0:
            return candidate.astype(np.int64)
        peaks = []
        last = -distance
        for index in candidate:
            if index - last < distance:
                if peaks and metric[index] > metric[peaks[-1]]:
                    peaks[-1] = int(index)
                    last = int(index)
                continue
            peaks.append(int(index))
            last = int(index)
        return np.asarray(peaks, dtype=np.int64)


def parse_args() -> argparse.Namespace:
    defaults = runtime_defaults("rx_monitor")
    tx_defaults = runtime_defaults("tx")
    parser = argparse.ArgumentParser()
    parser.add_argument("--args", default=defaults["args"], help='UHD args, e.g. "serial=3271260"')
    parser.add_argument("--freq", type=float, default=float(defaults["freq"]))
    parser.add_argument("--rate", type=float, default=float(defaults["rate"]))
    parser.add_argument("--gain", type=float, default=float(defaults["gain"]))
    parser.add_argument("--antenna", default=str(defaults["antenna"]))
    parser.add_argument("--buffer-seconds", type=float, default=float(defaults["buffer_seconds"]))
    parser.add_argument("--analysis-seconds", type=float, default=float(defaults["analysis_seconds"]))
    parser.add_argument("--update-interval-ms", type=int, default=int(defaults["update_interval_ms"]))
    parser.add_argument("--threshold", type=float, default=float(defaults["threshold"]))
    parser.add_argument("--min-frame-ratio", type=float, default=float(defaults["min_frame_ratio"]))
    parser.add_argument("--max-frames-display", type=int, default=int(defaults["max_frames_display"]))
    parser.add_argument("--probe-rate", type=float, default=float(defaults.get("probe_rate", tx_defaults["probe_rate"])))
    parser.add_argument("--active-carrier-count", type=int, default=int(defaults.get("active_carrier_count", tx_defaults["active_carrier_count"])))
    parser.add_argument("--tx-scale", type=float, default=float(defaults.get("tx_scale", tx_defaults["tx_scale"])))
    parser.add_argument(
        "--pilot-repeats-per-tx",
        type=int,
        default=int(defaults.get("pilot_repeats_per_tx", tx_defaults["pilot_repeats_per_tx"])),
    )
    parser.add_argument("--frame-format", default=str(defaults.get("frame_format", tx_defaults["frame_format"])))
    parser.add_argument("--sync-tx-mode", choices=["both", "tx0_only"], default=str(defaults.get("sync_tx_mode", tx_defaults["sync_tx_mode"])))
    parser.add_argument("--tx-chain-mode", choices=["both", "tx0_only", "tx1_only"], default=str(defaults.get("tx_chain_mode", tx_defaults["tx_chain_mode"])))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = Qt.QApplication(sys.argv)
    window = CsiMonitorWindow(
        device_args=args.args,
        center_freq=args.freq,
        sample_rate=args.rate,
        gain=args.gain,
        antenna=args.antenna,
        buffer_seconds=args.buffer_seconds,
        update_interval_ms=args.update_interval_ms,
        threshold=args.threshold,
        min_frame_ratio=args.min_frame_ratio,
        max_frames_display=args.max_frames_display,
        analysis_seconds=args.analysis_seconds,
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
