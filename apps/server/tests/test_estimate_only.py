"""P12b backend: every button that spends model calls can say what it would cost first (UX 6.4), and "Use this version"
can keep the variant's style in the same request. ``estimate_only`` reads and counts; it queues, saves and calls nothing."""

import base64
import http.client
import json
import unittest

from subserver.api import library_api
from subserver.services.console import ConsoleActions
from subserver.services.estimates import Estimator

from .test_corrections import SETTINGS, sha
from .test_style_profile import Env


def failed_retranslation(env, episode=1, **body):
    """A retranslation job that ended ``failed``, the thing Retry puts back in line."""
    env.service.retranslate(env.unit(episode), {"lang": "vi", **body})
    job = env.queued()[-1]
    env.jobs.jobs.transition(job["job_id"], "running")
    env.jobs.jobs.transition(job["job_id"], "failed", error="boom")
    return job["job_id"]


class RetranslateEstimate(unittest.TestCase):
    def test_a_range_costs_its_own_cues_and_nothing_is_queued_or_called(self):
        env = Env(self)
        env.translated(1)
        env.fake.calls.clear()
        whole = env.service.retranslate(env.unit(1), {"lang": "vi", "estimate_only": True})
        part = env.service.retranslate(env.unit(1), {"lang": "vi", "range": {"cues": [2, 5]}, "estimate_only": True})
        self.assertEqual((whole["estimate_only"], whole["estimate"]["jobs"], whole["estimate"]["cues"]), (True, 1, 12))
        self.assertEqual((part["range"], part["estimate"]["cues"], part["estimate"]["calls"]), ({"first": 2, "last": 5}, 4, 1))
        self.assertNotIn("job_id", part)
        self.assertEqual((env.queued(), env.fake.calls), ([], []))

    def test_the_estimate_refuses_what_the_real_request_would_refuse(self):
        env = Env(self)
        env.translated(1)
        env.output(1)
        with self.assertRaisesRegex(ValueError, "cues must"):
            env.service.retranslate(env.unit(1), {"lang": "vi", "range": {"cues": [5, 99]}, "estimate_only": True})
        with self.assertRaisesRegex(ValueError, "unknown or inactive"):
            env.service.retranslate(env.unit(1), {"lang": "vi", "correction_ids": [999], "estimate_only": True})
        env.service.retranslate(env.unit(1), {"lang": "vi"})  # a real one is now open
        with self.assertRaisesRegex(ValueError, "already queued"):
            env.service.retranslate(env.unit(1), {"lang": "vi", "estimate_only": True})
        self.assertEqual(len(env.queued()), 1)

    def test_new_corrections_are_not_made_by_an_estimate(self):
        env = Env(self)
        env.translated(1)
        body = {"lang": "vi", "estimate_only": True,
                "new_corrections": [{"kind": "term", "scope": "episode", "payload": {"source": "Kyber", "target": "K"}}]}
        env.service.retranslate(env.unit(1), body)
        self.assertEqual(env.service.list_corrections(env.unit(1), "vi"), [])
        with self.assertRaisesRegex(ValueError, "list of corrections"):
            env.service.retranslate(env.unit(1), {**body, "new_corrections": ["x"]})


class VariantEstimate(unittest.TestCase):
    def test_a_variant_costs_the_whole_episode_and_queues_nothing(self):
        env = Env(self)
        env.translated(1)
        result = env.service.variant(env.unit(1), {"lang": "vi", "label": "dry", "style": {"tone": "dry"}, "estimate_only": True})
        self.assertEqual((result["estimate_only"], result["label"], result["estimate"]["cues"]), (True, "dry", 12))
        self.assertEqual((env.queued(), env.fake.calls[1:]), ([], []))

    def test_the_estimate_checks_the_style_and_an_open_variant_like_the_real_request(self):
        env = Env(self)
        env.translated(1)
        with self.assertRaisesRegex(ValueError, "max_lines"):
            env.service.variant(env.unit(1), {"lang": "vi", "style": {"max_lines": 9}, "estimate_only": True})
        with self.assertRaises(LookupError):
            env.service.variant(env.unit(1), {"lang": "vi", "preset": "nope", "estimate_only": True})
        env.service.variant(env.unit(1), {"lang": "vi", "style": {"tone": "dry"}})
        with self.assertRaisesRegex(ValueError, "already queued"):
            env.service.variant(env.unit(1), {"lang": "vi", "style": {"tone": "wry"}, "estimate_only": True})


