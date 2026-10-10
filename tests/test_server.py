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

        # Reset default settings file
        if os.path.exists(server.SETTINGS_FILE):
            os.remove(server.SETTINGS_FILE)

    def tearDown(self):
        if os.path.exists(TEST_DIR):
            shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_default_settings(self):
        """Verify default settings include auto_transcode: True."""
        settings = server.load_settings()
        self.assertTrue(settings.get("auto_transcode"))
        self.assertEqual(settings.get("stream_preset"), "720p60")
        self.assertEqual(settings.get("download_format"), "mp4")

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

if __name__ == "__main__":
    unittest.main()
