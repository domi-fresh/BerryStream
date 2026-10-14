import os
import re
import signal
import subprocess
import time
import json
import threading
import shutil
import urllib.request
import urllib.parse
from queue import Queue
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

REC_DIR = os.environ.get("REC_DIR", "/app/recordings")
THUMB_DIR = os.path.join(REC_DIR, "thumbs")
MP4_DIR = os.path.join(REC_DIR, "mp4")
SETTINGS_FILE = os.path.join(REC_DIR, "settings.json")
WEB_DIR = os.environ.get("WEB_DIR", "/app/web" if os.path.exists("/app/web") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))
GO2RTC_CONFIG = os.environ.get("GO2RTC_CONFIG", "/app/go2rtc.yaml")

os.makedirs(REC_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(MP4_DIR, exist_ok=True)

STREAM_PRESETS = {
    "720p60":  {"width": 1280, "height": 720,  "fps": 60, "label": "720p 60fps (Recommended for Pi Zero 2W)"},
    "720p30":  {"width": 1280, "height": 720,  "fps": 30, "label": "720p 30fps (Ultra-Low Bandwidth)"},
    "1080p30": {"width": 1920, "height": 1080, "fps": 30, "label": "1080p 30fps (Full HD Cinema)"},
    "1080p60": {"width": 1920, "height": 1080, "fps": 60, "label": "1080p 60fps (Native Full HD)"},
}

ONBOARD_CAMERAS = {
    "v2": {
        "model": "v2",
        "name": "Raspberry Pi Camera Module v2",
        "sensor": "Sony IMX219 (8MP)",
        "default_preset": "720p60",
        "presets": {
            "1080p30": {"width": 1920, "height": 1080, "fps": 30, "label": "1080p 30fps", "sub": "Full HD Video (16:9)"},
            "720p60":  {"width": 1280, "height": 720,  "fps": 60, "label": "720p 60fps", "sub": "Smooth Action (16:9)"},
            "720p30":  {"width": 1280, "height": 720,  "fps": 30, "label": "720p 30fps", "sub": "Standard Framerate (16:9)"},
            "1640x1232_30": {"width": 1640, "height": 1232, "fps": 30, "label": "1640x1232 30fps", "sub": "Full Sensor FoV (4:3 Binned)"},
            "640x480_60":   {"width": 640,  "height": 480,  "fps": 60, "label": "VGA 60fps", "sub": "High Speed (4:3)"},
            "640x480_90":   {"width": 640,  "height": 480,  "fps": 90, "label": "VGA 90fps", "sub": "Ultra High Speed (4:3)"},
        }
    },
    "v1.3": {
        "model": "v1.3",
        "name": "Raspberry Pi Camera Module v1.3",
        "sensor": "OmniVision OV5647 (5MP)",
        "default_preset": "720p60",
        "presets": {
            "1080p30": {"width": 1920, "height": 1080, "fps": 30, "label": "1080p 30fps", "sub": "Full HD Video (16:9)"},
            "720p60":  {"width": 1280, "height": 720,  "fps": 60, "label": "720p 60fps", "sub": "Smooth 60p, Full FoV (16:9)"},
            "720p30":  {"width": 1280, "height": 720,  "fps": 30, "label": "720p 30fps", "sub": "Standard 30p, Full FoV (16:9)"},
            "640x480_60": {"width": 640,  "height": 480,  "fps": 60, "label": "VGA 60fps", "sub": "High Speed (4:3)"},
            "640x480_90": {"width": 640,  "height": 480,  "fps": 90, "label": "VGA 90fps", "sub": "High Speed (4:3)"},
        }
    }
}

DEFAULT_SETTINGS = {
    "stream_preset": "720p60",
    "download_format": "mp4",  # 'mp4' or 'mkv'
    "auto_transcode": True,    # True = transcode immediately; False = leave as raw MKV in field
    "show_storage": True,      # True = show storage info badge on live view
    "onboard_record": False,   # True = simultaneously record onboard Pi camera
    "onboard_preset": "720p60" # Default resolution/fps preset for onboard camera
}

_onboard_cam_cache = None
_onboard_cam_cache_time = 0

def detect_onboard_camera(force_refresh=False):
    """Detects whether an onboard Raspberry Pi camera module (v1.3 or v2) is connected.
    
    Checks in priority order:
    1. ONBOARD_CAMERA_MODEL environment variable (useful for testing and overrides)
    2. /sys/bus/i2c/drivers/ (kernel driver bindings for imx219 or ov5647)
    3. /sys/bus/i2c/devices/ (device names in sysfs)
    4. /proc/device-tree or /sys/firmware/devicetree/base
    5. rpicam-hello / libcamera-hello CLI query (if available)
    6. v4l2-ctl --list-devices / sysfs video4linux
    """
    global _onboard_cam_cache, _onboard_cam_cache_time
    now = time.time()
    if not force_refresh and _onboard_cam_cache is not None and (now - _onboard_cam_cache_time < 5.0):
        return _onboard_cam_cache

    detected_key = None

    # 1. Environment variable override
    env_override = os.environ.get("ONBOARD_CAMERA_MODEL", "").strip().lower()
    if env_override:
        if env_override in ("v2", "imx219", "2"):
            detected_key = "v2"
        elif env_override in ("v1.3", "v1", "ov5647", "1.3", "1"):
            detected_key = "v1.3"
        elif env_override in ("none", "false", "0", "off", "disabled"):
            detected_key = None

    # 2. Sysfs I2C driver directory checks
    if detected_key is None and not env_override:
        try:
            imx219_driver = "/sys/bus/i2c/drivers/imx219"
            if os.path.isdir(imx219_driver):
                entries = [e for e in os.listdir(imx219_driver) if re.match(r"^\d+-[0-9a-fA-F]+$", e)]
                if entries:
                    detected_key = "v2"

            if detected_key is None:
                ov5647_driver = "/sys/bus/i2c/drivers/ov5647"
                if os.path.isdir(ov5647_driver):
                    entries = [e for e in os.listdir(ov5647_driver) if re.match(r"^\d+-[0-9a-fA-F]+$", e)]
                    if entries:
                        detected_key = "v1.3"
        except Exception:
            pass

    # 3. Sysfs I2C devices checks (/sys/bus/i2c/devices/*/name)
    if detected_key is None and not env_override:
        try:
            i2c_devs = "/sys/bus/i2c/devices"
            if os.path.isdir(i2c_devs):
                for dev in os.listdir(i2c_devs):
                    name_file = os.path.join(i2c_devs, dev, "name")
                    if os.path.isfile(name_file):
                        with open(name_file, "r") as nf:
                            dev_name = nf.read().strip().lower()
                            if "imx219" in dev_name:
                                detected_key = "v2"
                                break
                            elif "ov5647" in dev_name:
                                detected_key = "v1.3"
                                break
        except Exception:
            pass

    # 4. Device tree check
    if detected_key is None and not env_override:
        try:
            dt_base = "/proc/device-tree" if os.path.isdir("/proc/device-tree") else "/sys/firmware/devicetree/base"
            if os.path.isdir(dt_base):
                for root, dirs, files in os.walk(dt_base):
                    low_root = root.lower()
                    if "imx219" in low_root:
                        detected_key = "v2"
                        break
                    elif "ov5647" in low_root:
                        detected_key = "v1.3"
                        break
        except Exception:
            pass

    # 5. rpicam-hello / libcamera-hello CLI checks
    if detected_key is None and not env_override:
        for cmd_name in ("rpicam-hello", "libcamera-hello"):
            if shutil.which(cmd_name):
                try:
                    res = subprocess.run([cmd_name, "--list-cameras"], capture_output=True, text=True, timeout=1.5)
                    out = res.stdout.lower() + res.stderr.lower()
                    if "imx219" in out:
                        detected_key = "v2"
                        break
                    elif "ov5647" in out:
                        detected_key = "v1.3"
                        break
                except Exception:
                    pass

    # 6. v4l2-ctl / video4linux name checks
    if detected_key is None and not env_override:
        try:
            v4l_dir = "/sys/class/video4linux"
            if os.path.isdir(v4l_dir):
                for node in os.listdir(v4l_dir):
                    name_path = os.path.join(v4l_dir, node, "name")
                    if os.path.isfile(name_path):
                        with open(name_path, "r") as nf:
                            content = nf.read().strip().lower()
                            if "imx219" in content:
                                detected_key = "v2"
                                break
                            elif "ov5647" in content:
                                detected_key = "v1.3"
                                break
        except Exception:
            pass

    if detected_key and detected_key in ONBOARD_CAMERAS:
        cam_meta = ONBOARD_CAMERAS[detected_key]
        result = {
            "detected": True,
            "model": detected_key,
            "name": cam_meta["name"],
            "sensor": cam_meta["sensor"],
            "default_preset": cam_meta["default_preset"],
            "presets": cam_meta["presets"]
        }
    else:
        result = {
            "detected": False,
            "model": None,
            "name": None,
            "sensor": None,
            "default_preset": None,
            "presets": {}
        }

    _onboard_cam_cache = result
    _onboard_cam_cache_time = now
    return result

def get_onboard_camera_info():
    return detect_onboard_camera()

def build_onboard_record_command(filepath, preset_key=None, camera_info=None):
    """Builds the command to capture video from the onboard camera module."""
    if camera_info is None:
        camera_info = detect_onboard_camera()

    presets = camera_info.get("presets", {})
    preset = presets.get(preset_key)
    if not preset:
        default_key = camera_info.get("default_preset")
        preset = presets.get(default_key) or next(iter(presets.values()), {"width": 1280, "height": 720, "fps": 60})

    width = preset["width"]
    height = preset["height"]
    fps = preset["fps"]

    # Mock command for test environments or desktop
    mock_cmd = os.environ.get("MOCK_ONBOARD_RECORD_CMD")
    if mock_cmd:
        return [part.replace("{filepath}", filepath) for part in mock_cmd.split()]

    # 1. Prefer rpicam-vid (Raspberry Pi OS Bookworm)
    if shutil.which("rpicam-vid"):
        return [
            "rpicam-vid", "-n", "-t", "0",
            "--width", str(width),
            "--height", str(height),
            "--framerate", str(fps),
            "--codec", "libav",
            "-o", filepath
        ]

    # 2. Prefer libcamera-vid (Raspberry Pi OS Bullseye)
    if shutil.which("libcamera-vid"):
        return [
            "libcamera-vid", "-n", "-t", "0",
            "--width", str(width),
            "--height", str(height),
            "--framerate", str(fps),
            "--codec", "libav",
            "-o", filepath
        ]

    # 3. Check V4L2 device if available
    onboard_dev = os.environ.get("ONBOARD_VIDEO_DEVICE", "/dev/video1")
    if os.path.exists(onboard_dev):
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2",
            "-video_size", f"{width}x{height}",
            "-framerate", str(fps),
            "-i", onboard_dev,
            "-c:v", "copy",
            filepath
        ]

    # 4. Fallback test pattern for desktop/testing
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"testsrc=size={width}x{height}:rate={fps}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-t", "3600",
        filepath
    ]

