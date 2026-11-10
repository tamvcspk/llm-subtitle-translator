import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gst_worker.logs import UTCFormatter, clear_logs, read_log_snapshot
from logging.handlers import RotatingFileHandler


class LogTests(unittest.TestCase):
    def test_snapshot_groups_tracebacks_and_limits_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "worker.log").write_text(
                "2026-09-07T12:00:00Z INFO Earlier entry\n"
                "2026-09-07T12:01:00Z ERROR Translation failed\n"
                "Traceback (most recent call last):\n  example.py:12\nValueError: invalid subtitle\n",
                encoding="utf-8",
            )
            result = read_log_snapshot(tmp, 1)
            self.assertEqual(len(result["entries"]), 1)
            self.assertEqual(result["entries"][0]["level"], "ERROR")
            self.assertIn("ValueError: invalid subtitle", result["entries"][0]["details"])
            self.assertTrue(result["truncated"])
            self.assertEqual(read_log_snapshot(tmp)["entries"][0]["message"], "Earlier entry")

    def test_large_file_discards_partial_event_at_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "worker.log").write_text(
                "2026-09-07T12:00:00Z ERROR Old entry\n" + "x" * 10000 + "\n"
                "2026-09-07T12:01:00Z INFO Latest entry\n", encoding="utf-8",
            )
            with patch("gst_worker.logs.MAX_LOG_BYTES", 100):
                result = read_log_snapshot(tmp)
            self.assertTrue(result["truncated"])
            self.assertEqual([entry["message"] for entry in result["entries"]], ["Latest entry"])

    def test_missing_empty_and_non_utf8_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_log_snapshot(tmp)["entries"], [])
            clear_logs(tmp)
            self.assertEqual(read_log_snapshot(tmp)["entries"], [])
            Path(tmp, "worker.log").write_bytes(b"2026-09-07T12:00:00Z INFO bad byte \xff\n")
            self.assertIn("bad byte", read_log_snapshot(tmp)["entries"][0]["message"])

    def test_rotation_and_clear_allow_subsequent_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "worker.log")
            handler = RotatingFileHandler(path, maxBytes=100, backupCount=3, encoding="utf-8")
            handler.setFormatter(UTCFormatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
            logger = logging.Logger("test-logs", logging.INFO)
            logger.addHandler(handler)
            try:
                for index in range(10):
                    logger.info("Translation completed %s", index)
                self.assertTrue(Path(tmp, "worker.log.1").exists())
                self.assertLessEqual(len(list(Path(tmp).glob("worker.log*"))), 4)
                with patch.object(logging.getLogger(), "handlers", [handler]):
                    clear_logs(tmp)
                self.assertEqual(path.read_text(encoding="utf-8"), "")
                self.assertTrue(Path(tmp, "worker.log.1").exists())
                logger.info("After clear")
                entry = read_log_snapshot(tmp)["entries"][0]
                self.assertEqual(entry["message"], "After clear")
                self.assertTrue(entry["timestamp"].endswith("Z"))
            finally:
                handler.close()
