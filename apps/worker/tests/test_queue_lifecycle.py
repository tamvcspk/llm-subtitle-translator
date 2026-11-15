import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from gst_worker import queue, queue_policy as policy, translation
from gst_worker.intake import receive_script_jobs
from gst_worker.queue import JobQueue
from gst_worker.store import Store


class QueueLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue_dir = str(self.root / "queue")
        for folder in ("pending", "processing", "done", "failed"):  # the shell wrapper creates these
            (Path(self.queue_dir) / folder).mkdir(parents=True)
        self.store = Store(self.root / "app.db")
        self.addCleanup(self.store.close)
        self.jobs = JobQueue(self.store)
        self.jobs.configure({}, ("m",), "gemini")
        self.source = self.root / "movie.en.srt"
        self.source.write_text("subtitle", encoding="utf-8")
        os.utime(self.source, (100, 100))
        self.base = {"subtitle_path": str(self.source), "source_code": "en"}
        self.targets = [{"code": "zh", "language": "Chinese", "enabled": True}]

    def enqueue(self):
        return self.jobs.enqueue(self.base, self.targets)[0]

    def snapshot(self):
        return self.jobs.snapshot()

    def test_a_job_that_keeps_meeting_an_outage_waits_for_the_provider_and_is_never_failed(self):
        job_id = self.enqueue()
        execute = Mock(side_effect=translation.ProviderUnavailableError("503 unavailable"))
        now = 1000
        for _ in range(3):  # failures 1 and 2 only make the job try again soon; the third opens the breaker
            self.assertTrue(self.jobs.process_once(execute, now=now))
            now += 31
        waiting = self.snapshot()["deferred"][0]
        self.assertEqual((waiting["wait_reason"], waiting["error"]), ("provider", "503 unavailable"))
        self.assertEqual(waiting["retry_at"], 1062 + 300)
        self.assertFalse(self.jobs.process_once(execute, now=waiting["retry_at"] - 1))
        self.assertEqual((execute.call_count, self.snapshot()["counts"]["failed"]), (3, 0))

        def finish(job, update_status):  # the rest is over: one trial call, and it works
            update_status("Translating")
            self.assertEqual(self.snapshot()["processing"][0]["stage"], "Translating")
            return "translated"

        self.assertTrue(self.jobs.process_once(finish, now=waiting["retry_at"]))
        self.assertEqual(self.snapshot()["counts"]["done"], 1)
        self.assertTrue(self.jobs.delete("done", job_id))
        self.assertEqual(self.snapshot()["total"], 0)

    def test_a_failed_job_can_be_retried_by_the_admin_with_a_fresh_start(self):
        job_id = self.enqueue()
        self.jobs.process_once(Mock(side_effect=RuntimeError("the source is broken")))
        self.assertEqual(self.snapshot()["counts"]["failed"], 1)
        self.assertTrue(self.jobs.retry(job_id))
        pending = self.snapshot()["pending"][0]
        for field in ("provider_retry_count", "retry_at", "deferred_reason", "last_error", "error"):
            self.assertNotIn(field, pending)
        self.assertTrue(self.jobs.process_once(lambda job, status: "translated"))
        self.assertEqual(self.snapshot()["counts"]["done"], 1)

    def test_a_spent_quota_survives_a_restart_and_no_job_is_lost_or_refused(self):
        job_id = self.enqueue()
        execute = Mock(side_effect=translation.DailyQuotaExceededError("429 daily quota"))
        self.assertTrue(self.jobs.process_once(execute, now=1000))
        reset = policy.daily_quota_reset_at(1000)

        restarted = JobQueue(self.store)
        restarted.configure({}, ("m",), "gemini")
        self.assertFalse(restarted.process_once(execute, now=1001))
        self.assertEqual(restarted.snapshot()["counts"]["deferred"], 1)
        self.assertEqual(self.enqueue_again(restarted), [])  # the same job is still there: no second one
        self.assertTrue(restarted.process_once(lambda job, status: "translated", now=reset))
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(restarted.snapshot()["counts"]["done"], 1)
        self.assertEqual(restarted.jobs.get(job_id)["state"], "done")

    def enqueue_again(self, jobs):
        return jobs.enqueue(self.base, self.targets)

    def test_interrupted_execution_recovers_through_the_same_queue(self):
        self.enqueue()

        def interrupt(job, update_status):
            update_status("Sending batches")
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.jobs.process_once(interrupt)
        self.assertEqual(self.snapshot()["counts"]["processing"], 1)
        restarted = JobQueue(self.store)
        restarted.configure({}, ("m",), "gemini")
        restarted.recover()
        self.assertEqual(self.snapshot()["pending"][0]["stage"], "Recovered after service restart")
        self.assertTrue(restarted.process_once(lambda job, status: "translated"))
        self.assertEqual(self.snapshot()["counts"]["done"], 1)

    def test_bazarr_embedded_python_drops_files_the_gate_turns_into_jobs(self):
        # Execute the producer payload without requiring Bash on Windows.
        script = (Path(__file__).resolve().parents[3] / "bazarr-postprocess" / "gst_enqueue.sh").read_text(encoding="utf-8")
        payload = script.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        (self.root / "targets.json").write_text(
            '{"source_languages": [{"code": "en", "language": "English"}], "target_languages": ['
            '{"code": "zh", "language": "Chinese", "enabled": true}]}', encoding="utf-8")
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
        self.assertEqual(self.snapshot()["total"], 0)  # a file in the gate is not a job yet
        self.assertEqual(receive_script_jobs(self.jobs, self.queue_dir, "auto"), {"queued": 1, "held": 0, "dropped": 0})
        job = self.snapshot()["pending"][0]
        self.assertEqual((job["origin"], job["prio"]), ("script", policy.AUTO))  # the script's jobs are the auto class
        self.assertEqual(list((Path(self.queue_dir) / "pending").glob("*.json")), [])

        run_producer()  # Bazarr runs the script again for the same item
        self.assertEqual(receive_script_jobs(self.jobs, self.queue_dir, "auto"), {"queued": 0, "held": 0, "dropped": 1})
        self.assertEqual(self.snapshot()["counts"]["pending"], 1)  # still one job

        self.jobs.process_once(Mock(side_effect=translation.DailyQuotaExceededError("daily quota")), now=1000)
        self.assertIn("queued", run_producer().stdout)  # a spent quota no longer turns the script away (NFR-1)
        receive_script_jobs(self.jobs, self.queue_dir, "auto")
        self.assertEqual(self.snapshot()["counts"]["deferred"], 1)
        self.assertEqual(self.snapshot()["counts"]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
