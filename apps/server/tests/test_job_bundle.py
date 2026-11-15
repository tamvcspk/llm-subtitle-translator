"""P11: the worker gathers a bundle, keeps it with its job, records its hash on every translation call, and hands it out
for "Xuất job spec". Every provider is a fake; nothing touches the network (criteria 18a, 18d, 18f; FR-15.10, FR-21.2)."""

import json
import unittest
from unittest import mock

from subcore.bundle import TranslationBundle
from subcore.glossary import loads as load_glossary
from subcore.translate.review import review
from subcore import srt

from subserver import gather as gather_mod
from subserver.console import ConsoleActions
from subserver.rules import build_rules
from subserver.store import Store
from subserver import translation_attempt

from .test_corrections import SETTINGS, Env

SECRET = "AIza-TEST-SECRET-KEY-9876"


class RecordingEnv(Env):
    """The corrections harness, with the provider wired the way the worker wires it: calls are recorded (on_call)."""

    def __init__(self, test):
        super().__init__(test)
        from subcore.providers.limiter import RateLimiter, ResilientProvider

        def factory(settings, model, *, deadline=None, on_call=None):
            return ResilientProvider(self.fake, RateLimiter(), deadline=deadline, on_call=on_call,
                                     base_delay=0.0, max_attempts=2)

        self.attempt.provider_factory = factory


class JobBundleTests(unittest.TestCase):
    def setUp(self):
        self.env = RecordingEnv(self)

    def queued(self, description=""):
        """A retranslation job that is in the queue table, so the bundle has a row to live on (FR-15)."""
        self.env.translate(1)
        self.env.service.retranslate(self.env.unit(1), {"lang": "vi"})
        job = self.env.jobs.jobs.by_state("queued")[-1]
        return job, description

    def test_a_translation_keeps_its_bundle_with_the_job(self):
        job, _ = self.queued()
        self.assertEqual(self.env.attempt.run(job, "", SETTINGS), "translated")
        kept = self.env.store.job_bundle(job["job_id"])
        self.assertIsNotNone(kept)
        bundle = TranslationBundle.from_json(kept.bundle_json)
        self.assertEqual(bundle.bundle_hash(), kept.bundle_hash)
        self.assertEqual(len(bundle.cues), 12)
        self.assertEqual(bundle.params.model, self.env.fake.model)

    def test_every_translation_call_carries_the_bundle_hash(self):
        """NFR-12: the call records of a translation name the bundle they were made from."""
        job, _ = self.queued()
        self.env.attempt.run(job, "", SETTINGS)
        kept = self.env.store.job_bundle(job["job_id"])
        calls = [c for c in self.env.store.llm_calls(job["job_id"]) if c.task == "translate"]
        self.assertTrue(calls)
        self.assertEqual({c.bundle_hash for c in calls}, {kept.bundle_hash})

    def test_the_job_snapshot_names_the_bundle(self):
        job, _ = self.queued()
        job["snapshot"] = {"model": "m-1"}
        self.env.attempt.run(job, "", SETTINGS)
        calls = [c for c in self.env.store.llm_calls(job["job_id"]) if c.task == "translate"]
        self.assertEqual(job["snapshot"]["bundle_hash"], calls[0].bundle_hash)

    def test_no_secret_reaches_the_stored_bundle(self):
        """Criterion 18d: a key that reached the text (here, the description) is masked in the copy the console
        exports; the keys in the settings never enter the bundle at all."""
        job, _ = self.queued()
        settings = {**SETTINGS, "gemini_api_key": SECRET, "gst_api_key_1": SECRET, "bazarr_api_key": SECRET}
        self.env.attempt.run(job, f"A film. note to self: {SECRET}", settings)
        kept = self.env.store.job_bundle(job["job_id"])
        self.assertIsNotNone(kept)
        self.assertNotIn(SECRET, kept.bundle_json)
        self.assertIn("[REDACTED]", kept.bundle_json)
        # The model never saw it either, and the exported spec reproduces the run: one text, one hash.
        sent = [c for c in self.env.fake.calls if SECRET in c.system or SECRET in c.user]
        self.assertEqual(sent, [])
        self.assertEqual(TranslationBundle.from_json(kept.bundle_json).bundle_hash(), kept.bundle_hash)
        calls = [c for c in self.env.store.llm_calls(job["job_id"]) if c.task == "translate"]
        self.assertEqual({c.bundle_hash for c in calls}, {kept.bundle_hash})

    def test_no_machine_path_reaches_the_bundle(self):
        job, _ = self.queued()
        self.env.attempt.run(job, "", SETTINGS)
        text = self.env.store.job_bundle(job["job_id"]).bundle_json
        self.assertNotIn(str(self.env.root), text)
        self.assertNotIn(str(self.env.root).replace("\\", "/"), text)

    def test_the_spec_is_the_document_the_cli_reads_and_is_absent_before_a_run(self):
        console = ConsoleActions(self.env.jobs)
        self.assertIsNone(console.job_spec("never-ran"))
        job, _ = self.queued()
        self.assertIsNone(console.job_spec(job["job_id"]))  # queued, not yet translated
        self.env.attempt.run(job, "", SETTINGS)
        spec = console.job_spec(job["job_id"])
        self.assertEqual(spec["bundle_version"], 1)
        self.assertEqual(TranslationBundle.from_dict(spec).bundle_hash(), self.env.store.job_bundle(job["job_id"]).bundle_hash)

    def test_jobs_rows_carry_no_bundle_text(self):
        """The bundle is big and many queue queries read whole job rows: it lives in a table of its own."""
        columns = {row["name"] for row in self.env.store.query("PRAGMA table_info(jobs)")}
        self.assertNotIn("bundle_json", columns)
        self.assertNotIn("bundle_hash", columns)

    def test_a_bundle_leaves_with_its_job(self):
        job, _ = self.queued()
        self.env.attempt.run(job, "", SETTINGS)
        self.assertIsNotNone(self.env.store.job_bundle(job["job_id"]))
        self.env.jobs.jobs.transition(job["job_id"], "done", result="translated")
        self.assertTrue(self.env.jobs.jobs.delete(("done",), job["job_id"]))
        self.assertIsNone(self.env.store.job_bundle(job["job_id"]))

    def test_clean_up_removes_the_bundles_of_expired_jobs(self):
        job, _ = self.queued()
        self.env.attempt.run(job, "", SETTINGS)
        self.env.jobs.jobs.transition(job["job_id"], "done", result="translated")
        reclaimed = self.env.jobs.jobs.expired(("done",), 1e12)
        self.assertGreater(reclaimed.bytes, len(self.env.store.job_bundle(job["job_id"]).bundle_json))
        self.assertEqual(self.env.jobs.jobs.delete_expired(("done",), 1e12), 1)
        self.assertIsNone(self.env.store.job_bundle(job["job_id"]))
        self.assertEqual(self.env.store.query("SELECT COUNT(*) AS n FROM job_bundles")[0]["n"], 0)

    def test_a_bundle_without_a_job_row_is_not_kept(self):
        self.env.store.save_job_bundle("no-such-job", "h", "{}")
        self.assertIsNone(self.env.store.job_bundle("no-such-job"))

    def test_keeping_the_bundle_never_fails_the_translation(self):
        job = self.env.job(5, job_id="broken-store")
        with mock.patch.object(Store, "save_job_bundle", side_effect=RuntimeError("disk full")):
            status = self.env.attempt.run(job, "", SETTINGS)
        self.assertEqual(status, "translated")
        self.assertEqual(srt.parse_file(self.env.output(5))[0].text, "[vi] Line 1 of episode 5")


