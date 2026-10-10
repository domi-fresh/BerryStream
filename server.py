import os
import re
import signal
import subprocess
import time
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

REC_DIR = "/app/recordings"
THUMB_DIR = "/app/recordings/thumbs"
MP4_DIR = "/app/recordings/mp4"

os.makedirs(REC_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(MP4_DIR, exist_ok=True)

current_process = None
current_raw_file = None
start_time = 0

def set_low_priority():
    """Sets lowest process scheduling priority so background ffmpeg never starves go2rtc."""
    try:
        os.nice(19)
    except Exception:
        pass

def process_recording(raw_filename):
    """Background worker: creates thumbnail and transcodes to iOS MP4 cleanly."""
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

    # 2. Transcode to web-safe MP4 at 60 fps (matching EVF stream rate, restricted to 2 threads and nice 19)
    res = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-threads", "2",
        "-r", "60",
        "-i", raw_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-r", "60",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        mp4_temp
    ], preexec_fn=set_low_priority)

    if res.returncode == 0 and os.path.exists(mp4_temp):
        os.rename(mp4_temp, mp4_final)

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
                    "-framerate", "60",
                    "-i", "http://127.0.0.1:1984/api/stream.mjpeg?src=evf",
                    "-c:v", "copy",
                    filepath
                ]
                current_process = subprocess.Popen(cmd)
                start_time = time.time()
                self._send_json({"status": "started", "file": current_raw_file})
            else:
                self._send_json({"status": "already_recording", "file": current_raw_file})

        elif parsed.path == "/api/record/stop":
            if current_process and current_process.poll() is None:
                current_process.send_signal(signal.SIGINT)
                try:
                    current_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    current_process.kill()

                finished_file = current_raw_file
                current_process = None
                current_raw_file = None

                threading.Thread(target=process_recording, args=(finished_file,), daemon=True).start()
                self._send_json({"status": "processing", "file": finished_file})
            else:
                self._send_json({"status": "not_recording"})

        elif parsed.path == "/api/gallery":
            items = []
            for f in sorted(os.listdir(REC_DIR), reverse=True):
                if f.endswith(".mkv"):
                    base = os.path.splitext(f)[0]
                    mp4_ready = os.path.exists(os.path.join(MP4_DIR, f"{base}.mp4"))
                    thumb_ready = os.path.exists(os.path.join(THUMB_DIR, f"{base}.jpg"))
                    raw_size = round(os.path.getsize(os.path.join(REC_DIR, f)) / (1024 * 1024), 1)

                    items.append({
                        "id": base,
                        "raw_name": f,
                        "size_mb": raw_size,
                        "thumb": f"/media/thumb/{base}.jpg" if thumb_ready else None,
                        "mp4": f"/media/mp4/{base}.mp4" if mp4_ready else None,
                        "ready": mp4_ready
                    })
            self._send_json({"items": items})

        elif parsed.path.startswith("/media/"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 3:
                mtype, fname = parts[1], parts[2]
                folder = THUMB_DIR if mtype == "thumb" else MP4_DIR
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
