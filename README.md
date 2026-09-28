# Canon EVF Ultra-Low-Latency Pi Monitor

A low-latency field viewfinder using a Raspberry Pi 4, HDMI capture card (UGREEN), and an iPhone 12 OLED display over a direct USB Lightning tether.

## Architecture
- **Video Input:** Canon HDMI -> UGREEN USB Capture (`/dev/video0`)
- **Streaming Engine:** `go2rtc` (direct V4L2 MJPEG pass-through, ~40-60ms glass-to-glass latency)
- **Display Client:** Mobile Safari PWA running on iPhone via `ipheth` USB Ethernet (`172.20.10.2`)

## Requirements
```bash
sudo apt update
sudo apt install -y usbmuxd libimobiledevice6 libimobiledevice-utils python3 v4l-utils
