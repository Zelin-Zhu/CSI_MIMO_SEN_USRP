#!/usr/bin/env python3
"""
Dual-channel USRP B210 RX spectrum viewer.

Features:
- Receives RX0 and RX1 from one B210.
- Displays both channels in a QT frequency plot.
- Displays both channels in a QT waterfall plot.
- Does NOT save IQ samples to disk.
- Does NOT require a Throttle block because the USRP Source controls the rate.

Example:
    python3 rx_spectrum_gui.py \
      --args "serial=3271260" \
      --freq 1800e6 \
      --rate 1e6 \
      --gain 20 \
      --antenna "TX/RX"
"""

from __future__ import annotations

import argparse
import signal
import sys

from PyQt5 import Qt
from gnuradio import gr, qtgui, uhd
from gnuradio.fft import window
import sip

from csi_probe_common import runtime_defaults


class DualRxSpectrumViewer(gr.top_block, Qt.QWidget):
    def __init__(
        self,
        device_args: str,
        center_freq: float,
        sample_rate: float,
        gain: float,
        antenna: str,
        fft_size: int,
    ):
        gr.top_block.__init__(self, "Dual-channel B210 RX spectrum viewer")
        Qt.QWidget.__init__(self)

        self.setWindowTitle("USRP B210 dual-RX spectrum viewer")
        self.resize(1280, 900)

        self.device_args = device_args
        self.center_freq = center_freq
        self.sample_rate = sample_rate
        self.gain = gain
        self.antenna = antenna
        self.fft_size = fft_size

        root_layout = Qt.QVBoxLayout(self)
        controls_layout = Qt.QGridLayout()
        root_layout.addLayout(controls_layout)

        self.freq_label = Qt.QLabel()
        self.gain_label = Qt.QLabel()
        controls_layout.addWidget(self.freq_label, 0, 0)
        controls_layout.addWidget(self.gain_label, 1, 0)

        self.freq_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.freq_slider.setMinimum(50)
        self.freq_slider.setMaximum(6000)
        self.freq_slider.setValue(int(round(center_freq / 1e6)))
        self.freq_slider.setSingleStep(1)
        self.freq_slider.setPageStep(10)
        controls_layout.addWidget(self.freq_slider, 0, 1)

        self.gain_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.gain_slider.setMinimum(0)
        self.gain_slider.setMaximum(76)
        self.gain_slider.setValue(int(round(gain)))
        self.gain_slider.setSingleStep(1)
        controls_layout.addWidget(self.gain_slider, 1, 1)

        self.freq_slider.valueChanged.connect(self._on_freq_slider)
        self.gain_slider.valueChanged.connect(self._on_gain_slider)

        self.usrp = uhd.usrp_source(
            device_args,
            uhd.stream_args(cpu_format="fc32", otw_format="", channels=[0, 1]),
        )
        self.usrp.set_samp_rate(sample_rate)

        for channel in (0, 1):
            self.usrp.set_center_freq(center_freq, channel)
            self.usrp.set_gain(gain, channel)
            self.usrp.set_antenna(antenna, channel)

        self.freq_sink = qtgui.freq_sink_c(
            fft_size,
            window.WIN_BLACKMAN_hARRIS,
            center_freq,
            sample_rate,
            "Dual-RX spectrum",
            2,
        )
        self.freq_sink.set_update_time(0.10)
        self.freq_sink.set_y_axis(-140, 10)
        self.freq_sink.set_y_label("Relative power", "dB")
        self.freq_sink.enable_autoscale(False)
        self.freq_sink.enable_grid(True)
        self.freq_sink.set_line_label(0, "RX0")
        self.freq_sink.set_line_label(1, "RX1")

        self.waterfall_sink = qtgui.waterfall_sink_c(
            fft_size,
            window.WIN_BLACKMAN_hARRIS,
            center_freq,
            sample_rate,
            "Dual-RX waterfall",
            2,
        )
        self.waterfall_sink.set_update_time(0.10)
        self.waterfall_sink.enable_grid(True)
        self.waterfall_sink.set_line_label(0, "RX0")
        self.waterfall_sink.set_line_label(1, "RX1")
        self.waterfall_sink.set_intensity_range(-140, 10)

        freq_widget = sip.wrapinstance(self.freq_sink.qwidget(), Qt.QWidget)
        waterfall_widget = sip.wrapinstance(self.waterfall_sink.qwidget(), Qt.QWidget)
        root_layout.addWidget(freq_widget)
        root_layout.addWidget(waterfall_widget)

        self.connect((self.usrp, 0), (self.freq_sink, 0))
        self.connect((self.usrp, 1), (self.freq_sink, 1))
        self.connect((self.usrp, 0), (self.waterfall_sink, 0))
        self.connect((self.usrp, 1), (self.waterfall_sink, 1))

        self._refresh_labels()

        print("RX spectrum viewer configuration")
        print(f"  device args     : {device_args!r}")
        print(f"  center frequency: {center_freq / 1e6:.3f} MHz")
        print(f"  sample rate     : {sample_rate / 1e6:.3f} MS/s")
        print(f"  gain            : {gain:.1f} dB")
        print(f"  antenna         : {antenna}")
        print("  disk recording  : disabled")

    def _refresh_labels(self) -> None:
        self.freq_label.setText(
            f"Center frequency: {self.center_freq / 1e6:.3f} MHz "
            f"(visible span: ±{self.sample_rate / 2e6:.3f} MHz)"
        )
        self.gain_label.setText(f"RX gain: {self.gain:.1f} dB")

    def _on_freq_slider(self, value_mhz: int) -> None:
        self.set_center_freq(float(value_mhz) * 1e6)

    def _on_gain_slider(self, value_db: int) -> None:
        self.set_gain(float(value_db))

    def set_center_freq(self, center_freq: float) -> None:
        self.center_freq = center_freq
        for channel in (0, 1):
            self.usrp.set_center_freq(center_freq, channel)
        self.freq_sink.set_frequency_range(center_freq, self.sample_rate)
        self.waterfall_sink.set_frequency_range(center_freq, self.sample_rate)
        self._refresh_labels()

    def set_gain(self, gain: float) -> None:
        self.gain = gain
        for channel in (0, 1):
            self.usrp.set_gain(gain, channel)
        self._refresh_labels()

    def closeEvent(self, event) -> None:
        self.stop()
        self.wait()
        event.accept()


def parse_args() -> argparse.Namespace:
    defaults = runtime_defaults("rx_gui")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--args",
        default=defaults["args"],
        help='UHD device args, for example: "serial=3271260"',
    )
    parser.add_argument("--freq", type=float, default=float(defaults["freq"]))
    parser.add_argument("--rate", type=float, default=float(defaults["rate"]))
    parser.add_argument("--gain", type=float, default=float(defaults["gain"]))
    parser.add_argument("--antenna", default=str(defaults["antenna"]))
    parser.add_argument("--fft-size", type=int, default=int(defaults["fft_size"]))
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    app = Qt.QApplication(sys.argv)
    tb = DualRxSpectrumViewer(
        device_args=args.args,
        center_freq=args.freq,
        sample_rate=args.rate,
        gain=args.gain,
        antenna=args.antenna,
        fft_size=args.fft_size,
    )
    tb.start()
    tb.show()

    def stop_handler(*_):
        tb.stop()
        tb.wait()
        app.quit()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    app.exec_()


if __name__ == "__main__":
    main()
