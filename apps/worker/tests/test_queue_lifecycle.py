import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gst_worker import queue, translation


class QueueLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue_dir = str(self.root / "queue")
        self.jobs = queue.JobQueue(self.queue_dir)
        self.source = self.root / "movie.en.srt"
        self.source.write_text("subtitle", encoding="utf-8")
        os.utime(self.source, (100, 100))
        self.base = {"subtitle_path": str(self.source), "source_code": "en"}
        self.targets = [{"code": "zh", "language": "Chinese", "enabled": True}]

    def enqueue(self):
        return Path(queue.enqueue_translation_jobs(self.queue_dir, self.base, self.targets)[0]).stem

    def snapshot(self):
        return queue.queue_snapshot(self.queue_dir)

    def test_retry_exhaustion_manual_retry_and_completion_share_one_lifecycle(self):
        job_id = self.enqueue()
        execute = Mock(side_effect=translation.ProviderUnavailableError("503 unavailable"))
        now = 1000
        for retry_count, delay in enumerate((300, 900, 1800, 3600), 1):
            with patch("gst_worker.queue.time.time", return_value=now):
                self.assertTrue(self.jobs.process_once(execute, now=now))
            deferred = self.snapshot()["deferred"][0]
            self.assertEqual(deferred["provider_retry_count"], retry_count)
            self.assertEqual(deferred["retry_at"], now + delay)
            self.assertEqual(deferred["error"], "503 unavailable")
            self.assertFalse(self.jobs.process_once(execute, now=now + delay - 1))
            now += delay

        self.assertTrue(self.jobs.process_once(execute, now=now))
        self.assertEqual(self.snapshot()["counts"]["failed"], 1)
        self.assertEqual(execute.call_count, 5)
        self.assertTrue(queue.retry_failed_job(self.queue_dir, job_id))
        pending = self.snapshot()["pending"][0]
        for field in ("provider_retry_count", "retry_at", "deferred_reason", "last_error"):
            self.assertNotIn(field, pending)
        self.assertEqual(list(Path(self.queue_dir).rglob("*.error")), [])

        def finish(job, update_status):
            update_status("Translating")
            self.assertEqual(self.snapshot()["processing"][0]["stage"], "Translating")
            return "translated"

        self.assertTrue(self.jobs.process_once(finish, now=now))
        self.assertEqual(self.snapshot()["counts"]["done"], 1)
        self.assertEqual(list(Path(self.queue_dir).rglob("*.tmp")), [])
        self.assertTrue(queue.delete_queue_job(self.queue_dir, "done", job_id))
        self.assertEqual(self.snapshot()["total"], 0)

    def test_daily_quota_survives_restart_and_expires_for_admission_and_retry(self):
        job_id = self.enqueue()
        execute = Mock(side_effect=translation.DailyQuotaExceededError("429 daily quota"))
        with patch("gst_worker.queue.time.time", return_value=1000):
            self.assertTrue(self.jobs.process_once(execute, now=1000))
        restarted = queue.JobQueue(self.queue_dir)
        with patch("gst_worker.queue.time.time", return_value=1001):
            self.assertFalse(restarted.process_once(execute))
            with self.assertRaises(ValueError):
                self.enqueue()
            with self.assertRaises(ValueError):
                queue.retry_failed_job(self.queue_dir, job_id)
        with patch("gst_worker.queue.time.time", return_value=87400):
            self.assertEqual(self.enqueue(), job_id)
            self.assertTrue(restarted.process_once(lambda job, status: "translated"))
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(self.snapshot()["counts"]["done"], 1)
        self.assertEqual(list(Path(self.queue_dir).rglob("*.error")), [])

    def test_interrupted_execution_recovers_through_the_same_queue(self):
        self.enqueue()

        def interrupt(job, update_status):
            update_status("Sending batches")
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.jobs.process_once(interrupt)
        self.assertEqual(self.snapshot()["counts"]["processing"], 1)
        restarted = queue.JobQueue(self.queue_dir)
        restarted.recover_interrupted_jobs()
        self.assertEqual(self.snapshot()["pending"][0]["stage"], "Recovered after service restart")
        self.assertTrue(restarted.process_once(lambda job, status: "translated"))
        self.assertEqual(self.snapshot()["counts"]["done"], 1)

    def test_bazarr_embedded_python_admission_matches_worker_file_protocol(self):
        # Execute the producer payload without requiring Bash on Windows.
        script = (Path(__file__).resolve().parents[1] / "bazarr-postprocess" / "gst_enqueue.sh").read_text(encoding="utf-8")
        payload = script.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        environment = {
            **os.environ, "QUEUE_DIR": self.queue_dir,
            "TARGETS_FILE": str(self.root / "targets.json"),
            "SUBTITLE_PATH": str(self.source), "VIDEO_PATH": str(self.root / "movie.mkv"),
            "LANGUAGE": "en", "PROVIDER": "embeddedsubtitles", "SERIES_ID": "",
            "MEDIA_ID": "", "MEDIA_TYPE": "movie",
        }

        def run_producer():
            return subprocess.run([sys.executable, "-"], input=payload, env=environment,
                                  text=True, capture_output=True, check=True)

        run_producer()
        job_id = self.snapshot()["pending"][0]["job_id"]
        self.assertEqual(queue.enqueue_translation_jobs(self.queue_dir, self.base, self.targets), [])
        self.jobs.process_once(Mock(side_effect=translation.ProviderUnavailableError("503")))
        run_producer()
        self.assertEqual(self.snapshot()["counts"]["pending"], 0)
        retry_at = self.snapshot()["deferred"][0]["retry_at"]
        self.jobs.process_once(Mock(side_effect=translation.DailyQuotaExceededError("daily quota")), now=retry_at)
        self.assertIn("paused", run_producer().stdout)
        self.assertEqual(self.snapshot()["failed"][0]["job_id"], job_id)
        self.assertEqual(self.snapshot()["counts"]["pending"], 0)
