# WiFi-like Training CSI Stability And Sanitization Analysis

## 1. Purpose

This note evaluates the latest 5-second CSI capture after switching to the
WiFi-like training frame. The goal is to answer:

```text
Is the new frame synchronization better?
Is adjacent-frame H more similar than before?
Does simple CSI sanitization improve stability?
Can the final data reflect a mostly static channel?
```

## 2. Latest Data

Latest capture:

```text
experiments/03_5s_csi_extraction_analysis/data/test_rx_5s
```

After re-extracting CSI with dual-channel sync selection and fixed frame grid:

```text
H.shape = (4999, 2, 2, 52)
duration = 4.999 s
sync channel = RX1
probe rate = 1000 Hz
```

Frame-start spacing:

```text
4990 intervals are exactly 1000 samples
only 4 intervals are not 1000 samples
```

Compared with the older capture:

```text
old non-1000 frame intervals: 235
new non-1000 frame intervals: 4
```

Conclusion: the WiFi-like training frame clearly improves frame detection and
frame-grid regularity.

## 3. Raw Adjacent-Frame CSI Stability

Raw complex `H` adjacent-frame metrics:

```text
complex correlation mean: 0.390
complex correlation median: 0.392
relative complex change mean: 1.271
mean absolute amplitude step: 4.84 dB
median absolute amplitude step: 4.83 dB
mean absolute phase step: 1.31 rad
```

Per-link mean absolute amplitude step:

```text
RX0<-TX0: 6.01 dB
RX0<-TX1: 6.00 dB
RX1<-TX0: 3.52 dB
RX1<-TX1: 3.84 dB
```

Interpretation:

```text
Raw complex H is still not frame-to-frame stable.
RX1 links are more stable than RX0 links.
The latest raw complex H is not more adjacent-frame similar than the previous H.
```

The new frame synchronization is better, but raw complex CSI is still dominated
by residual phase error, single-symbol pilot estimation noise, and per-frame
channel estimation noise.

## 4. Simple Sanitization Result

A simple phase sanitization was tested:

```text
unwrap phase over subcarriers
fit phase[k] = a*k + b per frame/link
subtract fitted linear phase
reconstruct complex H using original amplitude
```

Result:

```text
raw complex H correlation mean: 0.390
linear phase sanitized H correlation mean: 0.104
amplitude-only |H| correlation mean: 0.839
```

The simple linear phase sanitization does not improve this dataset. It likely
overfits noisy per-frame phase and removes useful residual structure. This means
the current limiting factor is not only common phase or subcarrier-linear phase;
amplitude noise and pilot-estimation noise are also significant.

## 5. Static Channel Evidence From Amplitude Features

Although raw complex `H` is not stable, the averaged amplitude feature is much
more stable.

Mean adjacent step of subcarrier-averaged amplitude:

```text
no smoothing: 0.717 dB
5-frame smoothing: 0.143 dB
10-frame smoothing: 0.072 dB
20-frame smoothing: 0.036 dB
50-frame smoothing: 0.015 dB
100-frame smoothing: 0.007 dB
```

This shows that the static nature of the channel is visible after feature
aggregation and temporal smoothing.

Conclusion:

```text
The final data does not show a static channel if raw complex H is compared frame by frame.
The final data does show a mostly static channel if using amplitude-based, subcarrier-averaged, smoothed CSI features.
```

## 6. Practical Conclusion

The WiFi-like training update improved synchronization, but it is not sufficient
to make raw complex CSI frame-to-frame constant.

For current sensing experiments, use:

```text
abs(H)
subcarrier-averaged amplitude
moving-average filtered amplitude
RX-relative phase after careful filtering
PCA or window-level statistical features
```

Avoid using:

```text
raw complex H[t+1] - H[t]
single-frame absolute phase
unsmoothed per-subcarrier phase
```

## 7. Recommended Next Improvement

To make raw CSI estimates more stable, improve the probe itself:

```text
repeat TX0 pilot several times and average
repeat TX1 pilot several times and average
keep fixed frame grid from the first detected frame
apply CFO correction using the stronger sync RX channel
optionally add phase tracking between frames
```

The most direct next change is pilot averaging. One pilot OFDM symbol per TX is
too noisy for millisecond-level raw complex CSI stability.
