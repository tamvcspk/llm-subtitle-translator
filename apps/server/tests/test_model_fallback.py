import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import worker
from subserver.domain import queue_policy
from subserver.domain import translation
from subserver.domain import queue_policy as policy
from tests.queue_harness import START, SchedulerCase

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


class ModelFallbackQueueTests(SchedulerCase):
    MODELS = MODELS

    def run_job(self, execute, advance=1):
        return self.tick(execute, advance=advance)

    def test_primary_outage_opens_the_breaker_then_finishes_on_the_strong_model(self):
        job_id = self.add("movie")
        used = []

        def execute(job, update_status):
            used.append(job["gst_model"])
            if job["gst_model"] == PRIMARY:
                raise translation.ProviderUnavailableError("503 UNAVAILABLE")
            return "translated"

        self.drain(execute, step=31)  # the default breaker opens at the third failure in a row
        self.assertEqual(used, [PRIMARY] * 3 + [FALLBACK])
        done = self.job(job_id)
        self.assertEqual((done["state"], done["gst_model"]), ("done", FALLBACK))
        for field in ("provider_retry_count", "last_error"):
            self.assertNotIn(field, done)

    def test_an_outage_of_the_strong_model_makes_the_job_wait_it_does_not_fail(self):
        job_id = self.add("movie")
        self.queue.jobs.transition(job_id, "queued", tier="strong")
        execute = Mock(side_effect=translation.ProviderUnavailableError("503 UNAVAILABLE"))

        self.drain(execute, step=31)

        # A job that needs the strong model is never sent back to the default one (R17-1): it waits for the strong one.
        self.assertEqual({call.args[0]["gst_model"] for call in execute.call_args_list}, {FALLBACK})
        job = self.job(job_id)
        self.assertEqual((job["state"], job["wait_reason"]), ("waiting", "provider"))  # not failed: the breaker rests
        self.assertEqual(self.queue.jobs.counts()["failed"], 0)

    def test_an_open_breaker_holds_the_other_jobs_too(self):
        self.configure(models=(PRIMARY,))
        first, second = self.add("first", created_at=START), self.add("second", created_at=START + 1)
        execute = Mock(side_effect=translation.ProviderUnavailableError("503"))
        self.drain(execute, step=31)  # three failures: the breaker is open
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(self.state(second), "waiting")  # parked: the provider cannot be called now
        self.assertIsNone(self.tick(lambda job, update: "translated", advance=100))
        done = self.drain(lambda job, update: "translated", step=400)
        self.assertEqual(sorted(done), sorted([first, second]))

    def test_daily_quota_moves_work_to_the_strong_model_until_both_are_exhausted(self):
        first, second = self.add("first", created_at=START), self.add("second", created_at=START + 1)
        execute = Mock(side_effect=[
            translation.DailyQuotaExceededError("429 daily quota"),
            translation.DailyQuotaExceededError("429 daily quota"),
        ])

        self.assertTrue(self.run_job(execute))
        self.assertIsNone(self.queue.quota_pause_until())  # the strong model still has quota
        self.assertEqual(self.job(first)["last_error"], "429 daily quota")
        health = self.queue.jobs.health_map()
        reset = policy.daily_quota_reset_at(self.clock.now)
        self.assertEqual((health[("gemini", PRIMARY)].state, health[("gemini", PRIMARY)].until), ("exhausted", reset))

        self.assertTrue(self.run_job(execute))
        self.assertEqual([call.args[0]["gst_model"] for call in execute.call_args_list], [PRIMARY, FALLBACK])
        self.assertEqual(self.queue.quota_pause_until(), reset)
        self.assertIsNone(self.tick(execute, advance=1))  # the next tick parks the job still in line
        self.assertEqual(self.queue.jobs.counts()["failed"], 0)  # waiting, not failed (NFR-1)
        self.assertEqual({self.state(first), self.state(second)}, {"waiting"})

        self.clock.advance(reset - self.clock.now + 1)
        used = []
        self.drain(lambda job, update: used.append(job["gst_model"]) or "translated")
        self.assertEqual(used, [PRIMARY, PRIMARY])
        self.assertEqual(self.job(first)["gst_model"], PRIMARY)

    def test_switching_the_strong_model_to_one_with_quota_lifts_the_pause(self):
        first = self.add("first")
        execute = Mock(side_effect=translation.DailyQuotaExceededError("429 daily quota"))
        self.assertTrue(self.run_job(execute))
        self.assertTrue(self.run_job(execute))
        self.assertIsNotNone(self.queue.quota_pause_until())
        self.assertEqual(self.state(first), "waiting")

        fresh = "gemini-3.6-flash-lite"
        self.configure(models=(PRIMARY, MODELS[1]))
        self.assertIsNotNone(self.queue.quota_pause_until())  # unchanged models keep the pause
        self.configure(models=(PRIMARY, fresh))
        self.assertIsNone(self.queue.quota_pause_until())  # a new model with quota left lifts it
        self.assertEqual(self.drain(lambda job, update: "translated"), [first])
        self.assertEqual(self.job(first)["gst_model"], fresh)

    def test_settings_update_through_the_worker_lifts_the_pause(self):
        store = self.store
        qw = worker.QueueWorker(
            "unused-queue-dir", {"gst_model": PRIMARY, "gst_fallback_model": FALLBACK}, None, None, store=store,
        )
        qw.queue.jobs.clock = self.clock.time
        qw.queue.jobs.monotonic = self.clock.monotonic
        self.queue = qw.queue
        self.add("first")
        execute = Mock(side_effect=translation.DailyQuotaExceededError("429 daily quota"))
        self.assertTrue(self.run_job(execute))
        self.assertTrue(self.run_job(execute))
        self.assertIsNotNone(qw.queue.quota_pause_until())
        qw.apply_settings({"gst_model": PRIMARY, "gst_fallback_model": "gemini-3.6-flash-lite"})
        self.assertIsNone(qw.queue.quota_pause_until())


class _NoBazarr:
    """MediaLibrary double: Bazarr cannot say whether the language is missing and nothing is rescanned."""

    def target_still_missing(self, job):
        return None

    def refresh(self, job):
        return None


class _NoTmdb:
    def description(self, job):
        return ""


class ProcessJobModelTests(unittest.TestCase):
    def test_process_job_runs_the_model_selected_by_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Movie.en.srt"
            subtitle.write_text("subtitle", encoding="utf-8")
            job = {"subtitle_path": str(subtitle), "source_code": "en", "target_code": "zh", "gst_model": FALLBACK}
            settings = {"gst_model": PRIMARY, "bazarr_url": "", "bazarr_api_key": "", "tmdb_api_key": ""}
            with patch.object(worker.DEFAULT_ATTEMPT, "run", return_value="translated") as run:
                worker.process_job(job, settings, None, None, media=_NoBazarr(), metadata=_NoTmdb())

            self.assertEqual(run.call_args.args[2]["gst_model"], FALLBACK)
            self.assertEqual(settings["gst_model"], PRIMARY)


if __name__ == "__main__":
    unittest.main()
