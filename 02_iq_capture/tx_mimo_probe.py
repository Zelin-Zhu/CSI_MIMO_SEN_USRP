#!/usr/bin/env python3
from __future__ import annotations
import argparse, signal, sys, time
from pathlib import Path
from gnuradio import blocks, gr, uhd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from common.frame_design import CFG, ProbeConfig, make_waveforms, runtime_defaults

class MimoProbeTx(gr.top_block):
    def __init__(self, args: str, freq: float, rate: float, gain: float, antenna: str,
                 probe_rate: float, tx_scale: float, pilot_repeats_per_tx: int,
                 frame_format: str, sync_tx_mode: str, tx_chain_mode: str,
                 active_carrier_count: int, debug_out_dir: Path | None = None,
                 debug_frames: int = 0):
        super().__init__("2xTX OFDM CSI probe transmitter")
        cfg = ProbeConfig(sample_rate=rate, center_freq=freq, fft_len=CFG.fft_len,
                          cp_len=CFG.cp_len, probe_rate_hz=probe_rate,
                          active_carrier_count=active_carrier_count,
                          tx_scale=tx_scale, pilot_repeats_per_tx=pilot_repeats_per_tx,
                          frame_format=frame_format,
                          sync_tx_mode=sync_tx_mode,
                          tx_chain_mode=tx_chain_mode,
                          seed=CFG.seed)
        tx0, tx1, meta = make_waveforms(cfg)
        self.src0 = blocks.vector_source_c(tx0.tolist(), True, 1, [])
        self.src1 = blocks.vector_source_c(tx1.tolist(), True, 1, [])
        if debug_out_dir is not None and debug_frames > 0:
            debug_out_dir.mkdir(parents=True, exist_ok=True)
            sample_count = int(meta["frame_len"]) * debug_frames
            self.debug_head0 = blocks.head(gr.sizeof_gr_complex, sample_count)
            self.debug_head1 = blocks.head(gr.sizeof_gr_complex, sample_count)
            self.debug_file0 = blocks.file_sink(
                gr.sizeof_gr_complex, str(debug_out_dir / "tx_debug_tx0.fc32"), False
            )
            self.debug_file1 = blocks.file_sink(
                gr.sizeof_gr_complex, str(debug_out_dir / "tx_debug_tx1.fc32"), False
            )
            self.connect(self.src0, self.debug_head0, self.debug_file0)
            self.connect(self.src1, self.debug_head1, self.debug_file1)
        self.usrp = uhd.usrp_sink(args, uhd.stream_args(cpu_format="fc32", otw_format="", channels=[0, 1]), "")
        self.usrp.set_samp_rate(rate)
        for ch in (0, 1):
            self.usrp.set_center_freq(freq, ch)
            self.usrp.set_gain(gain, ch)
            self.usrp.set_antenna(antenna, ch)
        self.connect(self.src0, (self.usrp, 0))
        self.connect(self.src1, (self.usrp, 1))
        print(f"TX: {freq/1e6:.6f} MHz, {rate/1e6:.3f} MS/s, probe={probe_rate:.1f} Hz, gain={gain:.1f} dB, frame={meta['frame_len']} samples, active_carriers={len(meta['active_carriers'])}, format={meta['frame_format']}, sync_tx_mode={meta.get('sync_tx_mode', 'both')}, tx_chain_mode={meta.get('tx_chain_mode', 'both')}")
        if debug_out_dir is not None and debug_frames > 0:
            print(f"TX debug recording: {debug_frames} frames -> {debug_out_dir}")

def parse_args():
    defaults = runtime_defaults("tx")
    p = argparse.ArgumentParser()
    p.add_argument("--args", default=defaults["args"], help='UHD args, e.g. "serial=XXXXXXXX"')
    p.add_argument("--freq", type=float, default=float(defaults["freq"]))
    p.add_argument("--rate", type=float, default=float(defaults["rate"]))
    p.add_argument("--gain", type=float, default=float(defaults["gain"]))
    p.add_argument("--antenna", default=str(defaults["antenna"]))
    p.add_argument("--probe-rate", type=float, default=float(defaults["probe_rate"]))
    p.add_argument("--active-carrier-count", type=int, default=int(defaults["active_carrier_count"]))
    p.add_argument("--tx-scale", type=float, default=float(defaults["tx_scale"]))
    p.add_argument("--pilot-repeats-per-tx", type=int, default=int(defaults["pilot_repeats_per_tx"]))
    p.add_argument("--frame-format", default=str(defaults["frame_format"]))
    p.add_argument("--sync-tx-mode", choices=["both", "tx0_only"], default=str(defaults["sync_tx_mode"]))
    p.add_argument("--tx-chain-mode", choices=["both", "tx0_only", "tx1_only"], default=str(defaults["tx_chain_mode"]))
    p.add_argument("--debug-out-dir", type=Path, help="Optional directory for TX-before-USRP debug IQ.")
    p.add_argument("--debug-frames", type=int, default=0, help="Number of TX frames to save when --debug-out-dir is set.")
    return p.parse_args()

def main():
    a = parse_args()
    tb = MimoProbeTx(a.args, a.freq, a.rate, a.gain, a.antenna, a.probe_rate, a.tx_scale, a.pilot_repeats_per_tx, a.frame_format, a.sync_tx_mode, a.tx_chain_mode, a.active_carrier_count, a.debug_out_dir, a.debug_frames)
    def stop_handler(*_):
        tb.stop(); tb.wait(); sys.exit(0)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    tb.start(); print("Transmitting. Press Ctrl+C to stop.")
    while True: time.sleep(1)

if __name__ == "__main__": main()
