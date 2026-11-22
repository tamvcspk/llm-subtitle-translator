"""Review wave W5 (F7): long work becomes jobs. Step 7a: what the kinds of job share and what readers of jobs must know.

No network and no real provider; ``provider_guard`` and the queue are driven directly.
"""

import unittest
from unittest.mock import patch

from subcore.providers import (
    AuthError, DeadlineExceeded, ModelNotFound, ProviderError, QuotaExhausted, RateLimited, Unavailable,
)
from subcore import srt
from subcore.translate import TranslationError

import worker
from subserver.jobs import provider_guard as guard
from subserver.domain.gather import gather
from subserver.domain.rules import UnitRules
from subserver.domain import queue_policy as policy
from subserver.api import suggestion_api
from subserver.jobs.jobs import (
    CANCELLED, FAILED, PUBLISH, QUEUED, RUNNING, TRANSLATING_KINDS, VARIANT, WAITING, job_key, job_name,
)
from subserver.jobs.queue import JobQueue
from subserver.domain.queue_policy import Health
from subserver.domain.ports import MissingApiKey
from subserver.adapters.store import Store
from subserver.domain.translation import (
    CallBudgetError, DailyQuotaExceededError, HardJobError, ModelNotFoundError, ProviderAuthError,
    ProviderUnavailableError,
)
from tests.queue_harness import START, SchedulerCase
from tests.test_proposals import Env as ProposalEnv
from tests.test_translation_attempt import SOURCE as SOURCE_TEXT, Env as AttemptEnv
from tests.test_story_memory import all_sources, source_text

import subserver.adapters.http as gst_http


class ErrorMappingTests(unittest.TestCase):
    """7a: the table that says what a provider's typed failure means to the queue, for every kind of job."""

    def raised(self, exc, settings=None, model=""):
        with self.assertRaises(Exception) as caught:
            with guard.mapped_errors(settings or {"gst_model": "light"}, model):
                raise exc
        return caught.exception

    def test_each_typed_failure_has_one_class(self):
        table = [
            (QuotaExhausted("out"), DailyQuotaExceededError),
            (RateLimited("slow"), ProviderUnavailableError),
            (Unavailable("down"), ProviderUnavailableError),
            (AuthError("no"), ProviderAuthError),
            (ModelNotFound("who"), ModelNotFoundError),
            (TranslationError({1: "bad output"}), HardJobError),
            (MissingApiKey("no key"), RuntimeError),
            (DeadlineExceeded(), RuntimeError),
            (ProviderError("other"), RuntimeError),
        ]
        for exc, expected in table:
            with self.subTest(exc=type(exc).__name__):
                self.assertIs(type(self.raised(exc)), expected)

    def test_a_task_with_a_model_of_its_own_names_that_model_when_it_is_unknown(self):
        error = self.raised(ModelNotFound("who"), {"gst_model": "light"}, model="extract-model")
        self.assertEqual(error.model, "extract-model")
        self.assertEqual(self.raised(ModelNotFound("who"), {"gst_model": "light"}).model, "light")

    def test_a_secret_in_the_message_is_hidden(self):
        error = self.raised(Unavailable("key=AIzaSyA-secret-secret-secret-secret-123456"))
        self.assertNotIn("AIzaSyA-secret", str(error))

    def test_the_text_of_an_error_never_decides_its_class(self):
        # A provider that says "quota" in a plain failure is still only a failure of the job's own.
        self.assertIs(type(self.raised(ProviderError("quota exhausted, rate limit, 429"))), RuntimeError)


