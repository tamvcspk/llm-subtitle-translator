import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import worker
from gst_worker import queue, queue_policy, translation

PRIMARY = "gemini-3.8-flash"
FALLBACK = "gemini-3.5-flash-lite"
MODELS = (PRIMARY, FALLBACK)


class ModelPolicyTests(unittest.TestCase):
    def test_configured_models_drop_empty_and_duplicate_fallback(self):
        self.assertEqual(queue_policy.configured_models(PRIMARY, FALLBACK), MODELS)
        self.assertEqual(queue_policy.configured_models(PRIMARY, ""), (PRIMARY,))
        self.assertEqual(queue_policy.configured_models(PRIMARY, PRIMARY), (PRIMARY,))

    def test_select_model_prefers_primary_unless_fallback_requested_or_primary_paused(self):
        self.assertEqual(queue_policy.select_model(MODELS, set()), PRIMARY)
        self.assertEqual(queue_policy.select_model(MODELS, set(), prefer_fallback=True), FALLBACK)
        self.assertEqual(queue_policy.select_model(MODELS, {PRIMARY}), FALLBACK)
        self.assertEqual(queue_policy.select_model(MODELS, {FALLBACK}, prefer_fallback=True), PRIMARY)
        self.assertIsNone(queue_policy.select_model(MODELS, {PRIMARY, FALLBACK}))

    def test_provider_retry_backs_off_then_hands_over_to_fallback(self):
        delays = [queue_policy.provider_retry_decision(count, 0).retry_at for count in range(4)]
        self.assertEqual(delays, [300, 900, 1800, 3600])
        self.assertEqual(queue_policy.provider_retry_decision(4, 0, fallback_available=True).state, "fallback")
        self.assertEqual(queue_policy.provider_retry_decision(4, 0).state, "failed")


class ExhaustedTranslatorRetryTests(unittest.TestCase):
    def run_with_output(self, stdout):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Movie.en.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            result = type("Result", (), {"returncode": 130, "stdout": stdout, "stderr": ""})()
            with patch("gst_worker.translation.subprocess.run", return_value=result):
                translation.run_translation(
                    {"subtitle_path": str(subtitle), "output_path": str(Path(tmp) / "Movie.zh.srt"), "target_code": "zh"},
                    "",
                    {"gst_batch_size": 500, "gst_retry_batch_size": 0},
                )

    def test_content_error_that_stopped_gst_fails_even_after_an_earlier_503(self):
        with self.assertRaisesRegex(RuntimeError, "upstream retry limit") as raised:
            self.run_with_output(
                "503 UNAVAILABLE. Model is overloaded. Pausing for 60 seconds...\n"
                "Stopping script due to reaching 3 consecutive errors to prevent API quota waste. "
                "Last error: Expected 300 lines, got 307."
            )
        self.assertNotIsInstance(raised.exception, translation.ProviderUnavailableError)

    def test_overload_retry_exhaustion_is_deferred(self):
        with self.assertRaises(translation.ProviderUnavailableError):
            self.run_with_output("Model is still overloaded after 3 attempts. Aborting. Last error: 503")


class ModelFallbackQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue_dir = str(self.root / "queue")
        self.jobs = queue.JobQueue(self.queue_dir)

    def enqueue(self, name, mtime=100):
        source = self.root / f"{name}.en.srt"
        source.write_text("subtitle", encoding="utf-8")
        os.utime(source, (100, 100))
        path = Path(queue.enqueue_translation_jobs(
            self.queue_dir, {"subtitle_path": str(source), "source_code": "en"},
            [{"code": "zh", "language": "Chinese", "enabled": True}],
        )[0])
        os.utime(path, (mtime, mtime))
        return path.stem

    def process(self, execute, now):
        with patch("gst_worker.queue.time.time", return_value=now):
            return self.jobs.process_once(execute, now=now, models=MODELS)

    def job(self, state, job_id):
        return json.loads((Path(self.queue_dir) / state / f"{job_id}.json").read_text(encoding="utf-8"))

    def test_primary_outage_backs_off_then_finishes_on_fallback(self):
        job_id = self.enqueue("movie")
        used = []

        def execute(job, update_status):
            used.append(job["gst_model"])
            if job["gst_model"] == PRIMARY:
                raise translation.ProviderUnavailableError("503 UNAVAILABLE")
            return "translated"

        now = 1000
        for delay in (300, 900, 1800, 3600):
            self.assertTrue(self.process(execute, now))
            self.assertEqual(self.job("deferred", job_id)["retry_at"], now + delay)
            now += delay
        self.assertTrue(self.process(execute, now))
        self.assertTrue(self.job("pending", job_id)["use_fallback_model"])
        self.assertTrue(self.process(execute, now))

        self.assertEqual(used, [PRIMARY] * 5 + [FALLBACK])
        done = self.job("done", job_id)
        self.assertEqual(done["gst_model"], FALLBACK)
        for field in ("use_fallback_model", "provider_retry_count", "last_error"):
            self.assertNotIn(field, done)

    def test_fallback_outage_fails_without_another_cycle(self):
        job_id = self.enqueue("movie")
        path = Path(self.queue_dir) / "pending" / f"{job_id}.json"
        path.write_text(json.dumps({**self.job("pending", job_id), "provider_retry_count": 4, "use_fallback_model": True}), encoding="utf-8")
        execute = Mock(side_effect=translation.ProviderUnavailableError("503 UNAVAILABLE"))

        self.assertTrue(self.process(execute, 1000))

        self.assertEqual(execute.call_args.args[0]["gst_model"], FALLBACK)
        self.assertTrue((Path(self.queue_dir) / "failed" / f"{job_id}.json").exists())

    def test_waiting_out_an_outage_holds_other_jobs(self):
        first = self.enqueue("first", mtime=100)
        second = self.enqueue("second", mtime=200)
        execute = Mock(side_effect=[translation.ProviderUnavailableError("503"), "translated", "translated"])

        self.assertTrue(self.process(execute, 1000))
        self.assertFalse(self.process(execute, 1299))
        self.assertEqual(execute.call_count, 1)

        self.assertTrue(self.process(execute, 1300))
        self.assertTrue(self.process(execute, 1300))
        self.assertEqual([call.args[0]["job_id"] for call in execute.call_args_list], [first, second, first])

    def test_daily_quota_moves_work_to_fallback_until_both_models_are_exhausted(self):
        first = self.enqueue("first", mtime=100)
        second = self.enqueue("second", mtime=200)
        execute = Mock(side_effect=[
            translation.DailyQuotaExceededError("429 daily quota"),
            translation.DailyQuotaExceededError("429 daily quota"),
        ])

        self.assertTrue(self.process(execute, 1000))
        self.assertIsNone(queue.daily_quota_pause_until(self.queue_dir, 1001))
        self.assertEqual(queue.model_quota_pauses(self.queue_dir, 1001), {PRIMARY: 87_400})
        self.assertEqual(self.job("pending", first)["last_error"], "429 daily quota")

        self.assertTrue(self.process(execute, 5000))
        self.assertEqual([call.args[0]["gst_model"] for call in execute.call_args_list], [PRIMARY, FALLBACK])
        self.assertEqual(queue.daily_quota_pause_until(self.queue_dir, 5001), 87_400)
        self.assertEqual(queue.queue_snapshot(self.queue_dir)["counts"]["failed"], 2)
        self.assertTrue((Path(self.queue_dir) / "failed" / f"{second}.json").exists())

        with patch("gst_worker.queue.time.time", return_value=87_400):
            self.assertTrue(queue.retry_failed_job(self.queue_dir, first))
        self.assertTrue(self.process(lambda job, update: job["gst_model"], 87_400))
        self.assertEqual(self.job("done", first)["gst_model"], PRIMARY)

    def test_switching_fallback_to_a_model_with_quota_lifts_the_pause(self):
        first = self.enqueue("first")
        execute = Mock(side_effect=translation.DailyQuotaExceededError("429 daily quota"))
        self.assertTrue(self.process(execute, 1000))
        self.assertTrue(self.process(execute, 1000))
        self.assertEqual(queue.daily_quota_pause_until(self.queue_dir, 1001), 87_400)

        fresh = "gemini-3.6-flash-lite"
        with patch("gst_worker.queue.time.time", return_value=2000):
            # Unchanged models keep the pause; a new model with quota left lifts it.
            self.assertEqual(queue.sync_daily_quota_pause(self.queue_dir, MODELS), 87_400)
            self.assertIsNone(queue.sync_daily_quota_pause(self.queue_dir, (PRIMARY, fresh)))
            self.assertIsNone(queue.queue_snapshot(self.queue_dir)["daily_quota_pause"])
            self.assertTrue(queue.retry_failed_job(self.queue_dir, first))
            self.assertTrue(self.jobs.process_once(lambda job, update: "translated", now=2000, models=(PRIMARY, fresh)))
        self.assertEqual(self.job("done", first)["gst_model"], fresh)

        # Switching back to two exhausted models restores the pause until the earliest expiry.
        self.assertEqual(queue.sync_daily_quota_pause(self.queue_dir, MODELS, 3000), 87_400)

    def test_settings_update_through_worker_lifts_the_pause(self):
        execute = Mock(side_effect=translation.DailyQuotaExceededError("429 daily quota"))
        self.enqueue("first")
        self.assertTrue(self.process(execute, 1000))
        self.assertTrue(self.process(execute, 1000))
        with patch("gst_worker.queue.time.time", return_value=2000):
            qw = worker.QueueWorker(self.queue_dir, {"gst_model": PRIMARY, "gst_fallback_model": FALLBACK}, None, None)
            self.assertIsNotNone(queue.daily_quota_pause_until(self.queue_dir))
            qw.apply_settings({"gst_model": PRIMARY, "gst_fallback_model": "gemini-3.6-flash-lite"})
            self.assertIsNone(queue.daily_quota_pause_until(self.queue_dir))


class ProcessJobModelTests(unittest.TestCase):
    def test_process_job_runs_the_model_selected_by_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Movie.en.srt"
            subtitle.write_text("subtitle", encoding="utf-8")
            job = {"subtitle_path": str(subtitle), "source_code": "en", "target_code": "zh", "gst_model": FALLBACK}
            settings = {"gst_model": PRIMARY, "bazarr_url": "", "bazarr_api_key": "", "tmdb_api_key": ""}
            with patch("worker.build_tmdb_description", return_value=""), \
                    patch("worker.refresh_bazarr"), \
                    patch.object(worker.DEFAULT_ATTEMPT, "run", return_value="translated") as run:
                worker.process_job(job, settings, None, None)

            self.assertEqual(run.call_args.args[2]["gst_model"], FALLBACK)
            self.assertEqual(settings["gst_model"], PRIMARY)


if __name__ == "__main__":
    unittest.main()