def get_storage_info():
    """Returns disk storage usage for the recordings partition/filesystem."""
    target_dir = REC_DIR if os.path.exists(REC_DIR) else "/"
    try:
        usage = shutil.disk_usage(target_dir)
        total = usage.total
        free = usage.free
        used = usage.used
    except Exception:
        total = 0
        free = 0
        used = 0

    total_gb = round(total / (1024 ** 3), 1) if total else 0
    free_gb = round(free / (1024 ** 3), 1) if total else 0
    used_gb = round(used / (1024 ** 3), 1) if total else 0
    pct_used = round((used / total) * 100, 1) if total else 0
    pct_free = round((free / total) * 100, 1) if total else 0

    if free >= 10 * (1024 ** 3):
        free_short = f"{free / (1024 ** 3):.0f}G"
        free_human = f"{free / (1024 ** 3):.1f} GB"
    elif free >= 1024 ** 3:
        free_short = f"{free / (1024 ** 3):.1f}G"
        free_human = f"{free / (1024 ** 3):.1f} GB"
    elif free >= 1024 ** 2:
        free_short = f"{free / (1024 ** 2):.0f}M"
        free_human = f"{free / (1024 ** 2):.0f} MB"
    else:
        free_short = f"{free / 1024:.0f}K"
        free_human = f"{free / 1024:.0f} KB"

    return {
        "total": total,
        "used": used,
        "free": free,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent_used": pct_used,
        "percent_free": pct_free,
        "free_human": free_human,
        "free_short": free_short
    }

