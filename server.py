import os
import re
import signal
import subprocess
import time
import json
import threading
import urllib.request
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

REC_DIR = "/app/recordings"
THUMB_DIR = "/app/recordings/thumbs"
MP4_DIR = "/app/recordings/mp4"
SETTINGS_FILE = os.path.join(REC_DIR, "settings.json")

os.makedirs(REC_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(MP4_DIR, exist_ok=True)

STREAM_PRESETS = {
    "1080p60": {"width": 1920, "height": 1080, "fps": 60, "label": "1080p 60fps (Full HD)"},
    "1080p30": {"width": 1920, "height": 1080, "fps": 30, "label": "1080p 30fps (Full HD)"},
    "720p60":  {"width": 1280, "height": 720,  "fps": 60, "label": "720p 60fps (HD)"},
    "720p30":  {"width": 1280, "height": 720,  "fps": 30, "label": "720p 30fps (HD)"},
}

DEFAULT_SETTINGS = {
    "stream_preset": "1080p60",
    "download_format": "mp4"  # 'mp4' or 'mkv'
}

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

def apply_stream_to_go2rtc(preset_key):
    preset = STREAM_PRESETS.get(preset_key, STREAM_PRESETS["1080p60"])
    dst = f"v4l2:device?video=/dev/video0&input_format=mjpeg&video_size={preset['width']}x{preset['height']}&framerate={preset['fps']}"
    try:
        url = f"http://127.0.0.1:1984/api/streams?src=evf&dst={urllib.parse.quote(dst)}"
        req = urllib.request.Request(url, method="PUT")
        with urllib.request.urlopen(req, timeout=1.5):
            pass
    except Exception:
        pass

current_process = None
current_raw_file = None
start_time = 0
last_heartbeat_time = 0
encoding_status = {}  # { base_id: { "percent": int, "eta_text": str } }

def set_low_priority():
    """Sets lowest process scheduling priority so background ffmpeg never starves go2rtc."""
    try:
        os.nice(19)
    except Exception:
        pass

def stop_recording_internal(reason="manual"):
    """Thread-safe recording stop & finalize: sends SIGINT and starts transcoding."""
    global current_process, current_raw_file
    if current_process and current_process.poll() is None:
        finished_file = current_raw_file
        print(f"Stopping recording ({reason}): {finished_file}")
        current_process.send_signal(signal.SIGINT)
        try:
            current_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            current_process.kill()

        current_process = None
        current_raw_file = None

        if finished_file:
            threading.Thread(target=process_recording, args=(finished_file,), daemon=True).start()
        return finished_file
    return None

def recording_watchdog_loop():
    """Background watchdog: automatically saves & finalizes recording if phone dies or disconnects."""
    global current_process, last_heartbeat_time
    HEARTBEAT_TIMEOUT = 5.0
    while True:
        time.sleep(1.0)
        if current_process and current_process.poll() is None:
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

def process_recording(raw_filename):
    """Background worker: creates thumbnail and transcodes to iOS MP4 cleanly with progress tracking."""
    raw_path = os.path.join(REC_DIR, raw_filename)
    base_name = os.path.splitext(raw_filename)[0]
    thumb_path = os.path.join(THUMB_DIR, f"{base_name}.jpg")
    mp4_temp = os.path.join(MP4_DIR, f"{base_name}.tmp.mp4")
    mp4_final = os.path.join(MP4_DIR, f"{base_name}.mp4")

    # 1. Generate thumbnail
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", raw_path,
        "-vframes", "1",
        "-vf", "scale=480:-1",
        thumb_path
    ], preexec_fn=set_low_priority)

    # 2. Get total clip duration for progress calculation
    total_duration = get_file_duration(raw_path)

    # Determine framerate from current settings
    current_settings = load_settings()
    preset = STREAM_PRESETS.get(current_settings.get("stream_preset"), STREAM_PRESETS["1080p60"])
    fps_str = str(preset["fps"])

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
            preexec_fn=set_low_priority
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

class CanopiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="/app/web", **kwargs)

    def do_GET(self):
        global current_process, current_raw_file, start_time
        parsed = urlparse(self.path)

        if parsed.path == "/api/record/status":
            is_recording = current_process is not None and current_process.poll() is None
            elapsed = int(time.time() - start_time) if is_recording else 0
            self._send_json({"recording": is_recording, "elapsed": elapsed, "file": current_raw_file})

        elif parsed.path == "/api/record/start":
            if current_process is None or current_process.poll() is not None:
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
                start_time = time.time()
                last_heartbeat_time = time.time()
                self._send_json({"status": "started", "file": current_raw_file})
            else:
                self._send_json({"status": "already_recording", "file": current_raw_file})

        elif parsed.path == "/api/record/ping":
            last_heartbeat_time = time.time()
            self._send_json({"status": "pong"})

        elif parsed.path == "/api/record/stop":
            finished_file = stop_recording_internal(reason="user_stop")
            if finished_file:
                self._send_json({"status": "processing", "file": finished_file})
            else:
                self._send_json({"status": "not_recording"})

        elif parsed.path == "/api/settings":
            self._send_json({"settings": load_settings(), "presets": STREAM_PRESETS})

        elif parsed.path == "/api/gallery":
            items = []
            for f in sorted(os.listdir(REC_DIR), reverse=True):
                if f.endswith(".mkv"):
                    base = os.path.splitext(f)[0]
                    mp4_path = os.path.join(MP4_DIR, f"{base}.mp4")
                    raw_path = os.path.join(REC_DIR, f)
                    mp4_ready = os.path.exists(mp4_path)
                    thumb_ready = os.path.exists(os.path.join(THUMB_DIR, f"{base}.jpg"))
                    mp4_size = round(os.path.getsize(mp4_path) / (1024 * 1024), 1) if mp4_ready else None
                    raw_size = round(os.path.getsize(raw_path) / (1024 * 1024), 1)

                    is_encoding = base in encoding_status
                    enc_info = encoding_status.get(base, None)

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
                        "progress": enc_info["percent"] if is_encoding else (100 if mp4_ready else 0),
                        "eta_text": enc_info["eta_text"] if is_encoding else ""
                    })
            self._send_json({"items": items})

        elif parsed.path == "/api/record/delete":
            query = parse_qs(parsed.query)
            clip_id = query.get("id", [None])[0]
            if not clip_id or not re.match(r"^evf_[\w-]+$", clip_id):
                self._send_json({"status": "error", "message": "Invalid clip ID"})
                return

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

            saved = save_settings(new_settings)
            self._send_json({"status": "saved", "settings": saved, "presets": STREAM_PRESETS})
        else:
            self.do_GET()

    def _send_json(self, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8000), CanopiHandler)
    server.serve_forever()
