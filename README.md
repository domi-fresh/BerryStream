# Canopi — Canon Ultra-Low-Latency Field EVF & Recorder

Canopi is an ultra-low-latency electronic viewfinder (EVF) monitor and recording system designed for digital cameras (e.g., Canon EOS R/M series). It turns a Raspberry Pi, a USB HDMI capture dongle (such as UGREEN), and an iPhone OLED display into a professional field monitor with **~40–60ms glass-to-glass latency** over a direct USB cable tether.

---

## Highlights & Hardware Compatibility
- **One-Command Setup:** Clone the repo and run `cd canopi && docker compose up -d` — everything is configured, installed, and launched automatically in Docker.
- **Auto-Start on Boot:** Preconfigures a systemd service so Canopi starts automatically on every boot.
- **Cross-Compatible Hardware:** Out of the box support for:
  - **Raspberry Pi 5** (*recommended*)
  - **Raspberry Pi 4** (*recommended*)
  - **Raspberry Pi 3** (Model B / B+)
  - **Raspberry Pi Zero 2W** (*I/O seems to be a bottleneck though -> **720p60** limition for smooth streaming*)
- **Automated Pi Zero 2W Tuning:** detects Pi Zero 2W board and applies a permanent, safe moderate overclock (`arm_freq=1200`, `gpu_freq=500`, `over_voltage=2`) to boost it to almost Pi 3 performance.
- **100% Docker-Based:** Clean containerized with zero host pollution.
- **Direct 1:1 Hardware Pass-Through:** Native 1080p60 V4L2 MJPEG stream with near-zero latency.

---