def is_camera_connected():
    """Checks whether the video capture source device is connected to the Pi."""
    device = os.environ.get("VIDEO_DEVICE", "/dev/video0")
    return os.path.exists(device)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)

def save_settings(new_settings):
    settings = load_settings()
    settings.update(new_settings)
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass
    return settings

GO2RTC_CONFIG = "/app/go2rtc.yaml"

def apply_stream_to_go2rtc(preset_key):
    global current_process
    if current_process and current_process.poll() is None:
        print("Cannot switch stream preset while recording is in progress")
        return False

    preset = STREAM_PRESETS.get(preset_key, STREAM_PRESETS["720p60"])
    config_content = f"""api:
  listen: ":1984"

streams:
  evf:
    - v4l2:device?video=/dev/video0&input_format=mjpeg&video_size={preset['width']}x{preset['height']}&framerate={preset['fps']}
"""
    # 1. Update /app/go2rtc.yaml on disk with active preset
    try:
        with open(GO2RTC_CONFIG, "w") as f:
            f.write(config_content)
        print(f"Updated {GO2RTC_CONFIG} with preset '{preset_key}' ({preset['width']}x{preset['height']}@{preset['fps']}fps)")
    except Exception as e:
        print(f"Error writing {GO2RTC_CONFIG}: {e}")

    # 2. Tell go2rtc to restart and cleanly reload configuration and V4L2 device
    try:
        req = urllib.request.Request("http://127.0.0.1:1984/api/restart", method="POST")
        with urllib.request.urlopen(req, timeout=2.0):
            pass
        print(f"Triggered go2rtc restart for preset '{preset_key}'")
        return True
    except Exception as e:
        print(f"Failed to trigger go2rtc restart: {e}")
        return False

