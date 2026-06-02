#!/usr/bin/env python3
from __future__ import annotations
import argparse, signal, sys, time
from gnuradio import blocks, gr, uhd
from csi_probe_common import CFG, ProbeConfig, make_waveforms

class MimoProbeTx(gr.top_block):
    def __init__(self, args: str, freq: float, rate: float, gain: float, antenna: str,
                 probe_rate: float, tx_scale: float):
        super().__init__("2xTX OFDM CSI probe transmitter")
        cfg = ProbeConfig(sample_rate=rate, center_freq=freq, fft_len=CFG.fft_len,
                          cp_len=CFG.cp_len, probe_rate_hz=probe_rate,
                          tx_scale=tx_scale, seed=CFG.seed)
        tx0, tx1, meta = make_waveforms(cfg)
        self.src0 = blocks.vector_source_c(tx0.tolist(), True, 1, [])
        self.src1 = blocks.vector_source_c(tx1.tolist(), True, 1, [])
        self.usrp = uhd.usrp_sink(args, uhd.stream_args(cpu_format="fc32", otw_format="", channels=[0, 1]), "")
        self.usrp.set_samp_rate(rate)
        for ch in (0, 1):
            self.usrp.set_center_freq(freq, ch)
            self.usrp.set_gain(gain, ch)
            self.usrp.set_antenna(antenna, ch)
        self.connect(self.src0, (self.usrp, 0))
        self.connect(self.src1, (self.usrp, 1))
        print(f"TX: {freq/1e6:.6f} MHz, {rate/1e6:.3f} MS/s, probe={probe_rate:.1f} Hz, gain={gain:.1f} dB, frame={meta['frame_len']} samples")

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--args", default="", help='UHD args, e.g. "serial=XXXXXXXX"')
    p.add_argument("--freq", type=float, default=CFG.center_freq)
    p.add_argument("--rate", type=float, default=CFG.sample_rate)
    p.add_argument("--gain", type=float, default=10.0)
    p.add_argument("--antenna", default="TX/RX")
    p.add_argument("--probe-rate", type=float, default=CFG.probe_rate_hz)
    p.add_argument("--tx-scale", type=float, default=CFG.tx_scale)
    return p.parse_args()

def main():
    a = parse_args()
    tb = MimoProbeTx(a.args, a.freq, a.rate, a.gain, a.antenna, a.probe_rate, a.tx_scale)
    def stop_handler(*_):
        tb.stop(); tb.wait(); sys.exit(0)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    tb.start(); print("Transmitting. Press Ctrl+C to stop.")
    while True: time.sleep(1)

if __name__ == "__main__": main()