class RetryEstimate(unittest.TestCase):
    def test_a_failed_job_is_costed_and_stays_failed(self):
        env = Env(self)
        env.translated(1, 2)
        job = failed_retranslation(env, 1)
        result = env.service.estimate_retry({"job_id": job, "estimate_only": True})
        self.assertEqual((result["estimate_only"], result["planned_total"], result["skipped_total"]), (True, 1, 0))
        self.assertEqual((result["estimate"]["jobs"], result["estimate"]["cues"], result["estimate"]["calls"]), (1, 12, 1))
        self.assertEqual(result["planned"][0]["lang"], "vi")
        self.assertEqual(env.jobs.jobs.state_of(job), "failed")  # nothing was put back in line

    def test_a_retried_range_costs_that_range_only(self):
        env = Env(self)
        env.translated(1)
        job = failed_retranslation(env, 1, range={"cues": [3, 6]})
        self.assertEqual(env.service.estimate_retry({"job_id": job})["estimate"]["cues"], 4)

    def test_all_names_every_failed_job_and_the_others_say_why_they_are_left_out(self):
        env = Env(self)
        env.translated(1, 2)
        first, second = failed_retranslation(env, 1), failed_retranslation(env, 2)
        env.service.retranslate(env.unit(2), {"lang": "vi"})  # episode 2 is asked for again: an open twin of `second`
        queued = env.queued()[-1]["job_id"]
        everything = env.service.estimate_retry({"all": True})
        self.assertEqual([r["job_id"] for r in everything["planned"]], [first])
        self.assertEqual(everything["skipped_by_reason"], {"already requested": 1})
        named = env.service.estimate_retry({"job_ids": [first, queued, "nope", first]})
        self.assertEqual((named["planned_total"], named["estimate"]["jobs"]), (1, 1))  # a repeated id counts once
        self.assertEqual(named["skipped_by_reason"], {"the job is not failed": 1, "no such job": 1})
        self.assertEqual(second, everything["skipped"][0]["job_id"])

    def test_the_request_must_name_the_jobs_in_exactly_one_way(self):
        env = Env(self)
        for body in ({}, {"job_id": "a", "all": True}, {"job_ids": "a"}, {"job_ids": [1]}, {"job_ids": ["a"], "all": True}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                env.service.estimate_retry(body)

    def test_retry_all_failed_puts_each_back_in_line_and_counts_the_ones_it_left_alone(self):
        env = Env(self)
        env.translated(1, 2)
        first, second = failed_retranslation(env, 1), failed_retranslation(env, 2)
        env.service.retranslate(env.unit(2), {"lang": "vi"})  # episode 2 is asked for again: `second` has an open twin
        result = env.service.retry_failed({"all": True})
        self.assertEqual(result, {"ok": True, "count": 1, "skipped": 1})
        self.assertEqual((env.jobs.jobs.state_of(first), env.jobs.jobs.state_of(second)), ("queued", "failed"))
        # Nothing is failed any more that can go back: a second time retries nothing, and says so.
        self.assertEqual(env.service.retry_failed({"all": True}), {"ok": False, "count": 0, "skipped": 1})
        self.assertEqual(env.service.retry_failed({"job_ids": []}), {"ok": True, "count": 0, "skipped": 0})
        with self.assertRaises(ValueError):
            env.service.retry_failed({"job_ids": ["a"], "all": True})

    def test_a_job_whose_source_is_gone_is_left_out_with_the_reason(self):
        env = Env(self)
        env.translated(1)
        job = failed_retranslation(env, 1)
        from pathlib import Path
        Path(env.store.get_unit(env.unit(1)).source_path).unlink()
        result = env.service.estimate_retry({"job_id": job})
        self.assertEqual((result["planned_total"], result["skipped_by_reason"]), (0, {"the source subtitle cannot be read": 1}))


class EstimateNumbers(unittest.TestCase):
    def test_the_estimate_says_the_calls_left_today_and_the_pause_like_a_bulk_request(self):
        env = Env(self)
        env.translated(1)
        env.service.estimator = Estimator(env.store, env.jobs, lambda: {"max_calls_per_day": 10})
        result = env.service.retranslate(env.unit(1), {"lang": "vi", "estimate_only": True})["estimate"]
        self.assertEqual((result["calls_left_today"], result["paused_until"]), (10, None))
        self.assertEqual(set(result), {
            "jobs", "cues", "calls", "calls_left_today", "paused_until", "minutes", "finish_at", "extract_calls", "extract_model",
        })


class AdoptKeepingTheStyle(unittest.TestCase):
    def variant(self, env, preset="drama", **style):
        env.prefix = "[alt] "
        env.service.variant(env.unit(1), {"lang": "vi", "label": "alt", "preset": preset, "style": style})
        job = env.queued()[-1]
        env.jobs.jobs.transition(job["job_id"], "done", result="candidate")
        env.attempt.run(job, "", SETTINGS)
        return next(v.version for v in env.store.versions(env.output(1)) if v.status == "candidate")

    def test_a_dry_run_counts_the_other_translations_it_would_flag_and_changes_nothing(self):
        env = Env(self)
        series = env.translated(1, 2)
        version = self.variant(env, tone="dry")
        stored = sha(env.output(1))
        dry = env.service.adopt(env.unit(1), version, "vi", apply_style=True, dry_run=True)
        self.assertEqual((dry["dry_run"], dry["adopted"], dry["would_stale"]), (True, None, 1))  # episode 2, not episode 1
        self.assertEqual(dry["style"], {"preset": "drama", "style": {"tone": "dry"}})
        self.assertEqual(sha(env.output(1)), stored)
        self.assertEqual(env.store.get_version(env.output(1), version).status, "candidate")
        self.assertEqual(env.style.overview(series)["style"], {})

    def test_apply_style_adopts_and_keeps_the_variants_style_for_the_series(self):
        env = Env(self)
        series = env.translated(1, 2)
        version = self.variant(env, tone="dry")
        done = env.service.adopt(env.unit(1), version, "vi", apply_style=True)
        self.assertEqual((done["ok"], done["adopted"], done["would_stale"]), (True, version, 1))
        self.assertEqual(env.cue_texts(1)[2], "[alt] Line 3 of episode 1")  # the variant is on disk
        page = env.style.overview(series)
        self.assertEqual((page["preset"], page["style"]), ("drama", {"tone": "dry"}))
        self.assertEqual(env.statuses(), {1: "current", 2: "stale"})  # flagged, never retranslated
        self.assertEqual(env.queued(), [])

    def test_without_apply_style_adopting_is_as_it_was(self):
        env = Env(self)
        series = env.translated(1)
        version = self.variant(env, tone="dry")
        done = env.service.adopt(env.unit(1), version, "vi")
        self.assertEqual(set(done), {"ok", "version", "adopted"})
        self.assertEqual(env.style.overview(series)["style"], {})

    def test_a_version_that_is_not_a_variant_has_no_style_to_keep_and_nothing_is_adopted(self):
        env = Env(self)
        env.translated(1)
        version = self.variant(env, tone="dry")
        env.service.adopt(env.unit(1), version, "vi")
        earlier = env.store.versions(env.output(1))[0].version
        with self.assertRaisesRegex(ValueError, "not made by a variant"):
            env.service.adopt(env.unit(1), earlier, "vi", apply_style=True)
        with self.assertRaises(LookupError):
            env.service.adopt(env.unit(1), 999, "vi", apply_style=True, dry_run=True)
        with self.assertRaisesRegex(ValueError, "add apply_style"):
            env.service.adopt(env.unit(1), version, "vi", dry_run=True)

    def test_a_preset_deleted_since_the_variant_was_made_is_refused_before_anything_is_adopted(self):
        env = Env(self)
        env.translated(1)
        env.styles.clone_preset("drama", "Mine")  # a built-in preset cannot be deleted, a clone can
        version = self.variant(env, preset="mine", tone="dry")
        env.styles.delete_preset("mine")
        stored = sha(env.output(1))
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            env.service.adopt(env.unit(1), version, "vi", apply_style=True)
        self.assertEqual(sha(env.output(1)), stored)

    def test_a_whole_episode_verdict_can_keep_the_style_too(self):
        env = Env(self)
        series = env.translated(1)
        version = self.variant(env, tone="dry")
        done = env.service.choose(env.unit(1), version, {"lang": "vi", "whole": "variant", "apply": True, "apply_style": True})
        self.assertEqual(done["applied"]["style"]["preset"], "drama")
        self.assertEqual(env.style.overview(series)["style"], {"tone": "dry"})

    def test_a_movie_keeps_the_style_as_its_own(self):
        from .test_movie_owner import MovieEnv

        env = MovieEnv(self)
        movie = env.translate_movie()
        env.prefix = "[alt] "
        env.service.variant(movie, {"lang": "vi", "preset": "drama", "style": {"tone": "dry"}})
        job = env.queued()[-1]
        env.jobs.jobs.transition(job["job_id"], "done", result="candidate")
        env.attempt.run(job, "", SETTINGS)
        target = str(env.movie_dir / "Heat.vi.srt")
        version = next(v.version for v in env.store.versions(target) if v.status == "candidate")
        env.service.adopt(movie, version, "vi", apply_style=True)
        self.assertEqual(env.styles.owner_style("movies", "bazarr-movie-5")[0], "drama")
        self.assertEqual(env.style.movie_overview(movie)["style"], {"tone": "dry"})


class Routes(unittest.TestCase):
    def test_adopt_takes_apply_style_and_dry_run_as_booleans_only(self):
        env = Env(self)
        env.translated(1)
        version = AdoptKeepingTheStyle().variant(env, tone="dry")
        path = f"/api/units/{env.unit(1)}/versions/{version}/adopt"
        status, body = library_api.handle(env.service, "POST", path, {}, {"lang": "vi", "apply_style": True, "dry_run": True})
        self.assertEqual((status, body["dry_run"]), (200, True))
        self.assertEqual(library_api.handle(env.service, "POST", path, {}, {"lang": "vi", "dry_run": True})[0], 400)
        status, body = library_api.handle(env.service, "POST", path, {}, {"lang": "vi", "apply_style": "yes"})
        self.assertEqual((status, set(body)), (200, {"ok", "version", "adopted"}))  # a string is not a yes

    def test_the_retry_route_estimates_only_when_asked_and_otherwise_retries(self):
        from subserver.services.auth import ConsoleAuth
        from tests.web_harness import serve_console

        env = Env(self)
        env.translated(1)
        job = failed_retranslation(env, 1)
        port = serve_console(
            self, {"units": env.service, "console": ConsoleActions(env.jobs), "static_dir": str(env.root),
                   "queue_dir": env.queue_dir},
            ConsoleAuth.from_env({"CONSOLE_USER": "u", "CONSOLE_PASSWORD": "p"}),
        ).port
        auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

        def post(body):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("POST", "/api/queue/retry", body=json.dumps(body), headers=auth)
                response = conn.getresponse()
                return response.status, json.loads(response.read().decode("utf-8"))
            finally:
                conn.close()

        status, body = post({"job_id": job, "estimate_only": True})
        self.assertEqual((status, body["planned_total"], env.jobs.jobs.state_of(job)), (200, 1, "failed"))
        self.assertEqual(post({"estimate_only": True})[0], 400)  # names no job
        self.assertEqual(post({"job_id": job}), (200, {"ok": True}))
        self.assertEqual(env.jobs.jobs.state_of(job), "queued")


if __name__ == "__main__":
    unittest.main()