def init_stream_preset():
    """Startup check: ensures go2rtc.yaml matches saved preset on container boot."""
    time.sleep(1.0)
    settings = load_settings()
    preset_key = settings.get("stream_preset", "720p60")
    preset = STREAM_PRESETS.get(preset_key, STREAM_PRESETS["720p60"])
    expected_line = f"video_size={preset['width']}x{preset['height']}&framerate={preset['fps']}"

    try:
        if os.path.exists(GO2RTC_CONFIG):
            with open(GO2RTC_CONFIG, "r") as f:
                content = f.read()
            if expected_line in content:
                print(f"go2rtc.yaml already configured for preset '{preset_key}'")
                return
    except Exception:
        pass

    # If different, wait until go2rtc is ready, then update and restart
    for _ in range(15):
        try:
            req = urllib.request.Request("http://127.0.0.1:1984/api/streams")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    apply_stream_to_go2rtc(preset_key)
                    break
        except Exception:
            time.sleep(1.0)

threading.Thread(target=init_stream_preset, daemon=True).start()

current_process = None
current_raw_file = None
current_onboard_process = None
current_onboard_raw_file = None
start_time = 0
last_heartbeat_time = 0
encoding_status = {}  # { base_id: { "percent": int, "eta_text": str } }

transcode_queue = Queue()
queued_files = set()
queued_lock = threading.Lock()
manual_transcodes = set()
manual_lock = threading.Lock()

def enqueue_recording(raw_filename, manual=False):
    """Thread-safe enqueueing: prevents duplicate queue entries."""
    if manual:
        with manual_lock:
            manual_transcodes.add(raw_filename)
    with queued_lock:
        if raw_filename not in queued_files:
            queued_files.add(raw_filename)
            transcode_queue.put(raw_filename)
            print(f"Enqueued for transcoding: {raw_filename} (Queue size: {transcode_queue.qsize()})")

def enqueue_orphaned_recordings(manual=False):
    """Checks for orphaned/untranscoded MKVs and enqueues them for sequential processing."""
    if not os.path.exists(REC_DIR):
        return []

    enqueued = []
    try:
        for f in sorted(os.listdir(REC_DIR)):
            if f.endswith(".mkv"):
                # Skip if actively being recorded to by either EVF or onboard camera
                if f == current_raw_file or f == current_onboard_raw_file:
                    continue

                base = os.path.splitext(f)[0]
                mp4_final = os.path.join(MP4_DIR, f"{base}.mp4")
                mp4_temp = os.path.join(MP4_DIR, f"{base}.tmp.mp4")

                # If MP4 does not exist yet, enqueue for processing
                if not os.path.exists(mp4_final):
                    with queued_lock:
                        already_queued = f in queued_files
                    if not already_queued:
                        # Clean up any abandoned corrupt .tmp.mp4 left by a power loss or crash
                        if os.path.exists(mp4_temp):
                            try:
                                os.remove(mp4_temp)
                                print(f"Removed orphaned temp file: {mp4_temp}")
                            except Exception:
                                pass

                        print(f"Enqueueing orphaned recording: {f}")
                        enqueue_recording(f, manual=manual)
                        enqueued.append(f)
    except Exception as e:
        print(f"Error checking orphaned recordings: {e}")
    return enqueued

def transcode_worker_loop():
    """Sequential consumer worker: strictly processes transcodes one by one to avoid CPU starvation."""
    while True:
        raw_filename = transcode_queue.get()
        try:
            with manual_lock:
                is_manual = raw_filename in manual_transcodes
                manual_transcodes.discard(raw_filename)

            settings = load_settings()
            # If auto_transcode is OFF and not explicitly requested manually,
            # skip processing this queued item to preserve battery in the field.
            if not is_manual and not settings.get("auto_transcode", True):
                print(f"Skipping transcode for {raw_filename}: auto_transcode is OFF")
                continue

            process_recording(raw_filename)
        except Exception as e:
            print(f"Error processing {raw_filename}: {e}")
        finally:
            with queued_lock:
                queued_files.discard(raw_filename)
            transcode_queue.task_done()

# Start background sequential transcode worker
threading.Thread(target=transcode_worker_loop, daemon=True).start()

