import json
import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import worker
from gst_worker import (
    backups as gst_backups,
    bazarr as gst_bazarr,
    config as gst_config,
    connection_tests as gst_connection_tests,
    gemini as gst_gemini,
    http as gst_http,
    logs as gst_logs,
    queue as gst_queue,
    subtitles as gst_subtitles,
    tmdb as gst_tmdb,
    translation as gst_translation,
)


class FakeHTTP:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params or {}, headers or {}))
        key = (url, tuple(sorted((params or {}).items())))
        return self.responses[key]

    def request_json(self, method, url, params=None, headers=None):
        self.calls.append((method, url, params or {}, headers or {}))
        return {"ok": True}




class TestWorkerQueueTests(unittest.TestCase):
    def test_job_should_skip_non_embedded_same_language_and_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Episode.ja.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            output = Path(tmp) / "Episode.zh.srt"
            output.write_text("translated", encoding="utf-8")

            self.assertTrue(gst_queue.should_skip_job({"provider": "opensubtitles", "language": "ja", "subtitle_path": str(subtitle)}))
            self.assertTrue(
                gst_queue.should_skip_job(
                    {"provider": "embeddedsubtitles", "source_code": "ja", "target_code": "ja", "subtitle_path": str(subtitle)}
                )
            )
            self.assertTrue(gst_queue.should_skip_job({"provider": "embeddedsubtitles", "language": "ja", "subtitle_path": str(subtitle), "target_code": "zh"}))
            output.unlink()
            self.assertFalse(gst_queue.should_skip_job({"provider": "embeddedsubtitles", "language": "ja", "subtitle_path": str(subtitle), "target_code": "zh"}))


    def test_job_should_skip_when_configured_target_output_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Episode.en.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            output = Path(tmp) / "Episode.zt.srt"
            output.write_text("translated", encoding="utf-8")

            self.assertTrue(
                gst_queue.should_skip_job(
                    {
                        "provider": "embeddedsubtitles",
                        "source_code": "en",
                        "subtitle_path": str(subtitle),
                        "target_code": "zt",
                        "target_language": "Traditional Chinese",
                    }
                )
            )

            output.unlink()
            self.assertFalse(
                gst_queue.should_skip_job(
                    {
                        "provider": "embeddedsubtitles",
                        "source_code": "en",
                        "subtitle_path": str(subtitle),
                        "target_code": "zt",
                        "target_language": "Traditional Chinese",
                    }
                )
            )


    def test_enqueue_translation_jobs_for_enabled_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            subtitle = Path(tmp) / "Movie.ja.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            (Path(tmp) / "Movie.zh.srt").write_text("existing", encoding="utf-8")

            created = gst_queue.enqueue_translation_jobs(
                queue_dir=str(queue_dir),
                base_job={
                    "video_path": str(Path(tmp) / "Movie.mkv"),
                    "subtitle_path": str(subtitle),
                    "provider": "embeddedsubtitles",
                    "source_code": "ja",
                    "source_language": "Japanese",
                    "media_id": "781",
                    "media_type": "movie",
                },
                targets=[
                    {"code": "zh", "language": "Simplified Chinese", "enabled": True},
                    {"code": "zt", "language": "Traditional Chinese", "enabled": True},
                ],
            )

            self.assertEqual(len(created), 1)
            job = json.loads(Path(created[0]).read_text(encoding="utf-8"))
            self.assertEqual(job["target_code"], "zt")
            self.assertEqual(job["target_language"], "Traditional Chinese")
            self.assertEqual(job["output_path"], str(Path(tmp) / "Movie.zt.srt"))
            self.assertEqual(job["source_code"], "ja")
            self.assertEqual(job["source_language"], "Japanese")


    def test_enqueue_translation_jobs_retries_existing_failed_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            subtitle = Path(tmp) / "Episode.en.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            base_job = {
                "subtitle_path": str(subtitle),
                "provider": "gemini-console",
                "source_code": "en",
                "source_language": "English",
                "media_type": "episode",
            }
            targets = [{"code": "zh", "language": "Simplified Chinese", "enabled": True}]

            [pending_path_string] = gst_queue.enqueue_translation_jobs(str(queue_dir), base_job, targets)
            pending_path = Path(pending_path_string)
            failed_path = queue_dir / "failed" / pending_path.name
            pending_path.replace(failed_path)
            failed_path.with_suffix(".error").write_text("gst failed with exit 1", encoding="utf-8")

            created = gst_queue.enqueue_translation_jobs(str(queue_dir), base_job, targets)
            snapshot = gst_queue.queue_snapshot(str(queue_dir))

            self.assertEqual(created, [str(pending_path)])
            self.assertEqual(snapshot["counts"]["pending"], 1)
            self.assertEqual(snapshot["counts"]["failed"], 0)
            self.assertFalse(failed_path.with_suffix(".error").exists())


    def test_enqueue_translation_jobs_skips_same_source_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            subtitle = Path(tmp) / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

            created = gst_queue.enqueue_translation_jobs(
                queue_dir=str(queue_dir),
                base_job={
                    "video_path": str(Path(tmp) / "Movie.mkv"),
                    "subtitle_path": str(subtitle),
                    "provider": "embeddedsubtitles",
                    "source_code": "zh",
                    "source_language": "Simplified Chinese",
                    "media_id": "781",
                    "media_type": "movie",
                },
                targets=[
                    {"code": "zh", "language": "Simplified Chinese", "enabled": True},
                    {"code": "en", "language": "English", "enabled": True},
                ],
            )

            self.assertEqual(len(created), 1)
            job = json.loads(Path(created[0]).read_text(encoding="utf-8"))
            self.assertEqual(job["target_code"], "en")
            self.assertEqual(job["output_path"], str(Path(tmp) / "Movie.en.srt"))


    def test_queue_snapshot_counts_job_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp)
            for state in ("pending", "processing", "done", "failed"):
                (queue_dir / state).mkdir()
            (queue_dir / "pending" / "a.json").write_text('{"job_id":"a"}', encoding="utf-8")
            (queue_dir / "failed" / "b.json").write_text('{"job_id":"b"}', encoding="utf-8")
            (queue_dir / "failed" / "b.error").write_text("boom", encoding="utf-8")

            snapshot = gst_queue.queue_snapshot(str(queue_dir))

            self.assertEqual(snapshot["counts"]["pending"], 1)
            self.assertEqual(snapshot["counts"]["failed"], 1)
            self.assertEqual(snapshot["failed"][0]["error"], "boom")


    def test_cancel_failed_job_removes_job_and_error_without_retrying(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp)
            failed_dir = queue_dir / "failed"
            failed_dir.mkdir()
            job = failed_dir / "cancel-me.json"
            error = failed_dir / "cancel-me.error"
            job.write_text('{"job_id":"cancel-me"}', encoding="utf-8")
            error.write_text("quota exhausted", encoding="utf-8")

            self.assertTrue(gst_queue.cancel_failed_job(str(queue_dir), "cancel-me"))
            self.assertFalse(job.exists())
            self.assertFalse(error.exists())
            self.assertFalse((queue_dir / "pending" / "cancel-me.json").exists())
            self.assertFalse(gst_queue.cancel_failed_job(str(queue_dir), "cancel-me"))


    def test_retry_failed_job_starts_a_fresh_provider_retry_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp)
            failed_dir = queue_dir / "failed"
            failed_dir.mkdir()
            failed = failed_dir / "retry-me.json"
            failed.write_text(
                json.dumps(
                    {
                        "job_id": "retry-me",
                        "provider_retry_count": 3,
                        "retry_at": 1_234,
                        "deferred_reason": "provider-unavailable",
                        "last_error": "503 UNAVAILABLE",
                        "use_fallback_model": True,
                    }
                ),
                encoding="utf-8",
            )
            failed.with_suffix(".error").write_text("503 UNAVAILABLE", encoding="utf-8")

            self.assertTrue(gst_queue.retry_failed_job(str(queue_dir), "retry-me"))

            pending = json.loads((queue_dir / "pending" / "retry-me.json").read_text(encoding="utf-8"))
            self.assertNotIn("provider_retry_count", pending)
            self.assertNotIn("retry_at", pending)
            self.assertNotIn("deferred_reason", pending)
            self.assertNotIn("last_error", pending)
            self.assertNotIn("use_fallback_model", pending)
            self.assertFalse((queue_dir / "pending" / "retry-me.error").exists())


    def test_queue_worker_waits_for_settle_window_before_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            for state in ("pending", "processing", "done", "failed"):
                (queue_dir / state).mkdir(parents=True)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            os.utime(subtitle, (1_000, 1_000))
            job = {
                "job_id": "settle-race",
                "created_at": 1_000,
                "subtitle_path": str(subtitle),
                "output_path": str(output),
                "source_code": "en",
                "target_code": "zh",
                "provider": "embeddedsubtitles",
            }
            (queue_dir / "pending" / "settle-race.json").write_text(json.dumps(job), encoding="utf-8")
            queue_worker = worker.QueueWorker(
                str(queue_dir),
                {"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "", "tmdb_api_key": "", "job_settle_seconds": 120},
                gst_http.MemoryCache(),
                FakeHTTP({}),
            )

            self.assertFalse(queue_worker.process_once(now=1_060))
            self.assertTrue((queue_dir / "pending" / "settle-race.json").exists())

            output.write_text("embedded zh subtitle", encoding="utf-8")
            self.assertTrue(queue_worker.process_once(now=1_121))
            self.assertTrue((queue_dir / "done" / "settle-race.json").exists())
            self.assertEqual(output.read_text(encoding="utf-8"), "embedded zh subtitle")


    def test_queue_worker_processes_old_subtitle_without_waiting_for_new_queue_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            gst_queue.ensure_queue_dirs(str(queue_dir))
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            os.utime(subtitle, (1_000, 1_000))
            job = {
                "job_id": "old-subtitle",
                "created_at": 1_119,
                "subtitle_path": str(subtitle),
                "output_path": str(output),
                "source_code": "en",
                "target_code": "zh",
                "provider": "embeddedsubtitles",
            }
            (queue_dir / "pending" / "old-subtitle.json").write_text(json.dumps(job), encoding="utf-8")
            queue_worker = worker.QueueWorker(
                str(queue_dir),
                {"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "", "tmdb_api_key": "", "job_settle_seconds": 120},
                gst_http.MemoryCache(),
                FakeHTTP({}),
            )

            output.write_text("embedded zh subtitle", encoding="utf-8")
            self.assertTrue(queue_worker.process_once(now=1_120))
            self.assertTrue((queue_dir / "done" / "old-subtitle.json").exists())


    def test_queue_worker_defers_503_and_retries_only_after_retry_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            gst_queue.ensure_queue_dirs(str(queue_dir))
            job = {
                "job_id": "overloaded",
                "created_at": 1_000,
                "subtitle_path": str(Path(tmp) / "Movie.en.srt"),
                "output_path": str(Path(tmp) / "Movie.zh.srt"),
                "source_code": "en",
                "target_code": "zh",
            }
            (queue_dir / "pending" / "overloaded.json").write_text(json.dumps(job), encoding="utf-8")
            queue_worker = worker.QueueWorker(
                str(queue_dir),
                {"job_settle_seconds": 0},
                gst_http.MemoryCache(),
                FakeHTTP({}),
            )

            with patch(
                "worker.process_job",
                side_effect=[gst_translation.ProviderUnavailableError("503 UNAVAILABLE"), "translated"],
            ) as process_job, patch("worker.time.time", return_value=1_300):
                self.assertTrue(queue_worker.process_once(now=1_000))
                deferred_path = queue_dir / "deferred" / "overloaded.json"
                self.assertTrue(deferred_path.exists())
                deferred = json.loads(deferred_path.read_text(encoding="utf-8"))
                self.assertEqual(deferred["retry_at"], 1_600)
                self.assertEqual(deferred["provider_retry_count"], 1)

                self.assertFalse(queue_worker.process_once(now=1_599))
                self.assertTrue(queue_worker.process_once(now=1_600))

            self.assertEqual(process_job.call_count, 2)
            self.assertTrue((queue_dir / "done" / "overloaded.json").exists())


    def test_processing_snapshot_includes_runtime_status_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            gst_queue.ensure_queue_dirs(str(queue_dir))
            subtitle = Path(tmp) / "Movie.en.srt"
            output = Path(tmp) / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            subtitle.with_suffix(".progress").write_text(json.dumps({"line": 42}), encoding="utf-8")
            output.with_name("Movie.zh.partial.srt").write_text("partial", encoding="utf-8")
            job = {
                "job_id": "active",
                "subtitle_path": str(subtitle),
                "output_path": str(output),
                "stage": "Sending subtitle batches to Gemini",
                "started_at": 1000,
            }
            (queue_dir / "processing" / "active.json").write_text(json.dumps(job), encoding="utf-8")

            snapshot = gst_queue.queue_snapshot(str(queue_dir))

            active = snapshot["processing"][0]
            self.assertEqual(active["stage"], "Sending subtitle batches to Gemini")
            self.assertEqual(active["progress_checkpoint"], 42)
            self.assertEqual(active["partial_bytes"], 7)


    def test_queue_worker_recovers_interrupted_processing_job_on_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            gst_queue.ensure_queue_dirs(str(queue_dir))
            interrupted = {"job_id": "interrupted", "stage": "Sending subtitle batches to Gemini"}
            (queue_dir / "processing" / "interrupted.json").write_text(json.dumps(interrupted), encoding="utf-8")

            worker.QueueWorker(str(queue_dir), {"job_settle_seconds": 0}, gst_http.MemoryCache(), FakeHTTP({}))

            recovered_path = queue_dir / "pending" / "interrupted.json"
            self.assertTrue(recovered_path.exists())
            self.assertFalse((queue_dir / "processing" / "interrupted.json").exists())
            recovered = json.loads(recovered_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["stage"], "Recovered after service restart")


    def test_daily_quota_blocks_enqueue_and_retry_until_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            gst_queue.ensure_queue_dirs(str(queue_dir))
            source = Path(tmp) / "movie.en.srt"
            source.write_text("subtitle", encoding="utf-8")
            pause = queue_dir / "provider-pause.json"
            pause.write_text(json.dumps({"reason": "daily-quota", "retry_at": 2000}), encoding="utf-8")
            failed = queue_dir / "failed" / "retry.json"
            failed.write_text('{"job_id":"retry"}', encoding="utf-8")
            base = {"subtitle_path": str(source), "source_code": "en"}
            targets = [{"code": "zh", "language": "Chinese", "enabled": True}]
            with patch("gst_worker.queue.time.time", return_value=1000):
                self.assertEqual(
                    gst_queue.queue_snapshot(str(queue_dir))["daily_quota_pause"],
                    {"retry_at": 2000},
                )
                with self.assertRaisesRegex(ValueError, "Daily Gemini quota"):
                    gst_queue.enqueue_translation_jobs(str(queue_dir), base, targets)
                with self.assertRaisesRegex(ValueError, "Daily Gemini quota"):
                    gst_queue.retry_failed_job(str(queue_dir), "retry")
            self.assertTrue(failed.exists())
            self.assertEqual(list((queue_dir / "pending").glob("*.json")), [])
            with patch("gst_worker.queue.time.time", return_value=2001):
                self.assertIsNone(gst_queue.queue_snapshot(str(queue_dir))["daily_quota_pause"])
                self.assertEqual(len(gst_queue.enqueue_translation_jobs(str(queue_dir), base, targets)), 1)
                self.assertTrue(gst_queue.retry_failed_job(str(queue_dir), "retry"))


    def test_cancel_pending_preserves_processing_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            gst_queue.ensure_queue_dirs(tmp)
            pending = Path(tmp) / "pending" / "cancel.json"
            processing = Path(tmp) / "processing" / "active.json"
            pending.write_text("{}", encoding="utf-8")
            processing.write_text("{}", encoding="utf-8")
            self.assertTrue(gst_queue.delete_queue_job(tmp, "pending", "cancel"))
            self.assertFalse(gst_queue.delete_queue_job(tmp, "pending", "active"))
            self.assertFalse(pending.exists())
            self.assertTrue(processing.exists())


    def test_queue_worker_daily_quota_pauses_other_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            gst_queue.ensure_queue_dirs(str(queue_dir))
            for job_id in ("first", "second"):
                job = {
                    "job_id": job_id,
                    "created_at": 1_000,
                    "subtitle_path": str(Path(tmp) / f"{job_id}.en.srt"),
                    "output_path": str(Path(tmp) / f"{job_id}.zh.srt"),
                    "source_code": "en",
                    "target_code": "zh",
                }
                (queue_dir / "pending" / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")
            queue_worker = worker.QueueWorker(
                str(queue_dir),
                {"job_settle_seconds": 0},
                gst_http.MemoryCache(),
                FakeHTTP({}),
            )

            with patch(
                "worker.process_job",
                side_effect=gst_translation.DailyQuotaExceededError("429 daily quota exhausted"),
            ) as process_job, patch("worker.time.time", return_value=1_000):
                self.assertTrue(queue_worker.process_once(now=1_000))
                self.assertFalse(queue_worker.process_once(now=1_001))

            self.assertEqual(process_job.call_count, 1)
            pause = json.loads((queue_dir / "provider-pause.json").read_text(encoding="utf-8"))
            self.assertEqual(pause["retry_at"], 87_400)
            self.assertEqual(len(list((queue_dir / "deferred").glob("*.json"))), 0)
            self.assertEqual(len(list((queue_dir / "pending").glob("*.json"))), 0)
            self.assertEqual(len(list((queue_dir / "failed").glob("*.json"))), 2)


    def test_queue_worker_fails_503_after_four_delayed_retries_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            gst_queue.ensure_queue_dirs(str(queue_dir))
            job = {
                "job_id": "still-overloaded",
                "created_at": 1_000,
                "subtitle_path": str(Path(tmp) / "Movie.en.srt"),
                "output_path": str(Path(tmp) / "Movie.zh.srt"),
                "source_code": "en",
                "target_code": "zh",
                "provider_retry_count": 4,
            }
            pending_path = queue_dir / "pending" / "still-overloaded.json"
            pending_path.write_text(json.dumps(job), encoding="utf-8")
            queue_worker = worker.QueueWorker(
                str(queue_dir),
                {"job_settle_seconds": 0},
                gst_http.MemoryCache(),
                FakeHTTP({}),
            )

            with patch(
                "worker.process_job",
                side_effect=gst_translation.ProviderUnavailableError("503 UNAVAILABLE"),
            ):
                self.assertTrue(queue_worker.process_once(now=1_000))

            self.assertTrue((queue_dir / "failed" / "still-overloaded.json").exists())
            self.assertFalse((queue_dir / "deferred" / "still-overloaded.json").exists())


    def test_run_translation_defers_gemini_503_that_outlasts_translator_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            result = type(
                "Result",
                (),
                {
                    "returncode": 130,
                    "stdout": (
                        "Stopping script due to reaching 3 consecutive errors. "
                        "Last error: 503 UNAVAILABLE. This model is currently experiencing high demand."
                    ),
                    "stderr": "",
                },
            )()

            with patch("gst_worker.translation.subprocess.run", return_value=result) as run:
                with self.assertRaises(gst_translation.ProviderUnavailableError):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {"gemini_api_key": "secret", "gst_batch_size": 500, "gst_retry_batch_size": 300},
                    )

            self.assertEqual(run.call_count, 1)

    def test_exhausted_translator_retries_fail_without_queue_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Movie.en.srt"
            source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            queue = gst_queue.JobQueue(str(root / "queue"))
            job = {"subtitle_path": str(source), "target_code": "zh", "job_id": "exhausted"}
            (queue.queue_dir / "pending" / "exhausted.json").write_text(json.dumps(job), encoding="utf-8")
            result = type("Result", (), {"returncode": 130, "stdout":
                "Stopping script due to reaching 3 consecutive errors to prevent API quota waste. "
                "Last error: Expected 300 lines, got 307. key=test-private-key", "stderr": ""})()
            settings = {"gemini_api_key": "test-private-key"}
            with patch("gst_worker.translation.subprocess.run", return_value=result) as run:
                with self.assertLogs(level="ERROR") as logs:
                    queue.process_once(lambda job, update: gst_translation.run_translation(
                        job, "", settings))
                self.assertTrue((queue.queue_dir / "failed" / "exhausted.json").exists())
                self.assertFalse(queue.process_once(lambda job, update: self.fail("Unexpected retry"), now=time.time() + 10000))
            self.assertEqual(run.call_count, 1)
            self.assertIn("Expected 300 lines, got 307", "\n".join(logs.output))
            self.assertNotIn("test-private-key", "\n".join(logs.output))


    def test_run_translation_does_not_retry_daily_quota_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            result = type(
                "Result",
                (),
                {
                    "returncode": 130,
                    "stdout": (
                        "429 RESOURCE_EXHAUSTED: Quota exceeded for metric "
                        "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
                    ),
                    "stderr": "",
                },
            )()

            with patch("gst_worker.translation.subprocess.run", return_value=result) as run:
                with self.assertRaises(gst_translation.DailyQuotaExceededError):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {"gemini_api_key": "secret", "gst_batch_size": 500, "gst_retry_batch_size": 300},
                    )

            self.assertEqual(run.call_count, 1)


    def test_run_translation_resumes_with_retry_batch_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            partial = root / "Movie.zh.partial.srt"
            progress = root / "Movie.en.progress"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            partial.write_text("already translated chunk", encoding="utf-8")
            progress.write_text('{"line": 701, "input_file": "Movie.en.srt"}', encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                temp_output = Path(command[command.index("-o") + 1])
                temp_output.write_text("finished from resume", encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                status = gst_translation.run_translation(
                    {
                        "subtitle_path": str(subtitle),
                        "output_path": str(output),
                        "target_code": "zh",
                        "target_language": "Simplified Chinese",
                    },
                    "",
                    {"gemini_api_key": "secret", "gst_batch_size": 1000, "gst_retry_batch_size": 500},
                )

            self.assertEqual(status, "translated")
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][commands[0].index("--batch-size") + 1], "500")
            self.assertEqual(output.read_text(encoding="utf-8"), "finished from resume")


    def test_run_translation_does_not_retry_unknown_resume_exit_130(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            partial = root / "Movie.zh.partial.srt"
            progress = root / "Movie.en.progress"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            partial.write_text("already translated chunk", encoding="utf-8")
            progress.write_text('{"line": 701, "input_file": "Movie.en.srt"}', encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                self.assertTrue(partial.exists())
                self.assertTrue(progress.exists())
                return type("Result", (), {"returncode": 130, "stdout": "", "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {
                            "gemini_api_key": "secret",
                            "gst_batch_size": 500,
                            "gst_retry_batch_size": 300,
                        },
                    )

            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][commands[0].index("--batch-size") + 1], "300")


