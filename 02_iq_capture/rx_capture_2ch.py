#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from gnuradio import blocks, gr, uhd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from common.frame_design import CFG, ProbeConfig, save_probe_metadata, runtime_defaults

class DualRxCapture(gr.top_block):
    def __init__(self, args: str, freq: float, rate: float, gain: float, antenna: str, seconds: float, out_dir: Path, probe_rate: float, tx_scale: float, pilot_repeats_per_tx: int, frame_format: str):
        super().__init__("2xRX raw IQ capture")
        out_dir.mkdir(parents=True, exist_ok=True)
        total_samples = int(round(seconds * rate))
        self.usrp = uhd.usrp_source(args, uhd.stream_args(cpu_format="fc32", otw_format="", channels=[0, 1]))
        self.usrp.set_samp_rate(rate)
        for ch in (0, 1):
            self.usrp.set_center_freq(freq, ch)
            self.usrp.set_gain(gain, ch)
            self.usrp.set_antenna(antenna, ch)
        self.head0 = blocks.head(gr.sizeof_gr_complex, total_samples)
        self.head1 = blocks.head(gr.sizeof_gr_complex, total_samples)
        self.file0 = blocks.file_sink(gr.sizeof_gr_complex, str(out_dir / "rx0.fc32"), False)
        self.file1 = blocks.file_sink(gr.sizeof_gr_complex, str(out_dir / "rx1.fc32"), False)
        self.connect((self.usrp, 0), self.head0, self.file0)
        self.connect((self.usrp, 1), self.head1, self.file1)
        cfg = {"uhd_args": args, "center_freq": freq, "sample_rate": rate, "rx_gain_db": gain,
               "rx_antenna": antenna, "seconds": seconds, "total_samples_per_channel": total_samples,
               "dtype": "complex64", "frame_format": frame_format}
        (out_dir / "capture_config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        probe_cfg = ProbeConfig(sample_rate=rate, center_freq=freq, fft_len=CFG.fft_len, cp_len=CFG.cp_len,
                                probe_rate_hz=probe_rate, tx_scale=tx_scale,
                                pilot_repeats_per_tx=pilot_repeats_per_tx,
                                frame_format=frame_format, seed=CFG.seed)
        save_probe_metadata(out_dir / "probe_metadata.json", probe_cfg)
        print(f"RX: {freq/1e6:.6f} MHz, {rate/1e6:.3f} MS/s, gain={gain:.1f} dB, duration={seconds:.1f}s, format={frame_format}, out={out_dir}")

def parse_args():
    defaults = runtime_defaults("rx_capture")
    p = argparse.ArgumentParser()
    p.add_argument("--args", default=defaults["args"], help='UHD args, e.g. "serial=YYYYYYYY"')
    p.add_argument("--freq", type=float, default=float(defaults["freq"]))
    p.add_argument("--rate", type=float, default=float(defaults["rate"]))
    p.add_argument("--gain", type=float, default=float(defaults["gain"]))
    p.add_argument("--antenna", default=str(defaults["antenna"]))
    p.add_argument("--seconds", type=float, default=float(defaults["seconds"]))
    p.add_argument("--out-dir", type=Path, default=Path(defaults["out_dir"]))
    p.add_argument("--probe-rate", type=float, default=float(defaults["probe_rate"]))
    p.add_argument("--tx-scale", type=float, default=float(defaults["tx_scale"]))
    p.add_argument("--pilot-repeats-per-tx", type=int, default=int(defaults["pilot_repeats_per_tx"]))
    p.add_argument("--frame-format", default=str(defaults["frame_format"]))
    return p.parse_args()

def main():
    a = parse_args(); tb = DualRxCapture(a.args, a.freq, a.rate, a.gain, a.antenna, a.seconds, a.out_dir, a.probe_rate, a.tx_scale, a.pilot_repeats_per_tx, a.frame_format)
    print("Capturing raw IQ..."); tb.run(); print("Capture complete.")

if __name__ == "__main__": main()