def reconciliation_worker_loop():
    """Background recovery: checks for orphaned/untranscoded MKVs every 5s and enqueues them if auto_transcode is ON."""
    while True:
        time.sleep(5.0)
        try:
            settings = load_settings()
            if settings.get("auto_transcode", True):
                enqueue_orphaned_recordings()
        except Exception as e:
            print(f"Reconciliation error: {e}")

# Start 5-second background recovery reconciliation loop
threading.Thread(target=reconciliation_worker_loop, daemon=True).start()

def set_low_priority():
    """Sets lowest process scheduling priority so background ffmpeg never starves go2rtc."""
    try:
        os.nice(19)
    except Exception:
        pass

def stop_recording_internal(reason="manual"):
    """Thread-safe recording stop & finalize: sends SIGINT and conditionally enqueues for sequential transcoding."""
    global current_process, current_raw_file, current_onboard_process, current_onboard_raw_file
    finished_files = []

    # Stop EVF recording
    if current_process and current_process.poll() is None:
        finished_evf = current_raw_file
        print(f"Stopping EVF recording ({reason}): {finished_evf}")
        current_process.send_signal(signal.SIGINT)
        try:
            current_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            current_process.kill()

        current_process = None
        current_raw_file = None
        if finished_evf:
            finished_files.append(finished_evf)

    # Stop Onboard Pi Camera recording
    if current_onboard_process and current_onboard_process.poll() is None:
        finished_onboard = current_onboard_raw_file
        print(f"Stopping Onboard recording ({reason}): {finished_onboard}")
        current_onboard_process.send_signal(signal.SIGINT)
        try:
            current_onboard_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            current_onboard_process.kill()

        current_onboard_process = None
        current_onboard_raw_file = None
        if finished_onboard:
            finished_files.append(finished_onboard)

    if finished_files:
        settings = load_settings()
        if settings.get("auto_transcode", True):
            for f in finished_files:
                enqueue_recording(f)
        else:
            for f in finished_files:
                print(f"Auto-transcode is OFF: Leaving {f} as raw MKV")
        return finished_files[0]

    return None

def recording_watchdog_loop():
    """Background watchdog: automatically saves & finalizes recording if phone dies or disconnects."""
    global current_process, current_onboard_process, last_heartbeat_time
    HEARTBEAT_TIMEOUT = 5.0
    while True:
        time.sleep(1.0)
        is_active = (current_process and current_process.poll() is None) or \
                    (current_onboard_process and current_onboard_process.poll() is None)
        if is_active:
            now = time.time()
            if last_heartbeat_time > 0 and (now - last_heartbeat_time > HEARTBEAT_TIMEOUT):
                print(f"Watchdog: Phone heartbeat lost ({now - last_heartbeat_time:.1f}s > {HEARTBEAT_TIMEOUT}s). Auto-saving recording...")
                stop_recording_internal(reason="phone_heartbeat_lost")

# Start background failsafe watchdog
threading.Thread(target=recording_watchdog_loop, daemon=True).start()

def parse_ffmpeg_time(time_str):
    try:
        parts = time_str.split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0.0

def get_file_duration(path):
    try:
        res = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ], capture_output=True, text=True, timeout=5)
        return float(res.stdout.strip())
    except Exception:
        return 0.0

def get_file_fps(path, fallback_fps=60):
    try:
        res = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ], capture_output=True, text=True, timeout=5)
        out = res.stdout.strip()
        if "/" in out:
            num, den = out.split("/", 1)
            val = float(num) / float(den)
            if val > 0:
                return int(round(val))
        elif out:
            val = float(out)
            if val > 0:
                return int(round(val))
    except Exception:
        pass
    return fallback_fps

