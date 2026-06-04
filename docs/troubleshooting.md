# Troubleshooting

## TX Underflow

Symptoms:

```text
U
usrp_sink :error: underflows occurred
cmd time errors occurred
```

Typical causes:

```text
VM USB passthrough jitter
USB2 instead of USB3
USB hub or overloaded controller
CPU scheduling pressure
multiple TX processes using the same B210
```

Use native Ubuntu and direct USB3 first.

## RX Overflow

Symptoms:

```text
O
usrp_source :error: overflows occurred
```

The realtime monitor is heavier than raw capture because it runs GUI plotting
and CSI preview. Use the monitor for tuning, close it, then run raw IQ capture.

## CSI Not Stable

Check in this order:

```text
TX log has no underflows
RX log has no overflows
raw IQ contains stable frame detections
LTF timing metric is high
within-frame pilot repeats are consistent
adjacent-frame amplitude and phase are stable
```

If repeated pilots inside the same frame are inconsistent, the problem is in
capture integrity, timing/CFO/SFO/CPE correction, or frame design.
