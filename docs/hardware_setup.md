# Hardware Setup

Recommended setup:

```text
OS: native Ubuntu 22.04 or 24.04
SDR: two USRP B210 devices
TX device: 2-channel TX
RX device: 2-channel RX
antennas: matched 2.4/5 GHz antennas on TX/RX ports
USB: direct USB3 connection, no hub if possible
```

Avoid running the USRP streaming experiment inside a VM. USB passthrough jitter
can cause TX underflows, RX overflows, and UHD command time errors.

## Ubuntu Packages

```bash
sudo apt update
sudo apt install -y git cmake build-essential python3-pip
sudo apt install -y python3-numpy python3-scipy python3-matplotlib
sudo apt install -y gnuradio uhd-host libuhd-dev python3-uhd
sudo uhd_images_downloader
```

## Device Checks

```bash
uhd_find_devices
uhd_usrp_probe --args "serial=326F493"
uhd_usrp_probe --args "serial=3271260"
```

Update `config/devices.local.json` with the serials for your setup.

## Frequency Selection

Use `01_spectrum_survey/rx_spectrum_gui.py` before transmitting. The default
configuration uses 2484 MHz because it was clean in the current test
environment. Always verify your local spectrum and comply with local RF rules.
