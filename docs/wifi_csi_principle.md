# WiFi CSI Frame And Extraction Principle

本文说明本项目下一阶段采用的 WiFi-like CSI sounding 思路：frame 设计尽量复刻标准 WiFi 中用于信道估计的训练字段，接收端从 raw IQ 经过同步、CFO 校正和 HT-LTF 解耦得到 CSI。

## 1. 标准 WiFi 中 CSI 来自哪里

标准 WiFi 接收机不是直接对任意 IQ 片段做 FFT 得到 CSI。一个 packet 的前导训练字段会先用于同步和信道估计。典型流程是：

```text
L-STF -> L-LTF -> SIGNAL/HT-SIG -> HT-STF -> HT-LTF -> DATA
```

其中：

```text
L-STF: packet detection, coarse timing, coarse CFO
L-LTF: fine timing, fine CFO, legacy channel estimate
HT-LTF: MIMO stream channel estimate
DATA pilot: residual CFO/SFO/common phase tracking
```

商用 WiFi 网卡输出的 CSI 通常已经经过逐包同步、CFO 补偿、符号边界定位和 LTF 信道估计。它不是 raw IQ 的直接 FFT。

## 2. 本项目的 WiFi-like CSI sounding frame

本项目当前默认 frame 是：

```text
short training -> legacy LTF -> HT-LTF1 -> HT-LTF2 -> guard
```

它不是完整可解码的 802.11 packet，因为没有完整 SIGNAL、MAC payload、FEC 和标准速率控制。它只保留 CSI 估计所需的关键结构。

2x2 MIMO HT-LTF 使用 Walsh 正交矩阵：

```text
HT-LTF1: TX0 = +X, TX1 = +X
HT-LTF2: TX0 = +X, TX1 = -X
```

这里的 `X[k]` 是第 `k` 个有效子载波上的已知 LTF 频域符号。

## 3. 2x2 MIMO H 估计公式

对某一个 RX 天线和某一个子载波 `k`，两个 HT-LTF 符号的接收频域值为：

```text
Y1[k] = H0[k] X[k] + H1[k] X[k] + N1[k]
Y2[k] = H0[k] X[k] - H1[k] X[k] + N2[k]
```

其中：

```text
H0[k]: TX0 到当前 RX 的 CSI
H1[k]: TX1 到当前 RX 的 CSI
X[k]: 已知 LTF 子载波符号
N1[k], N2[k]: 噪声和残余同步误差
```

因此可以解出：

```text
H0[k] = (Y1[k] + Y2[k]) / (2 X[k])
H1[k] = (Y1[k] - Y2[k]) / (2 X[k])
```

两个 RX 天线分别执行这一步，最终得到：

```text
H[frame, rx, tx, subcarrier]
```

## 4. Raw IQ 到 CSI 的恢复流程

接收端处理链路应该是：

```text
raw IQ
  -> packet detection
  -> coarse timing
  -> LTF timing refinement
  -> CFO estimation from repeated LTF
  -> CFO correction
  -> HT-LTF FFT
  -> MIMO LTF orthogonal decoding
  -> H estimation
  -> optional CSI sanitization
  -> quality metrics
```

当前代码中的离线提取器重点实现：

```text
frame detection: 使用 STF 的 16-sample 延迟自相关做粗检测
timing refinement: 使用 L-LTF matched correlation 做精细定时
CFO estimation: 使用两个连续 LTF 的相位差估计 CFO
CFO correction: 对 frame 内样本做复指数相位补偿
HT-LTF extraction: 对两个 HT-LTF 符号去 CP 后 FFT
MIMO decoding: 使用 (Y1+Y2)/(2X) 和 (Y1-Y2)/(2X)
```

旧的完整同步模板相关仍可作为 debug 对比，但不应作为主 packet
detection 方法。标准 WiFi 风格的 STF 延迟自相关对多径和信道响应更稳健。

## 5. CSI Sanitization 的位置

同步和 H 估计之后，CSI 仍可能包含：

```text
残余 common phase error
符号边界误差造成的子载波线性相位斜率
采样时钟偏差造成的相位斜率
硬件链路固定相位偏置
异常 frame
```

因此可以做轻量 sanitization：

```text
去除跨子载波线性相位斜率
去除每个 frame 的 common phase
剔除低相关或 CFO 异常 frame
```

但是 sanitization 不应该替代同步，也不应该盲目时间平滑。过强平滑会掩盖真实短时信道变化。正确原则是：

```text
先做好 WiFi-like packet synchronization
再做 LTF-based H estimation
最后只做可解释的轻量 CSI sanitization
```

## 6. 当前实现的边界

当前 frame 和提取流程更接近真实 WiFi CSI 的核心机制，但仍不是完整 WiFi PHY：

```text
没有完整标准 802.11 packet framing
没有完整 SIGNAL/HT-SIG 字段
没有 payload 解调
没有 DATA pilot 跟踪残余 CFO/SFO
不能被商用网卡直接解码
```

它适合当前 USRP B210 2x2 MIMO sensing 实验：先在干净频段采 raw IQ，再通过 WiFi-like HT-LTF 流程恢复更可信的 CSI。
