# Canopi — Canon Ultra-Low-Latency Field EVF & Recorder

Canopi is an ultra-low-latency electronic viewfinder (EVF) monitor and recording system designed for video cameras (e.g., Canon EOS series). It turns a Raspberry Pi 4, a USB HDMI capture dongle (such as UGREEN), and an iPhone OLED display into a professional field monitor with **~40–60ms glass-to-glass latency** over a direct USB cable tether.

---

## Table of Contents
- [Architecture & Tech Stack](#architecture--tech-stack)
- [Hardware Setup](#hardware-setup)
- [Software Prerequisites & USB Tethering](#software-prerequisites--usb-tethering)
- [Quick Start](#quick-start)
  - [Option A: Docker Compose (Recommended)](#option-a-docker-compose-recommended)
  - [Option B: Bare Metal Standalone](#option-b-bare-metal-standalone)
- [User Guide & Features](#user-guide--features)
  - [1. Live Viewfinder Monitor](#1-live-viewfinder-monitor)
  - [2. Stream Synchronization (SYNC Button)](#2-stream-synchronization-sync-button)
  - [3. Settings (Stream Presets & Download Format)](#3-settings-stream-presets--download-format)
  - [4. High-Framerate Recording (FHD 60p)](#4-high-framerate-recording-fhd-60p)
  - [5. Recordings Gallery](#5-recordings-gallery)
  - [6. Saving Directly to iPhone Photos / Camera Roll](#6-saving-directly-to-iphone-photos--camera-roll)
  - [7. Deleting Clips](#7-deleting-clips)
- [Automated Failsafes & Reliability](#automated-failsafes--reliability)
  - [Phone Death & Cable Disconnect Watchdog](#phone-death--cable-disconnect-watchdog)
  - [App-Switch & Screen-Lock Auto-Save](#app-switch--screen-lock-auto-save)
  - [Sequential Transcode Queue (No CPU Starvation)](#sequential-transcode-queue-no-cpu-starvation)
  - [Power-Loss & Crash Recovery Loop](#power-loss--crash-recovery-loop)
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
                                  ├─► Heartbeat Watchdog & Recovery Worker
                                  └─► Settings & Gallery Storage (/app/recordings)
```

- **Video Capture & Streaming:** `go2rtc` acts as the ultra-fast streaming engine, delivering uncompressed V4L2 MJPEG frames directly from `/dev/video0` over HTTP without re-encoding overhead.
- **Backend Controller:** Python (`server.py`) manages real-time stream dumps, background FFmpeg transcoding, progress tracking, client heartbeats, and storage management.
- **Frontend Client:** Pure HTML/CSS/JavaScript PWA (`web/index.html`) optimized for Mobile Safari and OLED displays, featuring zero third-party dependencies, hardware decoding, and native iOS Web Share API integration.

---

## Hardware Setup

1. **Camera Connection:** Connect your camera's clean HDMI output to the UGREEN USB HDMI Video Capture Card (MacroSilicon MS2109 or MS2130 chip).
2. **Raspberry Pi 4 Input:** Plug the capture card into one of the blue **USB 3.0** ports on the Raspberry Pi 4 (assigned to `/dev/video0`).
3. **iPhone Tether:** Connect your iPhone to one of the Pi 4's **USB 2.0** ports using a standard Lightning or USB-C cable.

---

## Software Prerequisites & USB Tethering

### 1. Install Host Dependencies
On the Raspberry Pi 4 host operating system:
```bash
sudo apt update
sudo apt install -y usbmuxd libimobiledevice6 libimobiledevice-utils v4l-utils
```

### 2. Enable iPhone USB Ethernet (`ipheth`)
1. On your iPhone, navigate to **Settings > Personal Hotspot** and turn it **ON**.
2. Select **USB Only** when prompted on the iPhone.
3. Tap **Trust This Computer** when prompted and enter your passcode.
4. Verify on the Pi that an Ethernet interface (typically `usb0` or `eth1`) is assigned an IP in the `172.20.10.x` subnet:
   ```bash
   ip a show dev usb0
   # The Pi will typically receive 172.20.10.2, with the iPhone at 172.20.10.1
   ```

---

## Quick Start

### Option A: Docker Compose (Recommended)

Canopi is fully containerized with Alpine Linux, bundling `go2rtc`, Python 3, FFmpeg, and V4L2 utilities:

```bash
# Clone the repository
git clone https://github.com/your-repo/canopi.git
cd canopi

# Start the services in detached mode
docker compose up -d
```

To view live service logs:
```bash
docker compose logs -f
```

To stop:
```bash
docker compose down
```

### Option B: Bare Metal Standalone

If you prefer to run directly on the host without Docker:
```bash
chmod +x run.sh
./run.sh
```

---

## User Guide & Features

Open Safari on your iPhone and navigate to:
```
http://172.20.10.2:8000
```
> **Tip:** In Safari, tap the **Share** button and select **Add to Home Screen** to launch Canopi as a borderless, full-screen OLED monitor.

### 1. Live Viewfinder Monitor
- Displays the camera stream with near-zero latency (~40–60ms).
- Top-left HUD displays connection health (`USB LINK • LIVE`).
- Screen wake lock automatically keeps your iPhone display awake while monitoring.

### 2. Stream Synchronization (SYNC Button)
- Located at the **bottom-left** corner of the EVF screen.
- If network jitter or browser backgrounding ever causes stream delay, tapping **SYNC** immediately halts lingering socket handles, flushes browser buffers, and performs an instant fresh reconnection to the camera feed.

### 3. Settings (Stream Presets & Download Format)
- Tap the **Gear Icon** button below the HUD in the top-left corner to open settings:
  - **Stream Settings:** Displays active capture mode:
    - `1080p 60fps (Native Full HD)`: Direct 1:1 hardware pass-through from the HDMI capture card for maximum sharpness with zero software scaling latency or CPU load.
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
- FFmpeg runs under `nice +19` with a strict limit of `-threads 2`, ensuring `go2rtc` and live capture maintain dedicated CPU cores on the Raspberry Pi 4 at all times.

### Power-Loss & Crash Recovery Loop
- If the Raspberry Pi 4 loses power unexpectedly or the container restarts mid-transcode, a background reconciliation worker scans the storage directory every **5 seconds**.
- Detects any `.mkv` files that lack a matching `.mp4` file.
- Purges any truncated, corrupt `.tmp.mp4` leftover files.
- Automatically feeds the orphaned raw clips back into the transcoding queue to finish encoding without manual intervention.

---

## Backend API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/record/status` | `GET` | Returns active recording status, elapsed time, and current filename. |
| `/api/record/start` | `GET` | Starts raw MKV recording from `go2rtc`. |
| `/api/record/stop` | `GET / POST` | Finalizes active recording and enqueues MP4 transcode. |
| `/api/record/ping` | `GET` | Heartbeat endpoint for the client watchdog. |
| `/api/record/delete?id={id}` | `GET / POST` | Deletes raw MKV, MP4, temp files, and thumbnail from disk. |
| `/api/gallery` | `GET` | Lists all recordings with thumbnail paths, sizes, readiness, and encoding progress. |
| `/api/settings` | `GET` | Retrieves current settings and supported stream presets. |
| `/api/settings` | `POST` | Updates stream presets and download formats. |
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
├── run.sh                   # Standalone host launcher script
├── server.py                # Python controller, API server, and recovery worker
├── recordings/              # Mounted volume for recorded media assets
│   ├── thumbs/              # Generated JPEG card thumbnails
│   ├── mp4/                 # Transcoded web-safe MP4 videos
│   └── settings.json        # Persistent user settings configuration
└── web/
    └── index.html           # Full-screen Mobile Safari PWA client interface
```