class BudgetTests(unittest.TestCase):
    def test_the_cap_of_a_day_counts_the_calls_of_every_job_whatever_its_kind(self):
        budget = guard.Budget(0, 2, lambda since: 2)
        with self.assertRaises(CallBudgetError) as raised:
            budget.spend()
        self.assertEqual(raised.exception.scope, "day")

    def test_the_cap_of_a_job_counts_its_own_calls(self):
        budget = guard.Budget(1, 0, None)
        budget.spend()
        with self.assertRaises(CallBudgetError) as raised:
            budget.spend()
        self.assertEqual(raised.exception.scope, "job")

    def test_job_providers_give_one_provider_per_model_under_one_budget(self):
        made = []

        class Inner:
            name, model, caps = "p", "m", None

            def generate(self, req):
                return "ok"

        def factory(settings, model, *, deadline, on_call):
            made.append(model)
            return Inner()

        providers = guard.JobProviders(factory, {"gst_model": "light", "max_calls_per_job": 1}, "light")
        self.assertIs(providers.for_task("translate"), providers.for_task("translate"))
        providers.for_task("translate").generate(None)
        with self.assertRaises(CallBudgetError):
            providers.for_task("translate").generate(None)
        self.assertEqual(made, ["light"])

    def test_without_a_factory_the_job_says_so(self):
        with self.assertRaisesRegex(RuntimeError, "no provider factory"):
            guard.JobProviders(None, {}, "light").for_task("translate")


class WorkerDispatchTests(unittest.TestCase):
    """7a: ``run_job`` runs a job by its kind."""

    def make(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        return worker.QueueWorker("unused-queue-dir", {"gst_model": "light"}, gst_http.MemoryCache(), None, store=store)

    def test_translating_kinds_go_to_the_translation(self):
        qw = self.make()
        seen = []
        with patch.object(worker, "process_job", side_effect=lambda job, *a, **k: seen.append(job["job_id"]) or "translated"):
            for kind in TRANSLATING_KINDS:
                self.assertEqual(qw.run_job({"job_id": kind, "kind": kind}, lambda *a, **k: None), "translated")
            self.assertEqual(qw.run_job({"job_id": "old"}, lambda *a, **k: None), "translated")  # no kind: a publish
        self.assertEqual(seen, [PUBLISH, VARIANT, "old"])

    def test_a_registered_kind_runs_its_own_handler_and_not_the_translation(self):
        qw = self.make()
        qw.register_handler("extract", lambda job, update: f"extracted {job['job_id']}")
        with patch.object(worker, "process_job", side_effect=AssertionError("must not translate")):
            self.assertEqual(qw.run_job({"job_id": "e1", "kind": "extract"}, lambda *a, **k: None), "extracted e1")

    def test_a_kind_nobody_handles_fails_the_job_by_name(self):
        qw = self.make()
        with self.assertRaisesRegex(RuntimeError, "'mystery'"):
            qw.run_job({"job_id": "x", "kind": "mystery"}, lambda *a, **k: None)


class KindAwareReaders(SchedulerCase):
    """7a: the places that read jobs and assumed a subtitle behind each one."""

    def add_other(self, name, kind, **payload):
        added = self.queue.jobs.add(
            {"unit_id": None, **payload}, key=f"{kind}:{name}", kind=kind, origin="manual", job_id=name,
        )
        self.assertEqual(added.job_id, name)
        return name

    def test_a_job_row_says_its_kind(self):
        self.add("t")
        self.add_other("e", "extract")
        self.assertEqual(self.job("t")["kind"], PUBLISH)
        self.assertEqual(self.job("e")["kind"], "extract")

    def test_a_job_without_a_subtitle_has_nothing_to_settle(self):
        self.configure(job_settle_seconds=60)  # not used by the queue here: the argument is
        self.add_other("e", "extract")
        ran = []
        self.queue.process_once(lambda job, update: ran.append(job["job_id"]) or "extracted", settle_seconds=60)
        self.assertEqual(ran, ["e"])

    def test_the_layer_report_counts_only_translations(self):
        self.add("t")
        self.drain()
        self.add_other("e", "extract")
        self.queue.jobs.transition("e", FAILED, payload={"gst_model": "light", "kind": "extract"})
        report = self.queue.layer_report()
        self.assertEqual(report["by_model"], {"light": {"done": 1, "failed": 0}})

    def test_a_hard_extract_job_fails_instead_of_going_to_the_strong_model(self):
        self.configure(models=("light", "strong"))
        self.add_other("e", "extract")

        def execute(job, update):
            raise HardJobError("the output stayed invalid")

        self.queue.process_once(execute)
        self.assertEqual(self.state("e"), FAILED)
        self.assertNotIn("escalated_from", self.job("e"))

    def test_a_hard_translation_still_goes_to_the_strong_model(self):
        self.configure(models=("light", "strong"))
        self.add("t")

        def execute(job, update):
            raise HardJobError("the output stayed invalid")

        self.queue.process_once(execute)
        self.assertEqual(self.state("t"), QUEUED)
        self.assertEqual(self.job("t")["escalated_from"], "light")

    def test_the_breaker_opened_by_one_kind_holds_back_every_kind(self):
        self.configure(breaker_threshold=1)
        self.add_other("e1", "extract")
        self.add_other("s1", "suggest")
        self.add("t")

        def down(job, update):
            raise ProviderUnavailableError("the provider is down")

        self.queue.process_once(down)  # the first to run is an extract job: the breaker opens on its failure
        self.queue.sweep(self.clock.now)
        for name in ("s1", "t"):
            job = self.job(name)
            self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "provider"), name)

    def test_a_day_of_quota_spent_by_a_translation_holds_back_a_reading_job(self):
        self.configure(max_calls_per_day=1)
        self.add_other("e1", "extract")
        self.queue.store.calls_since = lambda since: 1  # the day's call was spent by a translation
        self.queue.process_once(lambda job, update: "extracted")
        job = self.job("e1")
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "quota"))

    def test_latest_for_looks_only_at_its_own_kind(self):
        self.add("t")
        self.queue.jobs.transition("t", FAILED)
        key = job_key("/media/t.en.srt", "/media/t.vi.srt", "vi")
        self.assertIsNotNone(self.queue.jobs.latest_for(key, FAILED))
        self.assertIsNone(self.queue.jobs.latest_for(key, FAILED, kind="extract"))


