import os
import json
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Set up test environment paths before importing server
TEST_DIR = tempfile.mkdtemp(prefix="berrystream_test_")
REC_DIR = os.path.join(TEST_DIR, "recordings")
THUMB_DIR = os.path.join(REC_DIR, "thumbs")
MP4_DIR = os.path.join(REC_DIR, "mp4")
os.environ["REC_DIR"] = REC_DIR
os.environ["SETTINGS_FILE"] = os.path.join(REC_DIR, "settings.json")

import server

class TestAutoTranscode(unittest.TestCase):
    def setUp(self):
        # Clean and recreate test directories
        if os.path.exists(REC_DIR):
            shutil.rmtree(REC_DIR)
        os.makedirs(REC_DIR, exist_ok=True)
        os.makedirs(THUMB_DIR, exist_ok=True)
        os.makedirs(MP4_DIR, exist_ok=True)

        # Clear queue and queued_files
        with server.queued_lock:
            server.queued_files.clear()
        while not server.transcode_queue.empty():
            try:
                server.transcode_queue.get_nowait()
                server.transcode_queue.task_done()
            except Exception:
                break
        with server.manual_lock:
            server.manual_transcodes.clear()

        # Reset server globals
        server.current_process = None
        server.current_raw_file = None
        server.current_onboard_process = None
        server.current_onboard_raw_file = None
        server._onboard_cam_cache = None
        server._onboard_cam_cache_time = 0
        os.environ.pop("ONBOARD_CAMERA_MODEL", None)

        # Reset default settings file
        if os.path.exists(server.SETTINGS_FILE):
            os.remove(server.SETTINGS_FILE)

    def tearDown(self):
        if os.path.exists(TEST_DIR):
            shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_default_settings(self):
        """Verify default settings include auto_transcode: True and show_storage: True."""
        settings = server.load_settings()
        self.assertTrue(settings.get("auto_transcode"))
        self.assertEqual(settings.get("stream_preset"), "720p60")
        self.assertEqual(settings.get("download_format"), "mp4")
        self.assertTrue(settings.get("show_storage"))

    def test_save_and_load_storage_setting(self):
        """Verify saving show_storage as False and loading it back."""
        server.save_settings({"show_storage": False})
        settings = server.load_settings()
        self.assertFalse(settings.get("show_storage"))

        server.save_settings({"show_storage": True})
        settings = server.load_settings()
        self.assertTrue(settings.get("show_storage"))

    @patch("shutil.disk_usage")
    def test_get_storage_info_calculation(self, mock_usage):
        """Verify get_storage_info formats disk usage and percentages correctly."""
        # 32GB total, 16GB free, 16GB used
        total = 32 * 1024 * 1024 * 1024
        free = 16 * 1024 * 1024 * 1024
        used = 16 * 1024 * 1024 * 1024
        mock_usage.return_value = MagicMock(total=total, free=free, used=used)

        info = server.get_storage_info()
        self.assertEqual(info["total_gb"], 32.0)
        self.assertEqual(info["free_gb"], 16.0)
        self.assertEqual(info["used_gb"], 16.0)
        self.assertEqual(info["percent_free"], 50.0)
        self.assertEqual(info["percent_used"], 50.0)
        self.assertEqual(info["free_short"], "16G")
        self.assertEqual(info["free_human"], "16.0 GB")

    @patch("shutil.disk_usage")
    def test_get_storage_info_low_space(self, mock_usage):
        """Verify get_storage_info formats MB and small GB correctly."""
        # 32GB total, 500MB free
        total = 32 * 1024 * 1024 * 1024
        free = 500 * 1024 * 1024
        used = total - free
        mock_usage.return_value = MagicMock(total=total, free=free, used=used)

        info = server.get_storage_info()
        self.assertEqual(info["free_short"], "500M")
        self.assertEqual(info["free_human"], "500 MB")

    @patch("server.get_storage_info")
    def test_storage_api_endpoint(self, mock_storage_info):
        """Verify /api/storage endpoint returns storage info."""
        mock_storage_info.return_value = {"free_gb": 12.5, "free_short": "13G", "percent_free": 45.0}

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/storage"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertEqual(resp.get("free_gb"), 12.5)
        self.assertEqual(resp.get("free_short"), "13G")

    @patch("server.get_storage_info")
    def test_record_status_includes_storage(self, mock_storage_info):
        """Verify /api/record/status includes storage info in response."""
        mock_storage_info.return_value = {"free_gb": 12.5, "free_short": "13G"}

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/record/status"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertIn("storage", resp)
        self.assertEqual(resp["storage"]["free_short"], "13G")

    @patch("server.is_camera_connected")
    def test_record_status_includes_camera_connected(self, mock_camera_conn):
        """Verify /api/record/status includes camera_connected in response."""
        mock_camera_conn.return_value = True

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/record/status"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertIn("camera_connected", resp)
        self.assertTrue(resp["camera_connected"])

    @patch("os.path.exists")
    def test_is_camera_connected(self, mock_exists):
        """Verify is_camera_connected checks video device existence."""
        mock_exists.return_value = True
        self.assertTrue(server.is_camera_connected())

        mock_exists.return_value = False
        self.assertFalse(server.is_camera_connected())

    @patch("server.is_camera_connected")
    def test_connection_status_endpoint(self, mock_camera_conn):
        """Verify /api/connection/status returns phone and camera status."""
        mock_camera_conn.return_value = True

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/connection/status"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertTrue(resp.get("phone"))
        self.assertTrue(resp.get("camera_connected"))

    def test_post_settings_show_storage(self):
        """Verify /api/settings handles toggling show_storage setting."""
        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {"Content-Length": "23"}
        handler.rfile = MagicMock()
        handler.rfile.read.return_value = json.dumps({"show_storage": False}).encode("utf-8")
        handler.path = "/api/settings"
        handler._send_json = MagicMock()

        handler.do_POST()

        self.assertFalse(server.load_settings().get("show_storage"))
        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertFalse(resp["settings"]["show_storage"])

    def test_post_settings_hide_storage(self):
        """Verify /api/settings handles hide_storage parameter correctly."""
        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {"Content-Length": "22"}
        handler.rfile = MagicMock()
        handler.rfile.read.return_value = json.dumps({"hide_storage": True}).encode("utf-8")
        handler.path = "/api/settings"
        handler._send_json = MagicMock()

        handler.do_POST()

        self.assertFalse(server.load_settings().get("show_storage"))
        resp = handler._send_json.call_args[0][0]
        self.assertFalse(resp["settings"]["show_storage"])

    def test_save_and_load_settings(self):
        """Verify saving auto_transcode as False and loading it back."""
        server.save_settings({"auto_transcode": False})
        settings = server.load_settings()
        self.assertFalse(settings.get("auto_transcode"))

        server.save_settings({"auto_transcode": True})
        settings = server.load_settings()
        self.assertTrue(settings.get("auto_transcode"))

    @patch("server.process_recording")
    def test_stop_recording_with_auto_transcode_on(self, mock_process):
        """When auto_transcode is ON, stopping a recording enqueues it for transcoding."""
        server.save_settings({"auto_transcode": True})
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        server.current_process = mock_proc
        server.current_raw_file = "evf_20261010_120000.mkv"

        stopped = server.stop_recording_internal(reason="test")
        self.assertEqual(stopped, "evf_20261010_120000.mkv")
        mock_proc.send_signal.assert_called_once()
        # Verify worker received the job
        time.sleep(0.05)
        mock_process.assert_called_with("evf_20261010_120000.mkv")

    @patch("server.process_recording")
    def test_stop_recording_with_auto_transcode_off(self, mock_process):
        """When auto_transcode is OFF, stopping a recording does NOT enqueue it."""
        server.save_settings({"auto_transcode": False})
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        server.current_process = mock_proc
        server.current_raw_file = "evf_20261010_120001.mkv"

        stopped = server.stop_recording_internal(reason="test")
        self.assertEqual(stopped, "evf_20261010_120001.mkv")
        mock_proc.send_signal.assert_called_once()
        time.sleep(0.05)
        mock_process.assert_not_called()
        self.assertNotIn("evf_20261010_120001.mkv", server.queued_files)
        self.assertTrue(server.transcode_queue.empty())

    @patch("server.process_recording")
    def test_enqueue_orphaned_recordings(self, mock_process):
        """Verify enqueue_orphaned_recordings finds all MKVs lacking MP4s and enqueues them."""
        # Create an orphaned MKV
        with open(os.path.join(server.REC_DIR, "evf_20261010_100000.mkv"), "w") as f:
            f.write("mock mkv 1")
        # Create an MKV that already has an MP4 (should be skipped)
        with open(os.path.join(server.REC_DIR, "evf_20261010_100001.mkv"), "w") as f:
            f.write("mock mkv 2")
        with open(os.path.join(server.MP4_DIR, "evf_20261010_100001.mp4"), "w") as f:
            f.write("mock mp4 2")
        # Create an orphaned MKV with leftover temp file (tmp should be deleted)
        with open(os.path.join(server.REC_DIR, "evf_20261010_100002.mkv"), "w") as f:
            f.write("mock mkv 3")
        tmp_file = os.path.join(server.MP4_DIR, "evf_20261010_100002.tmp.mp4")
        with open(tmp_file, "w") as f:
            f.write("corrupt tmp")

        enqueued = server.enqueue_orphaned_recordings()
        self.assertIn("evf_20261010_100000.mkv", enqueued)
        self.assertIn("evf_20261010_100002.mkv", enqueued)
        self.assertNotIn("evf_20261010_100001.mkv", enqueued)

        # Check temp file was cleaned up
        self.assertFalse(os.path.exists(tmp_file))

        # Check worker received both
        time.sleep(0.05)
        calls = [c[0][0] for c in mock_process.call_args_list]
        self.assertIn("evf_20261010_100000.mkv", calls)
        self.assertIn("evf_20261010_100002.mkv", calls)

    @patch("server.process_recording")
    def test_reconciliation_skips_when_auto_transcode_off(self, mock_process):
        """When auto_transcode is OFF, reconciliation does not enqueue files."""
        server.save_settings({"auto_transcode": False})
        with open(os.path.join(server.REC_DIR, "evf_20261010_110000.mkv"), "w") as f:
            f.write("mock mkv")

        settings = server.load_settings()
        if settings.get("auto_transcode", True):
            server.enqueue_orphaned_recordings()

        time.sleep(0.05)
        mock_process.assert_not_called()
        self.assertNotIn("evf_20261010_110000.mkv", server.queued_files)

    @patch("server.process_recording")
    def test_post_settings_triggers_orphan_transcoding_when_toggled_on(self, mock_process):
        """When settings are updated to auto_transcode: True, all orphaned MKVs are enqueued."""
        server.save_settings({"auto_transcode": False})
        with open(os.path.join(server.REC_DIR, "evf_20261010_111111.mkv"), "w") as f:
            f.write("mock mkv")

        # Mock handler POST request
        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {"Content-Length": "25"}
        handler.rfile = MagicMock()
        handler.rfile.read.return_value = json.dumps({"auto_transcode": True}).encode("utf-8")
        handler.path = "/api/settings"
        handler._send_json = MagicMock()

        handler.do_POST()

        self.assertTrue(server.load_settings().get("auto_transcode"))
        time.sleep(0.05)
        mock_process.assert_called_with("evf_20261010_111111.mkv")

    @patch("server.process_recording")
    def test_manual_transcode_endpoint(self, mock_process):
        """Test /api/transcode can enqueue a specific recording even if auto_transcode is OFF."""
        server.save_settings({"auto_transcode": False})
        with open(os.path.join(server.REC_DIR, "evf_20261010_222222.mkv"), "w") as f:
            f.write("mock mkv")

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/transcode?id=evf_20261010_222222"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertEqual(resp.get("status"), "enqueued")
        time.sleep(0.05)
        mock_process.assert_called_with("evf_20261010_222222.mkv")

    def test_transcode_endpoint_rejects_invalid_id(self):
        """Test /api/transcode rejects path traversal attempts."""
        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/transcode?id=../../evil"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertEqual(resp.get("status"), "error")
        self.assertEqual(resp.get("message"), "Invalid clip ID")

    @patch("server.process_recording")
    def test_batch_transcode_endpoint(self, mock_process):
        """Test /api/transcode without id triggers transcode for all orphaned MKVs."""
        server.save_settings({"auto_transcode": False})
        with open(os.path.join(server.REC_DIR, "evf_20261010_444444.mkv"), "w") as f:
            f.write("mock mkv")

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/transcode"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertEqual(resp.get("status"), "enqueued_all")
        self.assertEqual(resp.get("count"), 1)
        time.sleep(0.05)
        mock_process.assert_called_with("evf_20261010_444444.mkv")

    @patch("server.process_recording")
    def test_worker_skips_when_auto_transcode_off(self, mock_process):
        """When auto_transcode is OFF, the background worker skips enqueued automatic jobs."""
        server.save_settings({"auto_transcode": False})
        server.enqueue_recording("evf_20261010_555555.mkv", manual=False)
        time.sleep(0.05)
        mock_process.assert_not_called()
        self.assertNotIn("evf_20261010_555555.mkv", server.queued_files)

    def test_gallery_reports_untranscoded(self):
        """Verify /api/gallery marks orphaned recordings as untranscoded: True."""
        server.save_settings({"auto_transcode": False})
        with open(os.path.join(server.REC_DIR, "evf_20261010_333333.mkv"), "w") as f:
            f.write("mock mkv")

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.path = "/api/gallery"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertIn("items", resp)
        self.assertEqual(len(resp["items"]), 1)
        item = resp["items"][0]
        self.assertEqual(item["id"], "evf_20261010_333333")
        self.assertFalse(item["ready"])
        self.assertFalse(item["encoding"])
        self.assertTrue(item["untranscoded"])

    # --- Onboard Camera Tests ---

    def test_default_settings_includes_onboard_camera(self):
        """Verify default settings include onboard_record and onboard_preset."""
        settings = server.load_settings()
        self.assertIn("onboard_record", settings)
        self.assertFalse(settings["onboard_record"])
        self.assertIn("onboard_preset", settings)
        self.assertEqual(settings["onboard_preset"], "720p60")

    def test_detect_onboard_camera_none_by_default(self):
        """When running in clean desktop/test environment, detect_onboard_camera returns detected: False."""
        server._onboard_cam_cache = None
        os.environ.pop("ONBOARD_CAMERA_MODEL", None)
        info = server.detect_onboard_camera(force_refresh=True)
        self.assertFalse(info["detected"])
        self.assertIsNone(info["model"])
        self.assertEqual(info["presets"], {})

    def test_detect_onboard_camera_v2_env(self):
        """When ONBOARD_CAMERA_MODEL=v2, returns Sony IMX219 v2 camera and presets."""
        server._onboard_cam_cache = None
        os.environ["ONBOARD_CAMERA_MODEL"] = "v2"
        info = server.detect_onboard_camera(force_refresh=True)
        self.assertTrue(info["detected"])
        self.assertEqual(info["model"], "v2")
        self.assertIn("Camera Module v2", info["name"])
        self.assertIn("IMX219", info["sensor"])
        self.assertIn("1080p30", info["presets"])
        self.assertIn("720p60", info["presets"])
        self.assertIn("1640x1232_30", info["presets"])
        self.assertIn("640x480_90", info["presets"])

    def test_detect_onboard_camera_v1_env(self):
        """When ONBOARD_CAMERA_MODEL=v1.3, returns OmniVision OV5647 v1.3 camera and presets."""
        server._onboard_cam_cache = None
        os.environ["ONBOARD_CAMERA_MODEL"] = "v1.3"
        info = server.detect_onboard_camera(force_refresh=True)
        self.assertTrue(info["detected"])
        self.assertEqual(info["model"], "v1.3")
        self.assertIn("Camera Module v1.3", info["name"])
        self.assertIn("OV5647", info["sensor"])
        self.assertIn("1080p30", info["presets"])
        self.assertIn("720p60", info["presets"])
        self.assertIn("640x480_60", info["presets"])
        self.assertNotIn("1640x1232_30", info["presets"])

    @patch("os.path.isdir")
    @patch("os.listdir")
    def test_detect_onboard_camera_sysfs_v2(self, mock_listdir, mock_isdir):
        """Verify sysfs detection of IMX219 (v2) module."""
        server._onboard_cam_cache = None
        os.environ.pop("ONBOARD_CAMERA_MODEL", None)

        def isdir_side_effect(path):
            return path == "/sys/bus/i2c/drivers/imx219"

        mock_isdir.side_effect = isdir_side_effect
        mock_listdir.return_value = ["10-0010", "bind", "unbind"]

        info = server.detect_onboard_camera(force_refresh=True)
        self.assertTrue(info["detected"])
        self.assertEqual(info["model"], "v2")

    @patch("os.path.isdir")
    @patch("os.listdir")
    def test_detect_onboard_camera_sysfs_v1(self, mock_listdir, mock_isdir):
        """Verify sysfs detection of OV5647 (v1.3) module."""
        server._onboard_cam_cache = None
        os.environ.pop("ONBOARD_CAMERA_MODEL", None)

        def isdir_side_effect(path):
            return path == "/sys/bus/i2c/drivers/ov5647"

        mock_isdir.side_effect = isdir_side_effect
        mock_listdir.return_value = ["10-0036", "bind", "unbind"]

        info = server.detect_onboard_camera(force_refresh=True)
        self.assertTrue(info["detected"])
        self.assertEqual(info["model"], "v1.3")

    def test_build_onboard_record_command(self):
        """Verify build_onboard_record_command produces valid command parameters."""
        os.environ["ONBOARD_CAMERA_MODEL"] = "v2"
        cmd = server.build_onboard_record_command("/path/to/test.mkv", preset_key="1080p30")
        self.assertIn("/path/to/test.mkv", cmd)
        cmd_str = " ".join(cmd)
        self.assertTrue("1920" in cmd_str or "1080" in cmd_str)

    def test_settings_api_includes_onboard_camera(self):
        """Verify /api/settings GET returns onboard_camera object."""
        os.environ["ONBOARD_CAMERA_MODEL"] = "v2"
        server._onboard_cam_cache = None

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/settings"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertIn("onboard_camera", resp)
        self.assertTrue(resp["onboard_camera"]["detected"])
        self.assertEqual(resp["onboard_camera"]["model"], "v2")

    def test_post_settings_saves_onboard_settings(self):
        """Verify saving onboard_record and onboard_preset via POST /api/settings."""
        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        payload = json.dumps({"onboard_record": True, "onboard_preset": "1080p30"}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = MagicMock()
        handler.rfile.read.return_value = payload
        handler.path = "/api/settings"
        handler._send_json = MagicMock()

        handler.do_POST()

        settings = server.load_settings()
        self.assertTrue(settings.get("onboard_record"))
        self.assertEqual(settings.get("onboard_preset"), "1080p30")
        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertTrue(resp["settings"]["onboard_record"])
        self.assertEqual(resp["settings"]["onboard_preset"], "1080p30")

    @patch("subprocess.Popen")
    def test_simultaneous_recording_start(self, mock_popen):
        """When onboard_record is True and camera is detected, start_record begins both EVF and Onboard capture."""
        os.environ["ONBOARD_CAMERA_MODEL"] = "v2"
        server._onboard_cam_cache = None
        server.save_settings({"onboard_record": True, "onboard_preset": "720p60"})

        mock_proc_evf = MagicMock()
        mock_proc_evf.poll.return_value = None
        mock_proc_onboard = MagicMock()
        mock_proc_onboard.poll.return_value = None
        mock_popen.side_effect = [mock_proc_evf, mock_proc_onboard]

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/record/start"
        handler._send_json = MagicMock()

        handler.do_GET()

        self.assertEqual(mock_popen.call_count, 2)
        self.assertIsNotNone(server.current_process)
        self.assertIsNotNone(server.current_onboard_process)
        self.assertTrue(server.current_raw_file.startswith("evf_"))
        self.assertTrue(server.current_onboard_raw_file.startswith("picam_"))

        resp = handler._send_json.call_args[0][0]
        self.assertEqual(resp["status"], "started")
        self.assertIn("evf_", resp["file"])
        self.assertIn("picam_", resp["onboard_file"])

    @patch("subprocess.Popen")
    def test_record_start_onboard_disabled(self, mock_popen):
        """When onboard_record is False, only EVF is started."""
        os.environ["ONBOARD_CAMERA_MODEL"] = "v2"
        server._onboard_cam_cache = None
        server.save_settings({"onboard_record": False})

        mock_proc_evf = MagicMock()
        mock_proc_evf.poll.return_value = None
        mock_popen.return_value = mock_proc_evf

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/record/start"
        handler._send_json = MagicMock()

        handler.do_GET()

        self.assertEqual(mock_popen.call_count, 1)
        self.assertIsNotNone(server.current_process)
        self.assertIsNone(server.current_onboard_process)
        self.assertIsNone(server.current_onboard_raw_file)

    @patch("server.process_recording")
    def test_simultaneous_recording_stop_enqueues_both(self, mock_process):
        """When both EVF and Onboard recordings are active, stopping enqueues both files."""
        server.save_settings({"auto_transcode": True})

        mock_evf = MagicMock()
        mock_evf.poll.return_value = None
        mock_onboard = MagicMock()
        mock_onboard.poll.return_value = None

        server.current_process = mock_evf
        server.current_raw_file = "evf_20261014_100000.mkv"
        server.current_onboard_process = mock_onboard
        server.current_onboard_raw_file = "picam_20261014_100000.mkv"

        stopped = server.stop_recording_internal(reason="test_both")

        mock_evf.send_signal.assert_called_once()
        mock_onboard.send_signal.assert_called_once()
        self.assertIsNone(server.current_process)
        self.assertIsNone(server.current_onboard_process)

        time.sleep(0.05)
        calls = [c[0][0] for c in mock_process.call_args_list]
        self.assertIn("evf_20261014_100000.mkv", calls)
        self.assertIn("picam_20261014_100000.mkv", calls)

    @patch("server.process_recording")
    def test_transcode_endpoint_accepts_picam_id(self, mock_process):
        """Verify /api/transcode accepts picam_ clip ID."""
        server.save_settings({"auto_transcode": False})
        with open(os.path.join(server.REC_DIR, "picam_20261014_777777.mkv"), "w") as f:
            f.write("mock picam mkv")

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/transcode?id=picam_20261014_777777"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertEqual(resp.get("status"), "enqueued")
        time.sleep(0.05)
        mock_process.assert_called_with("picam_20261014_777777.mkv")

    def test_delete_endpoint_accepts_picam_id(self):
        """Verify /api/record/delete accepts picam_ clip ID and cleans up assets."""
        mkv_path = os.path.join(server.REC_DIR, "picam_20261014_888888.mkv")
        mp4_path = os.path.join(server.MP4_DIR, "picam_20261014_888888.mp4")
        with open(mkv_path, "w") as f:
            f.write("mkv")
        with open(mp4_path, "w") as f:
            f.write("mp4")

        handler = server.BerryStreamHandler.__new__(server.BerryStreamHandler)
        handler.headers = {}
        handler.path = "/api/record/delete?id=picam_20261014_888888"
        handler._send_json = MagicMock()

        handler.do_GET()

        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args[0][0]
        self.assertEqual(resp.get("status"), "deleted")
        self.assertFalse(os.path.exists(mkv_path))
        self.assertFalse(os.path.exists(mp4_path))

if __name__ == "__main__":
    unittest.main()