def process_recording(raw_filename):
    """Background worker: creates thumbnail and transcodes to iOS MP4 cleanly with progress tracking."""
    raw_path = os.path.join(REC_DIR, raw_filename)
    base_name = os.path.splitext(raw_filename)[0]
    thumb_path = os.path.join(THUMB_DIR, f"{base_name}.jpg")
    mp4_temp = os.path.join(MP4_DIR, f"{base_name}.tmp.mp4")
    mp4_final = os.path.join(MP4_DIR, f"{base_name}.mp4")

    popen_kwargs = {"preexec_fn": set_low_priority} if os.name != "nt" else {}

    # 1. Generate thumbnail
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", raw_path,
        "-vframes", "1",
        "-vf", "scale=480:-1",
        thumb_path
    ], **popen_kwargs)

    # 2. Get total clip duration for progress calculation
    total_duration = get_file_duration(raw_path)

    # Determine framerate from current settings or detected onboard preset
    current_settings = load_settings()
    if raw_filename.startswith("picam_"):
        cam_info = detect_onboard_camera()
        p_key = current_settings.get("onboard_preset", "720p60")
        p_info = cam_info.get("presets", {}).get(p_key, {"fps": 60})
        fps_val = get_file_fps(raw_path, fallback_fps=p_info.get("fps", 60))
    else:
        preset = STREAM_PRESETS.get(current_settings.get("stream_preset"), STREAM_PRESETS["720p60"])
        fps_val = get_file_fps(raw_path, fallback_fps=preset["fps"])
    fps_str = str(fps_val)

    encoding_status[base_name] = {"percent": 0, "eta_text": "Starting..."}
    encode_start = time.time()

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-threads", "2",
        "-r", fps_str,
        "-i", raw_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-r", fps_str,
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        mp4_temp
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            **popen_kwargs
        )

        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time="):
                time_val = line.split("=", 1)[1]
                if time_val != "N/A":
                    curr_sec = parse_ffmpeg_time(time_val)
                    if total_duration > 0:
                        pct = min(99, max(0, int((curr_sec / total_duration) * 100)))
                    else:
                        pct = min(99, int(time.time() - encode_start))

                    elapsed = time.time() - encode_start
                    if pct > 0:
                        total_est = elapsed / (pct / 100.0)
                        eta_sec = max(0, int(total_est - elapsed))
                        eta_text = f"{eta_sec}s left" if eta_sec > 0 else "Finishing..."
                    else:
                        eta_text = "Calculating..."

                    encoding_status[base_name] = {"percent": pct, "eta_text": eta_text}

        proc.wait()
        if proc.returncode == 0 and os.path.exists(mp4_temp):
            os.rename(mp4_temp, mp4_final)
    finally:
        encoding_status.pop(base_name, None)

class BerryStreamHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        global current_process, current_raw_file, current_onboard_process, current_onboard_raw_file, start_time
        parsed = urlparse(self.path)

        if parsed.path == "/api/record/status":
            is_recording = (current_process is not None and current_process.poll() is None) or \
                           (current_onboard_process is not None and current_onboard_process.poll() is None)
            elapsed = int(time.time() - start_time) if is_recording else 0
            onboard_info = detect_onboard_camera()
            self._send_json({
                "recording": is_recording,
                "elapsed": elapsed,
                "file": current_raw_file,
                "onboard_file": current_onboard_raw_file,
                "onboard_recording": current_onboard_process is not None and current_onboard_process.poll() is None,
                "storage": get_storage_info(),
                "camera_connected": is_camera_connected(),
                "onboard_camera": onboard_info
            })

        elif parsed.path in ("/api/connection/status", "/api/camera/status"):
            onboard_info = detect_onboard_camera()
            self._send_json({
                "phone": True,
                "camera_connected": is_camera_connected(),
                "onboard_camera": onboard_info
            })

        elif parsed.path == "/api/storage":
            self._send_json(get_storage_info())

        elif parsed.path == "/api/record/start":
            is_evf_active = current_process is not None and current_process.poll() is None
            is_onboard_active = current_onboard_process is not None and current_onboard_process.poll() is None

            if not is_evf_active and not is_onboard_active:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                current_raw_file = f"evf_{timestamp}.mkv"
                filepath = os.path.join(REC_DIR, current_raw_file)

                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-i", "http://127.0.0.1:1984/api/stream.mjpeg?src=evf",
                    "-c:v", "copy",
                    filepath
                ]
                current_process = subprocess.Popen(cmd)

                # Simultaneous Onboard Camera Recording
                settings = load_settings()
                onboard_info = detect_onboard_camera()
                if settings.get("onboard_record", False) and onboard_info.get("detected"):
                    current_onboard_raw_file = f"picam_{timestamp}.mkv"
                    onboard_path = os.path.join(REC_DIR, current_onboard_raw_file)
                    onboard_cmd = build_onboard_record_command(
                        onboard_path,
                        preset_key=settings.get("onboard_preset"),
                        camera_info=onboard_info
                    )
                    try:
                        current_onboard_process = subprocess.Popen(onboard_cmd)
                    except Exception as e:
                        print(f"Failed to start onboard camera recording: {e}")
                        current_onboard_process = None
                        current_onboard_raw_file = None
                else:
                    current_onboard_process = None
                    current_onboard_raw_file = None

                start_time = time.time()
                last_heartbeat_time = time.time()
                self._send_json({
                    "status": "started",
                    "file": current_raw_file,
                    "onboard_file": current_onboard_raw_file
                })
            else:
                self._send_json({
                    "status": "already_recording",
                    "file": current_raw_file,
                    "onboard_file": current_onboard_raw_file
                })

        elif parsed.path == "/api/record/ping":
            last_heartbeat_time = time.time()
            self._send_json({"status": "pong"})

        elif parsed.path == "/api/record/stop":
            finished_file = stop_recording_internal(reason="user_stop")
            if finished_file:
                settings = load_settings()
                is_auto = settings.get("auto_transcode", True)
                self._send_json({
                    "status": "processing" if is_auto else "saved_raw",
                    "file": finished_file,
                    "auto_transcode": is_auto
                })
            else:
                self._send_json({"status": "not_recording"})

        elif parsed.path == "/api/settings":
            onboard_info = detect_onboard_camera()
            self._send_json({
                "settings": load_settings(),
                "presets": STREAM_PRESETS,
                "onboard_camera": onboard_info
            })

        elif parsed.path == "/api/transcode":
            query = parse_qs(parsed.query)
            clip_id = query.get("id", [None])[0]
            if clip_id:
                if not re.match(r"^(evf|picam)_[\w-]+$", clip_id):
                    self._send_json({"status": "error", "message": "Invalid clip ID"})
                    return
                raw_filename = f"{clip_id}.mkv"
                raw_path = os.path.join(REC_DIR, raw_filename)
                if not os.path.exists(raw_path):
                    self._send_json({"status": "error", "message": "File not found"})
                    return
                enqueue_recording(raw_filename, manual=True)
                self._send_json({"status": "enqueued", "id": clip_id, "file": raw_filename})
            else:
                enqueued = enqueue_orphaned_recordings(manual=True)
                self._send_json({"status": "enqueued_all", "count": len(enqueued), "files": enqueued})

        elif parsed.path == "/api/gallery":
            items = []
            settings = load_settings()
            auto_transcode = settings.get("auto_transcode", True)
            if os.path.exists(REC_DIR):
                for f in sorted(os.listdir(REC_DIR), reverse=True):
                    if f.endswith(".mkv"):
                        base = os.path.splitext(f)[0]
                        mp4_path = os.path.join(MP4_DIR, f"{base}.mp4")
                        raw_path = os.path.join(REC_DIR, f)
                        mp4_ready = os.path.exists(mp4_path)
                        thumb_ready = os.path.exists(os.path.join(THUMB_DIR, f"{base}.jpg"))
                        mp4_size = round(os.path.getsize(mp4_path) / (1024 * 1024), 1) if mp4_ready else None
                        raw_size = round(os.path.getsize(raw_path) / (1024 * 1024), 1) if os.path.exists(raw_path) else 0

                        with queued_lock:
                            is_in_queue = f in queued_files
                        is_active_encode = base in encoding_status
                        is_encoding = is_active_encode or is_in_queue
                        enc_info = encoding_status.get(base, None)

                        if is_active_encode and enc_info:
                            progress = enc_info["percent"]
                            eta_text = enc_info["eta_text"]
                        elif is_in_queue:
                            progress = 0
                            eta_text = "Queued..."
                        else:
                            progress = 100 if mp4_ready else 0
                            eta_text = ""

                        items.append({
                            "id": base,
                            "raw_name": f,
                            "mp4_name": f"{base}.mp4",
                            "size_mb": mp4_size if mp4_ready else raw_size,
                            "mp4_size_mb": mp4_size,
                            "raw_size_mb": raw_size,
                            "thumb": f"/media/thumb/{base}.jpg" if thumb_ready else None,
                            "mp4": f"/media/mp4/{base}.mp4" if mp4_ready else None,
                            "mkv": f"/media/raw/{base}.mkv",
                            "ready": mp4_ready,
                            "encoding": is_encoding,
                            "progress": progress,
                            "eta_text": eta_text,
                            "untranscoded": (not mp4_ready and not is_encoding)
                        })
            self._send_json({"items": items, "auto_transcode": auto_transcode})

        elif parsed.path == "/api/record/delete":
            query = parse_qs(parsed.query)
            clip_id = query.get("id", [None])[0]
            if not clip_id or not re.match(r"^(evf|picam)_[\w-]+$", clip_id):
                self._send_json({"status": "error", "message": "Invalid clip ID"})
                return

            with queued_lock:
                queued_files.discard(f"{clip_id}.mkv")

            # Clean up all associated assets for this recording from disk
            deleted = False
            for target_path in [
                os.path.join(REC_DIR, f"{clip_id}.mkv"),
                os.path.join(MP4_DIR, f"{clip_id}.mp4"),
                os.path.join(MP4_DIR, f"{clip_id}.tmp.mp4"),
                os.path.join(THUMB_DIR, f"{clip_id}.jpg")
            ]:
                if os.path.exists(target_path):
                    try:
                        os.remove(target_path)
                        deleted = True
                    except OSError:
                        pass

            self._send_json({"status": "deleted" if deleted else "not_found", "id": clip_id})

        elif parsed.path.startswith("/media/"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 3:
                mtype, fname = parts[1], parts[2]
                if mtype == "thumb":
                    folder = THUMB_DIR
                elif mtype == "raw":
                    folder = REC_DIR
                else:
                    folder = MP4_DIR
                target = os.path.join(folder, fname)

                if not os.path.exists(target):
                    self.send_error(404, "File not found")
                    return

                if mtype == "thumb":
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(os.path.getsize(target)))
                    self.end_headers()
                    with open(target, "rb") as fp:
                        self.wfile.write(fp.read())
                    return

                if mtype == "raw":
                    file_size = os.path.getsize(target)
                    self.send_response(200)
                    self.send_header("Content-Type", "video/x-matroska")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                    self.send_header("Content-Length", str(file_size))
                    self.end_headers()
                    with open(target, "rb") as fp:
                        chunk_size = 64 * 1024
                        while True:
                            data = fp.read(chunk_size)
                            if not data:
                                break
                            self.wfile.write(data)
                    return

                # Range-Request Handling for Video (iOS Safari Compliance)
                file_size = os.path.getsize(target)
                range_header = self.headers.get("Range")

                start = 0
                end = file_size - 1

                if range_header:
                    match = re.search(r"bytes=(\d+)-(\d*)", range_header)
                    if match:
                        start = int(match.group(1))
                        if match.group(2):
                            end = int(match.group(2))

                length = end - start + 1

                try:
                    if range_header:
                        self.send_response(206)
                        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    else:
                        self.send_response(200)

                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(length))
                    self.end_headers()

                    with open(target, "rb") as fp:
                        fp.seek(start)
                        chunk_size = 64 * 1024
                        bytes_to_send = length
                        while bytes_to_send > 0:
                            read_len = min(chunk_size, bytes_to_send)
                            data = fp.read(read_len)
                            if not data:
                                break
                            self.wfile.write(data)
                            bytes_to_send -= len(data)
                except (BrokenPipeError, ConnectionResetError):
                    # Safari actively cuts range requests when buffering atoms; ignore quietly
                    pass
                return

            self.send_error(404, "Media not found")

        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                data = {}

            new_settings = {}
            if "stream_preset" in data and data["stream_preset"] in STREAM_PRESETS:
                new_settings["stream_preset"] = data["stream_preset"]
                apply_stream_to_go2rtc(data["stream_preset"])
            if "download_format" in data and data["download_format"] in ["mp4", "mkv"]:
                new_settings["download_format"] = data["download_format"]
            if "auto_transcode" in data:
                new_settings["auto_transcode"] = bool(data["auto_transcode"])
            if "show_storage" in data:
                new_settings["show_storage"] = bool(data["show_storage"])
            if "hide_storage" in data:
                new_settings["show_storage"] = not bool(data["hide_storage"])
            if "onboard_record" in data:
                new_settings["onboard_record"] = bool(data["onboard_record"])
            if "onboard_preset" in data:
                new_settings["onboard_preset"] = str(data["onboard_preset"])

            saved = save_settings(new_settings)

            # If auto_transcode is ON, immediately enqueue any orphaned MKVs
            if saved.get("auto_transcode", True):
                enqueue_orphaned_recordings()

            onboard_info = detect_onboard_camera()
            self._send_json({
                "status": "saved",
                "settings": saved,
                "presets": STREAM_PRESETS,
                "onboard_camera": onboard_info
            })

        elif parsed.path == "/api/transcode":
            length = int(self.headers.get("Content-Length", 0))
            clip_id = None
            if length > 0:
                try:
                    body = self.rfile.read(length)
                    data = json.loads(body.decode("utf-8"))
                    clip_id = data.get("id")
                except Exception:
                    pass
            if not clip_id:
                query = parse_qs(parsed.query)
                clip_id = query.get("id", [None])[0]

            if clip_id:
                if not re.match(r"^(evf|picam)_[\w-]+$", clip_id):
                    self._send_json({"status": "error", "message": "Invalid clip ID"})
                    return
                raw_filename = f"{clip_id}.mkv"
                raw_path = os.path.join(REC_DIR, raw_filename)
                if not os.path.exists(raw_path):
                    self._send_json({"status": "error", "message": "File not found"})
                    return
                enqueue_recording(raw_filename, manual=True)
                self._send_json({"status": "enqueued", "id": clip_id, "file": raw_filename})
            else:
                enqueued = enqueue_orphaned_recordings(manual=True)
                self._send_json({"status": "enqueued_all", "count": len(enqueued), "files": enqueued})

        else:
            self.do_GET()

    def _send_json(self, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

# Backward-compatibility alias
CanopiHandler = BerryStreamHandler

if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8000), BerryStreamHandler)
    server.serve_forever()