class SharedReviewTests(unittest.TestCase):
    def test_the_attempt_reviews_its_output_with_the_library_function(self):
        """Criterion 18f: worker and `subcore validate` share one function, so they report the same findings."""
        env = Env(self)
        with mock.patch.object(translation_attempt, "review", wraps=review) as spy:
            env.translate(6)
        spy.assert_called_once()
        cues_in, cues_out, glossary = spy.call_args.args
        self.assertEqual(len(cues_in), len(cues_out))
        self.assertEqual(
            [f.to_dict() for f in review(cues_in, cues_out, glossary)],
            [f.to_dict() for f in review(srt.parse_file(env.media / "Show.S01E06.en.srt"),
                                          srt.parse_file(env.output(6)), glossary)],
        )


class ReviewGlossaryTests(unittest.TestCase):
    def test_review_uses_the_glossary_of_the_episode_not_the_whole_file(self):
        """A locked term that only starts at episode 5 must not be an error on episode 2 (the engine ignores it too)."""
        env = Env(self)
        env.glossaries.write(
            "series", "bazarr-7", "vi",
            "version: 1\nterms:\n  - {source: Kyber crystal, target: pha lê Kyber, locked: true, from: S01E05}\n",
        )
        seen = []

        def spy(source, target, glossary):
            seen.append(review(source, target, glossary))
            return seen[-1]

        with mock.patch.object(translation_attempt, "review", spy):
            env.translate(2)
        self.assertEqual([f for f in seen[0] if f.kind == "glossary"], [])
        whole = load_glossary(
            "version: 1\nterms:\n  - {source: Kyber crystal, target: pha lê Kyber, locked: true}\n")
        unwindowed = review(srt.parse_file(env.media / "Show.S01E02.en.srt"), srt.parse_file(env.output(2)), whole)
        self.assertTrue([f for f in unwindowed if f.kind == "glossary"])  # the wrong glossary would have flagged it

    def test_a_failing_review_never_fails_a_translated_job(self):
        env = Env(self)
        with mock.patch.object(translation_attempt, "review", side_effect=RuntimeError("boom")):
            self.assertEqual(env.translate(3), "translated")


class GatherTests(unittest.TestCase):
    def test_gather_is_pure_and_repeatable(self):
        """Same episode inputs, same bundle and hash: nothing in gather reads a clock, a file or the database."""
        env = Env(self)
        cues = srt.parse_file(env.media / "Show.S01E01.en.srt") if (env.media / "Show.S01E01.en.srt").exists() else []
        if not cues:
            env.translate(1)
            cues = srt.parse_file(env.media / "Show.S01E01.en.srt")
        rules = build_rules(load_glossary("version: 1\nterms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n"),
                            season=1, episode=1, cues=cues)
        kwargs = dict(
            cues=cues, rules=rules, unit=None, title="Show", overview="", target_code="vi", target_name="",
            source_code="en", model="m", max_output_tokens=4000, temperature=None, only=None, keep=None,
        )
        first = gather_mod.gather(**kwargs).bundle_hash()
        self.assertEqual(gather_mod.gather(**kwargs).bundle_hash(), first)
        self.assertEqual(json.loads(gather_mod.gather(**kwargs).to_json())["episode"]["title"], "Show")


if __name__ == "__main__":
    unittest.main()