def with_episodes(test, **kw):
    """Six episodes with a source subtitle, all known to the library; only episode 1's facts are stored."""
    env = all_sources(test, **kw)
    env.translate(1)
    return env


class ExtractJobTests(unittest.TestCase):
    """7b: Extract facts is one job per episode, run by the queue like any other work."""

    def read_calls(self, env):
        return len(env.calls("extract"))

    def test_the_episodes_picked_become_one_job_each_and_only_those_are_read(self):
        env = with_episodes(self)
        before = self.read_calls(env)
        answer = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(5), env.unit_id(3)]})
        self.assertEqual((answer["ok"], answer["queued"], answer["merged"]), (True, 2, 0))
        self.assertEqual(len(answer["job_ids"]), 2)
        self.assertEqual(self.read_calls(env), before)  # the request spends nothing: the queue does
        self.assertEqual(env.run_jobs(), {"S01E03": "extracted", "S01E05": "extracted"})  # in story order
        self.assertEqual(self.read_calls(env), before + 2)
        coverage = {u["label"]: u["has_facts"] for u in env.service.series_memory(env.store.list_series()[0].id)["units"]}
        self.assertEqual([k for k, v in coverage.items() if v], ["S01E01", "S01E03", "S01E05"])

    def test_without_a_pick_it_reads_the_episode_and_every_earlier_one_that_has_no_facts(self):
        env = with_episodes(self)
        answer = env.service.refresh_context(env.unit_id(3))
        self.assertEqual(answer["skipped"], {"S01E01": "cached"})
        self.assertEqual(env.run_jobs(), {"S01E02": "extracted", "S01E03": "extracted"})

    def test_an_episode_with_no_source_is_left_out_and_says_why(self):
        env = with_episodes(self)
        env.remove_source(2)
        answer = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2), env.unit_id(3)]})
        self.assertEqual((answer["queued"], answer["skipped"]), (1, {"S01E02": "no-source"}))

    def test_a_job_whose_source_vanished_meanwhile_ends_skipped_without_a_call(self):
        env = with_episodes(self)
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})
        env.remove_source(2)
        before = self.read_calls(env)
        self.assertEqual(env.run_jobs(), {"S01E02": "skipped-no-source"})
        self.assertEqual(self.read_calls(env), before)

    def test_a_bad_pick_is_refused_before_any_job(self):
        env = with_episodes(self)
        for bad in ([], "S01E02", [True], ["3"], [999_999]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                env.service.refresh_context(env.unit_id(1), {"unit_ids": bad})
        self.assertEqual(env.service.jobs.jobs.open_count(), 0)

    def test_asking_again_while_a_job_is_open_makes_no_second_job_and_lifts_it(self):
        env = with_episodes(self)
        (first,) = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})["job_ids"]
        env.service.jobs.jobs.set_prio(first, 0)
        again = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})
        self.assertEqual((again["queued"], again["merged"]), (0, 1))
        self.assertEqual(env.service.jobs.jobs.get(first)["prio"], policy.INTERACTIVE)

    def test_an_episode_whose_source_changed_is_read_again(self):
        env = with_episodes(self)
        env.source(1).write_text(source_text(1).replace("Line 5", "Another line 5"), encoding="utf-8")
        answer = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(1)]})
        self.assertEqual(answer["queued"], 1)
        self.assertEqual(env.run_jobs(), {"S01E01": "extracted"})

    def test_the_provider_out_of_quota_makes_the_job_wait_and_the_next_run_goes_on(self):
        env = with_episodes(self)
        handler = env.fake.handler

        def refuse(req, n):
            raise QuotaExhausted("daily quota")

        env.fake.handler = refuse
        (job_id,) = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})["job_ids"]
        queue = env.service.jobs
        queue.configure(env.settings, ("m-1",), "gemini")
        queue.process_once(env.read)
        job = queue.jobs.get(job_id)
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "quota"))
        self.assertIsNotNone(queue.quota_pause_until())
        env.fake.handler = handler  # the quota is back: the same job runs and reads the episode
        queue.jobs.set_health("gemini", "m-1", Health())
        queue.jobs.transition(job_id, QUEUED)
        self.assertEqual(env.run_jobs(), {"S01E02": "extracted"})

    def test_the_cap_of_calls_a_day_stops_extract_jobs_like_translations(self):
        env = with_episodes(self)
        env.settings["max_calls_per_day"] = 1
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2), env.unit_id(3), env.unit_id(4)]})
        done = env.run_jobs()
        self.assertEqual(done, {"S01E02": "extracted"})  # the one call of the day
        waiting = env.service.jobs.jobs.by_state(WAITING)
        self.assertEqual([job["wait_reason"] for job in waiting], ["quota", "quota"])

    def test_a_job_stopped_before_it_starts_reads_nothing_and_the_others_go_on(self):
        env = with_episodes(self)
        ids = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2), env.unit_id(3), env.unit_id(4)]})["job_ids"]
        self.assertEqual(env.service.jobs.stop(ids[1]), "cancelled")  # S01E03
        self.assertEqual(env.run_jobs(), {"S01E02": "extracted", "S01E04": "extracted"})
        self.assertEqual(env.service.jobs.jobs.state_of(ids[1]), CANCELLED)

    def test_a_running_job_stops_at_its_next_step_and_reads_nothing(self):
        env = with_episodes(self)
        (job_id,) = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})["job_ids"]
        queue = env.service.jobs
        queue.configure(env.settings, ("m-1",), "gemini")
        before = self.read_calls(env)

        def stopped_first(job, update_status):
            queue.stop(job["job_id"])  # the admin pressed Stop while the job was already running
            return env.read(job, update_status)

        queue.process_once(stopped_first)
        self.assertEqual(queue.jobs.state_of(job_id), CANCELLED)
        self.assertEqual(self.read_calls(env), before)  # it never reached the model

    def test_the_jobs_survive_a_restart_and_run_in_story_order(self):
        env = with_episodes(self)
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(4), env.unit_id(2), env.unit_id(3)]})
        restarted = JobQueue(env.store)  # a new process: the table is what is left
        self.assertEqual(restarted.recover(), 0)
        self.assertEqual(restarted.jobs.open_count(), 3)
        env.service.jobs = restarted
        self.assertEqual(list(env.run_jobs()), ["S01E02", "S01E03", "S01E04"])

    def test_a_job_that_died_with_the_worker_goes_back_in_line(self):
        env = with_episodes(self)
        (job_id,) = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})["job_ids"]
        env.service.jobs.jobs.transition(job_id, RUNNING)
        restarted = JobQueue(env.store)
        self.assertEqual(restarted.recover(), 1)
        self.assertEqual(restarted.jobs.state_of(job_id), QUEUED)

    def test_an_answer_the_model_garbles_fails_the_job_with_the_reason_and_it_can_be_retried(self):
        env = with_episodes(self)
        handler = env.fake.handler
        env.fake.handler = lambda req, n: "this is not json"
        (job_id,) = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})["job_ids"]
        queue = env.service.jobs
        queue.configure(env.settings, ("m-1",), "gemini")
        queue.process_once(env.read)
        job = queue.jobs.get(job_id)
        self.assertEqual(job["state"], FAILED)
        self.assertIn("could not be read", job["error"])
        env.fake.handler = handler
        estimate = env.service.estimate_retry({"job_id": job_id})
        self.assertEqual((estimate["planned_total"], estimate["planned"][0]["title"]), (1, "Extract facts · S01E02"))
        self.assertEqual(env.service.retry_failed({"job_id": job_id})["count"], 1)
        self.assertEqual(env.run_jobs(), {"S01E02": "extracted"})

    def test_the_activity_lists_it_with_what_it_reads(self):
        env = with_episodes(self)
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})
        (job,) = env.service.jobs.snapshot()["pending"]
        self.assertEqual((job["kind"], job["label"], job["title"]), ("extract", "S01E02", "Show"))
        self.assertEqual(job_name(job), "Extract facts · S01E02")