## Table of Contents
- [Canopi — Canon Ultra-Low-Latency Field EVF \& Recorder](#canopi--canon-ultra-low-latency-field-evf--recorder)
  - [Highlights \& Hardware Compatibility](#highlights--hardware-compatibility)
  - [Table of Contents](#table-of-contents)
  - [Architecture \& Tech Stack](#architecture--tech-stack)
  - [Hardware Setup](#hardware-setup)
  - [Quick Start (One-Command Setup)](#quick-start-one-command-setup)
    - [What `docker compose up -d` Automatically Does:](#what-runsh-automatically-does)
  - [iPhone USB Tethering Setup](#iphone-usb-tethering-setup)
  - [User Guide \& Features](#user-guide--features)
    - [1. Live Viewfinder Monitor](#1-live-viewfinder-monitor)
    - [2. Stream Synchronization (SYNC Button)](#2-stream-synchronization-sync-button)
    - [3. Settings (Stream Mode \& Download Format)](#3-settings-stream-mode--download-format)
    - [4. High-Framerate Recording (FHD 60p)](#4-high-framerate-recording-fhd-60p)
    - [5. Recordings Gallery](#5-recordings-gallery)
    - [6. Saving Directly to iPhone Photos / Camera Roll](#6-saving-directly-to-iphone-photos--camera-roll)
    - [7. Deleting Clips](#7-deleting-clips)
  - [Automated Failsafes \& Reliability](#automated-failsafes--reliability)
    - [Phone Death \& Cable Disconnect Watchdog](#phone-death--cable-disconnect-watchdog)
    - [App-Switch \& Screen-Lock Auto-Save](#app-switch--screen-lock-auto-save)
    - [Sequential Transcode Queue (No CPU Starvation)](#sequential-transcode-queue-no-cpu-starvation)
    - [Power-Loss \& Crash Recovery Loop](#power-loss--crash-recovery-loop)
  - [Docker Management Commands](#docker-management-commands)
  - [Backend API Reference](#backend-api-reference)
  - [Project Structure](#project-structure)

---

## Architecture & Tech Stack

```
[ Camera HDMI ]
      │
      ▼ (Clean HDMI 1080p60)
[ UGREEN USB Capture Card ] (/dev/video0)
      │
      ▼ (V4L2 Hardware MJPEG)
[ go2rtc Core (:1984) ] ──────────┐
      │ (Raw MJPEG Stream)        │ (Zero-copy MJPEG dump)
      ▼                           ▼
[ Mobile Safari PWA ]      [ Canopi Python Controller (:8000) ]
(iPhone 12 OLED Display)          │
                                  ├─► Background FFmpeg Transcoder (libx264, 60fps)
                                  ├─► Sequential FIFO Transcode Queue
                                  ├─► Heartbeat Watchdog & 5s Power Recovery
                                  └─► Settings & Gallery Storage (/app/recordings)
```

- **Streaming Core (`go2rtc`):** High-performance Go streaming core running in Docker, passing raw V4L2 MJPEG packets directly from `/dev/video0` over HTTP with zero transcoding delay.
- **Controller & API Server (`server.py`):** Multi-threaded Python server managing real-time MKV dumps, background sequential transcoding, live progress tracking, watchdog heartbeats, and settings storage.
- **Client App (`web/index.html`):** Pure HTML5/CSS/JavaScript PWA optimized for Mobile Safari and OLED displays, featuring zero third-party dependencies, hardware decoding, and native iOS Web Share API integration.

---

## Hardware Setup

1. **Camera Connection:** Connect your camera's clean HDMI output to the UGREEN USB HDMI Video Capture Card (MacroSilicon MS2109 or MS2130 chip).
2. **Raspberry Pi Input:**
   - **Pi 4 / Pi 5:** Plug the capture card into a blue **USB 3.0** port (`/dev/video0`).
   - **Pi 3 / Pi Zero 2W:** Install a micro-USB splitter. Plug the capture card into a USB port.
3. **iPhone Tether:** Connect your iPhone to one of the Pi's available USB ports using a standard Lightning or USB-C cable.

---

## Quick Start (One-Command Setup)

Setting up Canopi on a freshly installed Raspberry Pi OS Lite Bookworm (64bit) (Debian/Ubuntu) requires the following commands:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git -y
git clone https://github.com/domi-fresh/canopi.git
cd canopi
docker compose up -d # This may take a couple of minutes due to initial setup
# docker logs -f canopi # if one wants to inspect the docker logs
```

### What `docker compose up -d` Automatically Does:
1. Detects your hardware architecture (`arm64`, `armv7l`, or `x86_64`).
2. Installs required host dependencies (`usbmuxd`, `libimobiledevice`, `v4l-utils`).
3. Installs Docker and the Docker Compose plugin (if not already installed).
4. Enables the Docker daemon to start automatically on system boot.
5. Configures and enables a systemd service (`canopi.service`) ensuring the container auto-launches on boot.
6. Automatically applies a safe moderate overclock (`1.2GHz CPU`, `500MHz GPU`, `over_voltage=2`) in `config.txt` if a Raspberry Pi Zero 2W is detected.
7. Builds the container and starts Canopi in detached mode.

---

## iPhone USB Tethering Setup

1. On your iPhone, navigate to **Settings > Personal Hotspot** and turn it **ON**.
2. Select **USB Only** when prompted on the iPhone.
3. Tap **Trust This Computer** when prompted and enter your iPhone passcode.
4. Open Mobile Safari on your iPhone and go to:
   ```
   http://172.20.10.2:8000
   ```
   
> **Tip:** In Safari, tap the **Share** button and select **Add to Home Screen** to launch Canopi as a borderless, full-screen OLED monitor and use it like an app.

---

## User Guide & Features

### 1. Live Viewfinder Monitor
- Displays the camera stream with near-zero latency (~40–60ms).
- Top-left HUD displays connection health (`USB LINK • LIVE`).
- Automatic screen wake lock keeps your iPhone OLED display illuminated while monitoring.

### 2. Stream Synchronization (SYNC Button)
- Located in the **bottom-left** corner of the EVF screen.
- If network jitter or browser backgrounding ever causes stream delay, tapping **SYNC** immediately halts lingering socket handles, flushes browser buffers, and performs an instant fresh reconnection to the live camera feed.

### 3. Settings (Stream Mode, Transcoding & Download Format)
- Tap the **Gear Icon** button below the HUD in the top-left corner to open settings:
  - **Stream Settings:** Choose between live capture presets dynamically:
    - `720p 60fps` *(Default / Recommended for Pi Zero 2W: Smooth 60p with zero USB bus congestion)*.
    - `720p 30fps` *(Ultra-low bandwidth and maximum power saving)*.
    - `1080p 30fps` *(Full HD cinema quality with reduced USB bandwidth)*.
    - `1080p 60fps` *(Native Full HD for high-speed USB architectures like Pi 4 / Pi 5)*.
  - **Transcoding (Battery Saving Mode):**
    - `Automatic Transcoding (ON / Default)`: Transcodes raw MKVs to web-optimized MP4 immediately after each take completes.
    - `Field Mode (OFF)`: Saves only the raw `.mkv` files without starting background CPU-intensive FFmpeg transcoding, maximizing battery life while in the field. Once turned back ON (e.g. at home plugged into the wall), all orphaned MKVs are automatically detected and transcoded sequentially.
  - **Download Settings:** Choose between download and gallery display formats:
    - `MP4` *(Default: Transcoded H.264, compact file size, native iPhone Photos compatibility)*.
    - `MKV` *(Raw uncompressed MJPEG camera dump for maximum archival quality)*.

### 4. High-Framerate Recording (FHD 60p)
- Tap the red **REC** button in the top-right corner to start recording.
- The button glows red and displays the live elapsed recording time (`MM:SS`).
- Tap **REC** again to stop. The raw capture is finalized, and a low-priority background worker transcodes the video to web-safe MP4 matching the stream framerate (60 fps).

### 5. Recordings Gallery
- Tap **GALLERY** in the bottom-right corner to open the recordings view.
- **Responsive Layout:** Automatically displays **2 columns in portrait** mode and **3 columns in landscape** mode.
- **Reverse Chronological Order:** The newest recorded clips always appear at the top.
- **Real-Time Progress & ETA:** Clips currently encoding display an animated green progress bar with live completion percentage and remaining time estimate (e.g., `Encoding (45%) • 12s left`).
- **Sequential Queuing:** Clips waiting in line behind an active transcode display `Encoding • Queued...` with zero CPU overload.
- **In-Browser Playback:** Tap **PLAY** on any ready clip to view it in an instant full-screen playback modal.

### 6. Saving Directly to iPhone Photos / Camera Roll
- Tap **SAVE** on any clip card.
- Instead of downloading to generic files, Canopi invokes the native **iOS Share Sheet**.
- The share sheet presents **Save Video**, which saves the `.mp4` file directly into your iPhone's **Apple Photos app (Camera Roll)**.
- Once saved or dismissed, the sheet slides down and you remain seamlessly in the gallery.

### 7. Deleting Clips
- Tap the red **Trashcan Icon** button on any card.
- A native confirmation alert appears showing the clip filename.
- Confirming permanently removes the `.mkv`, transcoded `.mp4`, and thumbnail assets from the Pi's storage.

---

## Automated Failsafes & Reliability

Canopi is built specifically for field operation where battery depletion, cables being snagged, and power cuts happen:

### Phone Death & Cable Disconnect Watchdog
- While recording, the iPhone transmits a lightweight heartbeat ping to `/api/record/ping` every 2 seconds.
- The backend runs a continuous watchdog thread. If the iPhone battery dies, the phone crashes, or the USB cable is unplugged for **more than 5 seconds**, the watchdog automatically:
  1. Issues `SIGINT` to FFmpeg to cleanly close the `.mkv` container headers without file corruption.
  2. Enqueues the clip for thumbnail generation and MP4 transcoding.
  3. Guarantees that all footage up to the disconnection is safely preserved.

### App-Switch & Screen-Lock Auto-Save
- If you accidentally swipe to another iOS app, minimize Safari, or lock your iPhone while recording, the client listens to `visibilitychange` (`hidden`) and `pagehide` events.
- An instant keepalive beacon (`navigator.sendBeacon('/api/record/stop')`) finalizes the recording immediately.
- Tapping **GALLERY** while recording also automatically finalizes and queues the current take.

### Sequential Transcode Queue (No CPU Starvation)
- Transcode tasks are queued into a single-consumer FIFO queue (`queue.Queue`).
- Taking multiple takes back-to-back processes clips strictly **one at a time**.
- FFmpeg runs under `nice +19` with a strict limit of `-threads 2`, ensuring `go2rtc` and live capture maintain dedicated CPU cores on the Raspberry Pi at all times.

### Power-Loss & Crash Recovery Loop
- If the Raspberry Pi loses power unexpectedly or the container restarts mid-transcode, a background reconciliation worker scans the storage directory every **5 seconds**.
- Detects any `.mkv` files that lack a matching `.mp4` file.
- Purges any truncated, corrupt `.tmp.mp4` leftover files.
- Automatically feeds the orphaned raw clips back into the transcoding queue to finish encoding without manual intervention.

---

## Docker Management Commands

Canopi runs as a native Docker Compose project:

```bash
# View live container logs
docker compose logs -f

# Check container status
docker compose ps

# Restart services
docker compose restart

# Stop services
docker compose down

# Rebuild container after code changes
docker compose up -d --build
```

---

## Backend API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/record/status` | `GET` | Returns active recording status, elapsed time, and current filename. |
| `/api/record/start` | `GET` | Starts raw MKV recording from `go2rtc`. |
| `/api/record/stop` | `GET / POST` | Finalizes active recording and conditionally enqueues MP4 transcode if auto-transcode is enabled. |
| `/api/record/ping` | `GET` | Heartbeat endpoint for the client watchdog. |
| `/api/record/delete?id={id}` | `GET / POST` | Deletes raw MKV, MP4, temp files, and thumbnail from disk. |
| `/api/gallery` | `GET` | Lists all recordings with thumbnail paths, sizes, readiness, and encoding progress. |
| `/api/settings` | `GET` | Retrieves current settings (`stream_preset`, `download_format`, `auto_transcode`) and supported stream presets. |
| `/api/settings` | `POST` | Updates stream presets, download formats, and toggles automatic transcoding. |
| `/api/transcode` | `GET / POST` | Manually enqueues an individual recording (`?id={id}`) or all orphaned MKVs for transcoding. |
| `/media/thumb/{file}` | `GET` | Serves preview JPEG thumbnails. |
| `/media/mp4/{file}` | `GET` | Streams MP4 video with HTTP 206 Byte-Range support for Safari. |
| `/media/raw/{file}` | `GET` | Downloads original raw MKV files. |

---

## Project Structure

```
canopi/
├── docker-compose.yml       # Production Docker deployment definition
├── Dockerfile               # Alpine-based container image with go2rtc & FFmpeg
├── entrypoint.sh            # Container init launching go2rtc and Python server
├── go2rtc.yaml              # Default V4L2 stream configuration for go2rtc
├── README.md                # Comprehensive project documentation
├── run.sh                   # All-in-one automated installer & boot launcher
├── server.py                # Python controller, API server, and recovery worker
├── recordings/              # Mounted volume for recorded media assets
│   ├── thumbs/              # Generated JPEG card thumbnails
│   ├── mp4/                 # Transcoded web-safe MP4 videos
│   └── settings.json        # Persistent user settings configuration
└── web/
    └── index.html           # Full-screen Mobile Safari PWA client interface
```
