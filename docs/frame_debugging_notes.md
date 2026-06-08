# Frame 调试经验记录

本文记录 24-carrier TX0-only 实验中发现的 frame 设计问题、定位方法和后续检查流程。

## 背景

当前实验使用 WiFi-like HT20 2x2 LTF sounding frame：

```text
STF       160 samples
L-LTF     160 samples = 32-sample CP + 64 + 64
HT-LTF1    80 samples = 16-sample CP + 64
HT-LTF2    80 samples = 16-sample CP + 64
Guard    1520 samples
Frame    2000 samples at 20 MS/s = 100 us
```

在 `tx_chain_mode=tx0_only` 时，预期是：

```text
TX0: STF + L-LTF + HT-LTF1 + HT-LTF2 + guard
TX1: all-zero
```

因此，单天线发射并不意味着 `HT-LTF2` 不发。`HT-LTF2` 仍应由 TX0 发射。

## 现象

早期 raw IQ 可视化显示：

```text
L-LTF 能够被检测到或局部可见；
HT-LTF1 很弱；
HT-LTF2 基本贴近 guard/noise；
高功率连续段约 160 samples，而不是预期的 480 samples。
```

因此，直接进行 CSI extraction 会得到不可靠的 H：

```text
相邻 frame complex H correlation 很低；
相邻 frame amplitude step 很大；
HT-LTF active/inactive SNR 低于质量门限。
```

这类数据不应该用于 CSI 分析。

## 关键定位方法

### 1. 区分 TX-before-USRP 和 RX-after-air

仅检查 Python 生成的理想 waveform 不够，因为它没有经过 GNU Radio、USRP、天线和无线信道。

因此加入 TX-side debug IQ：

```text
tx_debug_tx0.fc32: vector_source 送入 USRP sink 前的 TX0 IQ
tx_debug_tx1.fc32: vector_source 送入 USRP sink 前的 TX1 IQ
rx0.fc32/rx1.fc32: RX 端实际采集到的空口 IQ
```

使用：

```bash
bash 02_iq_capture/capture_once_tx_then_rx.sh data/captures/<capture_id> tx0_only 0.2
python3 02_iq_capture/compare_tx_debug_rx_frame.py --capture-dir data/captures/<capture_id>
```

判断逻辑：

```text
如果 tx_debug_tx0 有完整 480 samples occupied，但 RX 没有：
问题在 USRP/RF/天线/接收侧，或链路 SNR 不足。

如果 tx_debug_tx0 本身也没有完整 480 samples：
问题在 frame generator、GNU Radio TX flowgraph 或循环输出。
```

### 2. 不依赖字段对齐的高功率窗口扫描

只按 metadata offset 看字段有风险，因为 frame 起点可能没对齐。更稳妥的第一步是扫描 raw stream 中的高功率连续窗口：

```text
预期 occupied 高功率长度: 480 samples
若只看到约 160 samples: 说明只有一段训练字段明显高于噪声
```

这个方法能快速判断是否值得进入 CSI extraction。

## 已发现的根因

TX-side debug 显示旧 frame 中各字段功率严重不平衡：

```text
STF        about -3 dB
L-LTF      about -28 dB
HT-LTF1    about -28 dB
HT-LTF2    about -28 dB
```

也就是说，`STF` 比 `L-LTF/HT-LTF` 强约 25 dB。结果是 USRP 输出动态范围被 STF 占用，后续 LTF/HT-LTF 虽然存在，但空口接收时接近噪声。

这解释了为什么 raw IQ 中看起来只有一段约 160 samples 的强信号。

## 修复方式

修复已在 `common/frame_design.py` 中完成：

```text
先将 STF 的 RMS 匹配到 LTF useful symbol；
再整体按 tx_scale 做 peak scaling。
```

修复后 TX-side debug 中各字段功率变为同量级：

```text
STF        about -7.9 dB
L-LTF      about -7.9 to -10.9 dB
HT-LTF1    about -7.9 to -10.9 dB
HT-LTF2    about -7.9 to -10.9 dB
```

`tx_debug_tx0` 现在能看到完整的 480-sample occupied frame，`tx_debug_tx1` 在 `tx0_only` 下保持全零。

## 当前 RMS 修复后观察

RMS 修复后的 `tx_debug_tx0` 已经正常，但最新 RX raw IQ 中仍未稳定检测到 frame：

```text
RX STF delay metric max 约 0.12-0.15；
template corr 仍低；
best 480-sample raw power window 仅高于噪声约 3-4 dB；
默认 CSI frame detector 找不到稳定 peaks。
```

这说明 frame generator 问题已修复，但空口链路仍然偏弱，下一步应优先检查 RF 链路质量。

## 后续实验建议

### 优先级 1：有线 loopback

使用 TX0 到 RX0/RX1 的同轴线和合适衰减器，验证：

```text
RX raw IQ 是否能看到完整 480 samples occupied；
STF/L-LTF/HT-LTF1/HT-LTF2 是否都明显高于 guard；
CSI extraction 是否得到稳定 H。
```

如果有线 loopback 正常，而无线不正常，则问题主要是天线、距离、遮挡、频段或链路预算。

### 优先级 2：采集前 raw frame gate

在进入 CSI extraction 前，必须先检查：

```text
HT-LTF1 over guard > 6 dB
HT-LTF2 over guard > 6 dB
HT-LTF active/inactive SNR > 6 dB
高功率 occupied 区接近 480 samples
```

不满足这些条件时，不应该分析 H。

### 优先级 3：减少实时 GUI 负载

RX 采集日志仍偶尔出现 overflow：

```text
usrp_source: 1 overflows occurred
```

这说明主机或 USB 链路仍可能有压力。正式采集时尽量关闭 monitor GUI，只保留 TX 和 raw IQ capture。

## 结论

本轮调试证明：

```text
早期 CSI 不稳定并不是因为单天线模式不发 HT-LTF2；
也不是 Python generator 完全漏掉 HT-LTF；
而是 STF 与 LTF/HT-LTF 的功率归一化严重失衡。
```

该问题已经修复。后续若 RX 仍无法看到完整 frame，应转向 RF 链路、天线、距离、接收增益、overflow 和有线 loopback 排查。