class SuggestJobTests(unittest.TestCase):
    """7c: Look for proposals is one job per series and language, and a translation only leaves one behind."""

    def test_asking_only_queues_and_the_queue_makes_the_calls(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        answer = env.suggestions.generate(env.series_id, "vi")
        self.assertEqual((answer["queued"], answer["merged"], len(answer["job_ids"])), (1, 0, 1))
        self.assertEqual(env.asked(), [])
        self.assertEqual(env.pending(), [])
        done = env.run_jobs()
        self.assertEqual(list(done.values()), ["proposed"])
        self.assertTrue(env.pending("address_rule") and env.pending("term") and env.pending("character"))
        job = env.service.jobs.jobs.get(answer["job_ids"][0])
        self.assertEqual((job["state"], job["report"]["calls"], job["report"]["errors"]), ("done", 3, []))

    def test_a_second_look_over_unchanged_facts_is_free(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        env.generate()
        asked = len(env.asked())
        (first, second) = (env.suggestions.generate(env.series_id, "vi")["job_ids"][0], None)
        self.assertEqual(list(env.run_jobs().values()), ["nothing-new"])
        self.assertEqual(len(env.asked()), asked)

    def test_the_provider_giving_out_midway_keeps_what_was_paid_for_and_the_job_goes_on_from_there(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        real = env.handler

        def spent_at_terms(req, n):
            if req.meta.get("what") == "terms":
                raise QuotaExhausted("daily quota")
            return real(req, n)

        env.fake.handler = spent_at_terms
        (job_id,) = env.suggestions.generate(env.series_id, "vi")["job_ids"]
        env.run_jobs()
        queue = env.service.jobs
        job = queue.jobs.get(job_id)
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "quota"))
        self.assertTrue(env.pending("address_rule"))  # the rules were stored as soon as they came back
        self.assertEqual(job["report"]["added"], len(env.pending()))  # and the job says so
        env.fake.handler = real  # the quota is back
        queue.jobs.set_health("gemini", "m-1", Health())
        queue.jobs.transition(job_id, QUEUED)
        before = len(env.asked("rules"))
        env.run_jobs()
        self.assertEqual(len(env.asked("rules")), before)  # the rules are not asked (nor paid for) again
        self.assertTrue(env.pending("term") and env.pending("character"))

    def test_the_cap_of_calls_a_day_holds_a_look_for_proposals_back_like_any_other_job(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(3)
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(5)]})
        env.run_jobs()  # one call of the day, recorded as the worker records it
        env.settings["max_calls_per_day"] = 1
        (job_id,) = env.suggestions.generate(env.series_id, "vi")["job_ids"]
        env.run_jobs()
        self.assertEqual(env.asked(), [])
        job = env.service.jobs.jobs.get(job_id)
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "quota"))

    def test_the_cap_of_calls_for_one_job_fails_it_with_the_reason(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        env.settings["max_calls_per_job"] = 1
        (job_id,) = env.suggestions.generate(env.series_id, "vi")["job_ids"]
        env.run_jobs()
        job = env.service.jobs.jobs.get(job_id)
        self.assertEqual(job["state"], FAILED)
        self.assertIn("max_calls_per_job", job["error"])
        self.assertTrue(env.pending())  # the one call it made is stored

    def test_activity_names_it_for_its_series_and_a_retry_costs_up_to_three_calls(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        (job_id,) = env.suggestions.generate(env.series_id, "vi")["job_ids"]
        (job,) = env.service.jobs.snapshot()["pending"]
        self.assertEqual((job["kind"], job["title"], job["target_code"]), ("suggest", "Show", "vi"))
        self.assertEqual(job_name(job), "Look for proposals · Show")
        env.service.jobs.jobs.transition(job_id, FAILED, error="boom")
        estimate = env.service.estimate_retry({"job_id": job_id})
        self.assertEqual((estimate["planned_total"], estimate["estimate"]["calls"]), (1, 3))

    def test_the_routes_answer_with_the_job(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        status, payload = suggestion_api.handle(
            env.suggestions, "POST", "/api/suggestions/generate", {}, {"series_id": env.series_id, "lang": "vi"},
        )
        self.assertEqual((status, payload["queued"], len(payload["job_ids"])), (200, 1, 1))
        status, payload = suggestion_api.handle(
            env.suggestions, "POST", "/api/suggestions/generate", {}, {"series_id": env.series_id, "lang": "vi"},
        )
        self.assertEqual((status, payload["queued"], payload["merged"]), (200, 0, 1))  # the same job: not made twice

    def test_a_look_is_not_the_policys_and_not_a_translation_of_the_library(self):
        env = ProposalEnv(self)
        env.translate(6)
        self.assertEqual(env.store.policy_jobs_since(0), 0)
        self.assertEqual(env.service.jobs.jobs.for_units([env.unit_id(6)]), [])


class AttemptHardeningTests(unittest.TestCase):
    """7e: what a translation attempt refuses, and how it keeps its bundle."""

    VARIANT = {"label": "casual", "preset": "", "style": {}}
    SECRET = "AIza-TEST-SECRET-KEY-9876"

    def test_a_variant_without_the_version_history_is_refused_and_nothing_goes_on_disk(self):
        env = AttemptEnv(self, versions=False)
        with self.assertRaisesRegex(RuntimeError, "variant needs"):
            env.run(trigger="variant", variant=self.VARIANT)
        self.assertFalse(env.output.exists())
        self.assertEqual(env.fake.calls, [])

    def test_a_variant_without_the_library_is_refused_even_with_the_history(self):
        env = AttemptEnv(self)  # versions, but no library: its style layers could not be laid
        with self.assertRaisesRegex(RuntimeError, "variant needs"):
            env.run(trigger="variant", variant=self.VARIANT)
        self.assertFalse(env.output.exists())
        self.assertEqual(env.fake.calls, [])

    def test_an_attempt_without_the_history_still_translates_a_plain_job(self):
        env = AttemptEnv(self, versions=False)
        self.assertEqual(env.run(), "translated")  # the bare attempt of the legacy entry point is unchanged
        self.assertTrue(env.output.exists())

    def test_the_free_text_is_masked_before_the_bundle_so_nothing_is_read_back_from_it(self):
        from subcore.bundle import TranslationBundle

        env = AttemptEnv(self)
        settings = {"gst_model": "m-1", "gemini_api_key": self.SECRET}
        with patch.object(TranslationBundle, "from_json", side_effect=AssertionError("the bundle was rebuilt")), \
                patch.object(TranslationBundle, "to_json", autospec=True, side_effect=TranslationBundle.to_json) as dumped:
            env.run(description=f"A film. note to self: {self.SECRET}", settings=settings)
        self.assertEqual(dumped.call_count, 1)  # serialised once: to check it and to keep it
        sent = " ".join(c.system + c.user for c in env.fake.calls)
        self.assertNotIn(self.SECRET, sent)
        self.assertIn("[REDACTED]", sent)

    def test_a_key_that_reaches_the_bundle_by_another_field_is_still_masked_in_it_and_in_its_text(self):
        from subcore.bundle import TranslationBundle

        env = AttemptEnv(self)
        bundle = gather(
            cues=srt.parse(SOURCE_TEXT), rules=UnitRules(), unit=None, title="Movie", overview=f"note {self.SECRET}",
            target_code="vi", target_name="Vietnamese", source_code="en", model="m-1", max_output_tokens=1000,
            temperature=None, only=None, keep=None,
        )
        clean, text = env.attempt._clean(bundle, {"gemini_api_key": self.SECRET})
        self.assertNotIn(self.SECRET, text)
        self.assertNotIn(self.SECRET, clean.to_json())
        self.assertEqual(TranslationBundle.from_json(text).bundle_hash(), clean.bundle_hash())  # one text, one hash

    def test_a_bundle_that_cannot_be_read_back_is_kept_as_it_was(self):
        env = AttemptEnv(self)
        bundle = gather(
            cues=srt.parse(SOURCE_TEXT), rules=UnitRules(), unit=None, title="Movie", overview=f"note {self.SECRET}",
            target_code="vi", target_name="Vietnamese", source_code="en", model="m-1", max_output_tokens=1000,
            temperature=None, only=None, keep=None,
        )
        from subcore.bundle import TranslationBundle

        with patch.object(TranslationBundle, "from_json", side_effect=ValueError("garbled")):
            kept, text = env.attempt._clean(bundle, {"gemini_api_key": self.SECRET})
        self.assertIs(kept, bundle)
        self.assertEqual(text, bundle.to_json())


class BackgroundTasksAreGoneTests(unittest.TestCase):
    """7d: the one mechanism for long work is the queue."""

    def test_the_modules_and_the_context_field_are_gone(self):
        import importlib.util

        from subserver.runtime import RuntimeContext

        for name in ("subserver.background", "subserver.tasks_api"):
            self.assertIsNone(importlib.util.find_spec(name), name)
        self.assertNotIn("tasks", RuntimeContext.__dataclass_fields__)

    def test_no_route_serves_tasks_and_nothing_takes_a_background_flag(self):
        from subserver.api.web import create_app

        self.assertEqual([route.path for route in create_app({}, None).routes if "/tasks" in route.path], [])
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        self.assertFalse(hasattr(env.suggestions, "tasks"))
        self.assertFalse(hasattr(env.service, "tasks"))


class ExtractJobsAreNotTranslationsTests(unittest.TestCase):
    """7b: the readers of jobs that must not mistake an extract job for a translation."""

    def test_an_extract_job_is_not_a_job_of_the_library_pages_or_the_policys_count(self):
        env = with_episodes(self)
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})
        jobs = env.service.jobs.jobs
        self.assertEqual(jobs.for_units([env.unit_id(2)]), [])  # the Library reads publish jobs only
        self.assertIsNone(jobs.open_for_unit(env.unit_id(2), ""))
        self.assertEqual(env.store.policy_jobs_since(0), 0)

    def test_an_extract_job_does_not_count_as_a_series_translation_in_the_catalog(self):
        from subserver.services.catalog_view import CatalogView

        env = with_episodes(self)
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)]})
        view = CatalogView.__new__(CatalogView)
        view.store = env.store
        self.assertEqual(view._open_by_series(), {})


if __name__ == "__main__":
    unittest.main()
