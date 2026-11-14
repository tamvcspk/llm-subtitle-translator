"""The worker translates in-process through subcore (P2). No network: every provider is a fake."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subcore import srt
from subcore.providers import (
    AuthError,
    FakeProvider,
    QuotaExhausted,
    RateLimited,
    ResilientProvider,
    Unavailable,
)

from gst_worker import queue as gst_queue
from gst_worker.store import Store
from gst_worker.translation import DailyQuotaExceededError, HardJobError, ProviderUnavailableError
from gst_worker.translation_attempt import TranslationAttempt
from gst_worker.versions import TranslationVersions

SOURCE = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello <i>there</i>\n\n"
    "2\n00:00:03,000 --> 00:00:04,500\nHow are you?\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\nFine.\n"
)


class Env:
    """A temp directory with a source subtitle, a store and an attempt wired to a fake provider."""

    def __init__(self, test, handler=None, versions=True):
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "Movie.en.srt"
        self.source.write_text(SOURCE, encoding="utf-8")
        self.output = self.root / "Movie.vi.srt"
        self.store = Store(self.root / "app.db")
        test.addCleanup(self.store.close)
        self.fake = FakeProvider(handler=handler, prefix="[vi] ")
        self.slept = []

        def factory(settings, model, *, deadline=None, on_call=None):
            self.model = model
            return ResilientProvider(
                self.fake, deadline=deadline, on_call=on_call, base_delay=0.0, max_attempts=3,
                sleep=self.slept.append,
            )

        self.versions = TranslationVersions(self.store, self.root / "data" / "backups") if versions else None
        self.attempt = TranslationAttempt(versions=self.versions, usage=self.store if versions else None, provider_factory=factory)

    def job(self, **extra):
        return {
            "job_id": "job1",
            "subtitle_path": str(self.source),
            "output_path": str(self.output),
            "source_code": "en",
            "target_code": "vi",
            "target_language": "Vietnamese",
            **extra,
        }

    def run(self, description="", settings=None, **job_extra):
        return self.attempt.run(self.job(**job_extra), description, settings or {"gst_model": "m-1"})


class TranslateAndPublishTests(unittest.TestCase):
    def test_translates_in_process_and_keeps_structure(self):
        env = Env(self)
        self.assertEqual(env.run(), "translated")

        source = srt.parse(SOURCE)
        result = srt.parse(env.output.read_text(encoding="utf-8"))
        self.assertEqual([c.timing for c in result], [c.timing for c in source])
        self.assertEqual([c.index for c in result], [c.index for c in source])
        self.assertEqual(result[0].text, "[vi] Hello <i>there</i>")
        self.assertEqual(len(env.fake.calls), 1)

    def test_model_prompt_has_no_timecodes_or_paths(self):
        env = Env(self)
        env.run(description="A drama about a door.")
        request = env.fake.calls[0]
        text = request.system + request.user
        self.assertNotIn("00:00:01", text)
        self.assertNotIn(str(env.root), text)
        self.assertIn("A drama about a door.", text)
        self.assertIn("Vietnamese", text)

    def test_records_llm_calls_per_job(self):
        env = Env(self)
        env.run()
        calls = env.store.llm_calls("job1")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].outcome, "ok")
        self.assertEqual(calls[0].task, "translate")
        self.assertGreater(calls[0].tokens_in, 0)
        self.assertEqual(env.store.token_totals().calls, 1)

    def test_progress_is_reported(self):
        env = Env(self)
        seen = []
        env.attempt.run(env.job(), "", {"gst_model": "m-1"}, on_progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen[-1], (3, 3))

    def test_existing_target_is_never_overwritten_and_costs_no_call(self):
        env = Env(self)
        env.output.write_text("hand written", encoding="utf-8")
        self.assertEqual(env.run(), "skipped-existing-output")
        self.assertEqual(env.output.read_text(encoding="utf-8"), "hand written")
        self.assertEqual(env.fake.calls, [])

    def test_target_created_while_translating_is_not_overwritten(self):
        def handler(req, n):
            env.output.write_text("someone else", encoding="utf-8")  # appears mid-call
            return [{"i": i, "text": t} for i, t in req.meta["source"].items()]

        env = Env(self, handler=handler)
        self.assertEqual(env.run(), "skipped-existing-output")
        self.assertEqual(env.output.read_text(encoding="utf-8"), "someone else")
        self.assertEqual(env.store.versions(str(env.output)), [])

    def test_works_without_a_store(self):
        env = Env(self, versions=False)
        self.assertEqual(env.run(), "translated")
        self.assertTrue(env.output.exists())
        self.assertEqual(env.run(), "skipped-existing-output")


class FailureMappingTests(unittest.TestCase):
    def failing(self, error, **kwargs):
        env = Env(self, handler=lambda req, n: (_ for _ in ()).throw(error))
        return env

    def test_daily_quota_maps_to_queue_exception(self):
        env = self.failing(QuotaExhausted("per day"))
        with self.assertRaises(DailyQuotaExceededError):
            env.run()
        self.assertEqual(len(env.fake.calls), 1)  # not retried
        self.assertFalse(env.output.exists())

    def test_unavailable_maps_to_deferral(self):
        env = self.failing(Unavailable("503"))
        with self.assertRaises(ProviderUnavailableError):
            env.run()
        self.assertEqual(len(env.fake.calls), 3)  # backed off between attempts
        self.assertEqual(len(env.slept), 2)

    def test_continuous_429_defers_the_job_instead_of_failing_it(self):
        env = self.failing(RateLimited("429", retry_after=1))
        queue = gst_queue.JobQueue(str(env.root / "queue"))
        job = env.job()
        (queue.queue_dir / "pending" / "job1.json").write_text(json.dumps(job), encoding="utf-8")
        queue.process_once(
            lambda j, update: env.attempt.run(j, "", {"gst_model": "m-1"}), models=("m-1",)
        )
        self.assertTrue((queue.queue_dir / "deferred" / "job1.json").exists())
        self.assertFalse((queue.queue_dir / "failed" / "job1.json").exists())

    def test_rejected_key_fails_without_leaking_the_key(self):
        secret = "AIzaSyFAKEKEYFORTESTING1234567890abcdef"
        env = self.failing(AuthError(f"API key {secret} not valid"))
        with self.assertRaises(RuntimeError) as raised:
            env.run(settings={"gst_model": "m-1", "gemini_api_key": secret})
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIsInstance(raised.exception, (ProviderUnavailableError, DailyQuotaExceededError))

    def test_watchdog_stops_a_job_that_ran_out_of_time(self):
        env = Env(self)
        with patch("gst_worker.translation_attempt.job_timeout_seconds", return_value=0.0):
            with self.assertRaisesRegex(RuntimeError, "job limit"):
                env.run()
        self.assertEqual(env.fake.calls, [])

    def test_invalid_output_after_repair_fails_the_job(self):
        env = Env(self, handler=lambda req, n: "this is not json")
        with self.assertRaisesRegex(HardJobError, "Translation failed"):  # the queue may escalate this one
            env.run()
        self.assertFalse(env.output.exists())


class IdempotenceTests(unittest.TestCase):
    """NFR-3: the same input and context never buys a second model call."""

    def test_rerun_after_target_was_removed_reuses_stored_result(self):
        env = Env(self)
        env.run()
        first = env.output.read_bytes()
        env.output.unlink()
        self.assertEqual(env.run(), "translated")
        self.assertEqual(len(env.fake.calls), 1)
        self.assertEqual(env.output.read_bytes(), first)

    def test_changed_context_calls_the_model_again(self):
        env = Env(self)
        env.run(description="one")
        env.output.unlink()
        env.run(description="two")
        self.assertEqual(len(env.fake.calls), 2)

    def test_changed_model_calls_the_model_again(self):
        env = Env(self)
        env.run()
        env.output.unlink()
        env.run(settings={"gst_model": "m-2"})
        self.assertEqual(len(env.fake.calls), 2)

    def test_changed_source_calls_the_model_again(self):
        env = Env(self)
        env.run()
        env.output.unlink()
        env.source.write_text(SOURCE.replace("Fine.", "Great."), encoding="utf-8")
        env.run()
        self.assertEqual(len(env.fake.calls), 2)

    def test_damaged_stored_copy_is_not_trusted(self):
        env = Env(self)
        env.run()
        env.output.unlink()
        for stored in (env.root / "data" / "backups").rglob("v1-*"):
            stored.write_text("corrupted", encoding="utf-8")
        env.run()
        self.assertEqual(len(env.fake.calls), 2)


if __name__ == "__main__":
    unittest.main()
