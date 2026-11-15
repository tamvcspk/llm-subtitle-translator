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
from subserver import queue_policy
from subserver.jobs import FROM_LEGACY, JobStore
from subserver.queue import JobQueue
from subserver.store import Store
from subserver import (
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

    def request_json(self, method, url, params=None, headers=None, timeout=None):
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


    def queue(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        return JobQueue(store)

    def worker(self, queue_dir, settings, **kwargs):
        store = Store(":memory:")
        self.addCleanup(store.close)
        return worker.QueueWorker(str(queue_dir), settings, gst_http.MemoryCache(), FakeHTTP({}), store=store, **kwargs)

    def drop(self, queue_dir, job):
        """What Bazarr's script does: one JSON file in the gate folder, `pending/`."""
        folder = Path(queue_dir) / "pending"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{job['job_id']}.json").write_text(json.dumps(job), encoding="utf-8")

    def names(self, jobs, state):
        return sorted(job["job_id"] for job in jobs.by_state(*FROM_LEGACY[state]))

    def test_enqueue_translation_jobs_for_enabled_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self.queue()
            subtitle = Path(tmp) / "Movie.ja.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            (Path(tmp) / "Movie.zh.srt").write_text("existing", encoding="utf-8")

            created = queue.enqueue(
                {
                    "video_path": str(Path(tmp) / "Movie.mkv"),
                    "subtitle_path": str(subtitle),
                    "provider": "embeddedsubtitles",
                    "source_code": "ja",
                    "source_language": "Japanese",
                    "media_id": "781",
                    "media_type": "movie",
                },
                [
                    {"code": "zh", "language": "Simplified Chinese", "enabled": True},
                    {"code": "zt", "language": "Traditional Chinese", "enabled": True},
                ],
            )

            self.assertEqual(len(created), 1)
            job = queue.jobs.get(created[0])
            self.assertEqual(job["target_code"], "zt")
            self.assertEqual(job["target_language"], "Traditional Chinese")
            self.assertEqual(job["output_path"], str(Path(tmp) / "Movie.zt.srt"))
            self.assertEqual(job["source_code"], "ja")
            self.assertEqual(job["source_language"], "Japanese")

    def test_enqueue_translation_jobs_retries_existing_failed_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self.queue()
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

            [job_id] = queue.enqueue(base_job, targets)
            queue.jobs.transition(job_id, "failed", error="gst failed with exit 1")

            created = queue.enqueue(base_job, targets)
            snapshot = queue.snapshot()

            self.assertEqual(created, [job_id])
            self.assertEqual(snapshot["counts"]["pending"], 1)
            self.assertEqual(snapshot["counts"]["failed"], 0)
            self.assertNotIn("error", queue.jobs.get(job_id))

    def test_enqueue_translation_jobs_skips_same_source_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self.queue()
            subtitle = Path(tmp) / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

            created = queue.enqueue(
                {
                    "video_path": str(Path(tmp) / "Movie.mkv"),
                    "subtitle_path": str(subtitle),
                    "provider": "embeddedsubtitles",
                    "source_code": "zh",
                    "source_language": "Simplified Chinese",
                    "media_id": "781",
                    "media_type": "movie",
                },
                [
                    {"code": "zh", "language": "Simplified Chinese", "enabled": True},
                    {"code": "en", "language": "English", "enabled": True},
                ],
            )

            self.assertEqual(len(created), 1)
            job = queue.jobs.get(created[0])
            self.assertEqual(job["target_code"], "en")
            self.assertEqual(job["output_path"], str(Path(tmp) / "Movie.en.srt"))

    def test_queue_snapshot_counts_job_states(self):
        queue = self.queue()
        queue.jobs.add({"job_id": "a"}, key="a", job_id="a")
        queue.jobs.add({"job_id": "b"}, key="b", job_id="b")
        queue.jobs.transition("b", "failed", error="boom")

        snapshot = queue.snapshot()

        self.assertEqual(snapshot["counts"]["pending"], 1)
        self.assertEqual(snapshot["counts"]["failed"], 1)
        self.assertEqual(snapshot["failed"][0]["error"], "boom")

    def test_cancel_failed_job_removes_job_without_retrying(self):
        queue = self.queue()
        queue.jobs.add({"job_id": "cancel-me"}, key="k", job_id="cancel-me")
        queue.jobs.transition("cancel-me", "failed", error="quota exhausted")

        self.assertTrue(queue.cancel("cancel-me"))
        self.assertIsNone(queue.jobs.get("cancel-me"))
        self.assertEqual(queue.snapshot()["counts"]["pending"], 0)
        self.assertFalse(queue.cancel("cancel-me"))

    def test_retry_failed_job_starts_a_fresh_provider_retry_cycle(self):
        queue = self.queue()
        queue.jobs.add(
            {
                "job_id": "retry-me",
                "provider_retry_count": 3,
                "retry_at": 1_234,
                "deferred_reason": "provider-unavailable",
                "last_error": "503 UNAVAILABLE",
                "use_fallback_model": True,
            },
            key="k", job_id="retry-me",
        )
        queue.jobs.transition("retry-me", "failed", error="503 UNAVAILABLE")

        self.assertTrue(queue.retry("retry-me"))

        pending = queue.jobs.get("retry-me")
        self.assertEqual(pending["state"], "queued")
        for field in ("provider_retry_count", "retry_at", "deferred_reason", "last_error", "use_fallback_model", "error"):
            self.assertNotIn(field, pending)

    def test_queue_worker_waits_for_settle_window_before_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            os.utime(subtitle, (1_000, 1_000))
            self.drop(queue_dir, {
                "job_id": "settle-race",
                "created_at": 1_000,
                "subtitle_path": str(subtitle),
                "output_path": str(output),
                "source_code": "en",
                "target_code": "zh",
                "provider": "embeddedsubtitles",
            })
            queue_worker = self.worker(
                queue_dir,
                {"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "", "tmdb_api_key": "", "job_settle_seconds": 120},
            )
            jobs = queue_worker.queue.jobs

            self.assertFalse(queue_worker.process_once(now=1_060))
            self.assertEqual(jobs.get("settle-race")["wait_reason"], "settle")  # waiting, and it says why
            self.assertEqual(jobs.get("settle-race")["not_before"], 1_120)

            output.write_text("embedded zh subtitle", encoding="utf-8")
            self.assertTrue(queue_worker.process_once(now=1_121))
            self.assertEqual(jobs.state_of("settle-race"), "skipped")
            self.assertEqual(output.read_text(encoding="utf-8"), "embedded zh subtitle")

    def test_no_settle_window_means_a_subtitle_stamped_slightly_ahead_of_the_clock_still_starts(self):
        """Windows rounds a modification time up, so a file just written can be a few ms "in the future"."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            output.write_text("embedded zh subtitle", encoding="utf-8")
            os.utime(subtitle, (1_000.005, 1_000.005))
            self.drop(queue_dir, {
                "job_id": "ahead", "created_at": 1_000, "subtitle_path": str(subtitle), "output_path": str(output),
                "source_code": "en", "target_code": "zh", "provider": "embeddedsubtitles",
            })
            queue_worker = self.worker(queue_dir, {"job_settle_seconds": 0})

            self.assertTrue(queue_worker.process_once(now=1_000.0))  # the clock is 5 ms behind the file
            self.assertEqual(queue_worker.queue.jobs.state_of("ahead"), "skipped")

    def test_queue_worker_processes_old_subtitle_without_waiting_for_new_queue_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            os.utime(subtitle, (1_000, 1_000))
            self.drop(queue_dir, {
                "job_id": "old-subtitle",
                "created_at": 1_119,
                "subtitle_path": str(subtitle),
                "output_path": str(output),
                "source_code": "en",
                "target_code": "zh",
                "provider": "embeddedsubtitles",
            })
            queue_worker = self.worker(
                queue_dir,
                {"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "", "tmdb_api_key": "", "job_settle_seconds": 120},
            )

            output.write_text("embedded zh subtitle", encoding="utf-8")
            self.assertTrue(queue_worker.process_once(now=1_120))
            self.assertEqual(queue_worker.queue.jobs.state_of("old-subtitle"), "skipped")

    def test_queue_worker_waits_out_a_503_and_retries_when_the_wait_is_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            self.drop(queue_dir, {
                "job_id": "overloaded",
                "created_at": 1_000,
                "subtitle_path": str(Path(tmp) / "Movie.en.srt"),
                "output_path": str(Path(tmp) / "Movie.zh.srt"),
                "source_code": "en",
                "target_code": "zh",
            })
            queue_worker = self.worker(queue_dir, {"job_settle_seconds": 0})
            jobs = queue_worker.queue.jobs

            with patch(
                "worker.process_job",
                side_effect=[gst_translation.ProviderUnavailableError("503 UNAVAILABLE"), "translated"],
            ) as process_job:
                self.assertTrue(queue_worker.process_once(now=1_000))
                waiting = jobs.get("overloaded")
                self.assertEqual((waiting["state"], waiting["wait_reason"]), ("waiting", "rate"))
                self.assertEqual(waiting["not_before"], 1_030)  # below the breaker threshold: soon

                self.assertFalse(queue_worker.process_once(now=1_029))
                self.assertTrue(queue_worker.process_once(now=1_030))

            self.assertEqual(process_job.call_count, 2)
            self.assertEqual(jobs.state_of("overloaded"), "done")

    def test_processing_snapshot_includes_runtime_status_and_cue_progress(self):
        queue = self.queue()
        queue.jobs.add({"job_id": "active", "stage": "Translating 120/300 cues", "progress_done": 120,
                        "progress_total": 300, "started_at": 1000}, key="k", job_id="active")
        queue.jobs.transition("active", "running")

        active = queue.snapshot()["processing"][0]

        self.assertEqual(active["stage"], "Translating 120/300 cues")
        self.assertEqual((active["progress_done"], active["progress_total"]), (120, 300))

    def test_queue_worker_recovers_interrupted_job_on_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(":memory:")
            self.addCleanup(store.close)
            first = JobQueue(store)
            first.jobs.add({"job_id": "interrupted", "stage": "Sending subtitle batches to Gemini"}, key="k", job_id="interrupted")
            first.jobs.transition("interrupted", "running")

            started = worker.QueueWorker(str(Path(tmp) / "queue"), {"job_settle_seconds": 0}, gst_http.MemoryCache(),
                                         FakeHTTP({}), store=store)

            recovered = started.queue.jobs.get("interrupted")
            self.assertEqual((recovered["state"], recovered["attempts"]), ("queued", 1))
            self.assertEqual(recovered["stage"], "Recovered after service restart")

    def test_a_spent_daily_quota_does_not_refuse_enqueue_or_retry_it_makes_them_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self.queue()
            queue.configure({}, ("m",), "gemini")
            source = Path(tmp) / "movie.en.srt"
            source.write_text("subtitle", encoding="utf-8")
            queue.jobs.add({"job_id": "retry"}, key="retry", job_id="retry")
            queue.jobs.transition("retry", "failed", error="x")
            queue.jobs.set_health("gemini", "m", queue_policy.after_quota(queue_policy.Health(), time.time() + 5000))
            base = {"subtitle_path": str(source), "source_code": "en"}
            targets = [{"code": "zh", "language": "Chinese", "enabled": True}]

            self.assertIsNotNone(queue.snapshot()["daily_quota_pause"])
            self.assertEqual(len(queue.enqueue(base, targets)), 1)  # NFR-1: the request is kept, not refused
            self.assertTrue(queue.retry("retry"))

    def test_cancel_pending_preserves_processing_job(self):
        queue = self.queue()
        queue.jobs.add({}, key="a", job_id="cancel")
        queue.jobs.add({}, key="b", job_id="active")
        queue.jobs.transition("active", "running")

        self.assertTrue(queue.delete("pending", "cancel"))
        self.assertFalse(queue.delete("pending", "active"))
        self.assertFalse(queue.delete("processing", "active"))
        self.assertEqual(queue.jobs.state_of("cancel"), "cancelled")
        self.assertEqual(queue.jobs.state_of("active"), "running")

    def test_queue_worker_daily_quota_makes_other_jobs_wait_it_fails_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            for job_id in ("first", "second"):
                self.drop(queue_dir, {
                    "job_id": job_id,
                    "created_at": 1_000,
                    "subtitle_path": str(Path(tmp) / f"{job_id}.en.srt"),
                    "output_path": str(Path(tmp) / f"{job_id}.zh.srt"),
                    "source_code": "en",
                    "target_code": "zh",
                })
            queue_worker = self.worker(queue_dir, {"job_settle_seconds": 0, "gst_model": "m"})
            jobs = queue_worker.queue.jobs

            with patch(
                "worker.process_job",
                side_effect=gst_translation.DailyQuotaExceededError("429 daily quota exhausted"),
            ) as process_job:
                self.assertTrue(queue_worker.process_once(now=1_000))
                self.assertFalse(queue_worker.process_once(now=1_001))

            self.assertEqual(process_job.call_count, 1)
            counts = jobs.counts()
            self.assertEqual((counts["failed"], counts["deferred"], counts["pending"]), (0, 2, 0))
            self.assertEqual({job["wait_reason"] for job in jobs.by_state("waiting")}, {"quota"})
            self.assertEqual(jobs.health_map()[("gemini", "m")].state, "exhausted")
            self.assertFalse((queue_dir / "provider-pause.json").exists())  # the pause lives in the database now

    def test_queue_worker_keeps_a_503_job_waiting_it_does_not_fail_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp) / "queue"
            self.drop(queue_dir, {
                "job_id": "still-overloaded",
                "created_at": 1_000,
                "subtitle_path": str(Path(tmp) / "Movie.en.srt"),
                "output_path": str(Path(tmp) / "Movie.zh.srt"),
                "source_code": "en",
                "target_code": "zh",
            })
            queue_worker = self.worker(queue_dir, {"job_settle_seconds": 0, "gst_model": "m"})
            jobs = queue_worker.queue.jobs

            with patch("worker.process_job", side_effect=gst_translation.ProviderUnavailableError("503 UNAVAILABLE")):
                now = 1_000
                for _ in range(3):  # three failures in a row open the breaker
                    queue_worker.process_once(now=now)
                    now += 31

            job = jobs.get("still-overloaded")
            self.assertEqual((job["state"], job["wait_reason"]), ("waiting", "provider"))
            self.assertEqual(jobs.counts()["failed"], 0)
