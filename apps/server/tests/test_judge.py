"""P15: the LLM judge between a variant and the current translation (FR-18.8, FR-19.6).

Advice only: it is off by default, it spends model calls only through a job of the one queue, its verdicts never count
as the admin's A/B pick and never edit a cue. Every provider is a fake; nothing touches the network."""

import json
import tempfile
import unittest
from unittest import mock

from subcore.judge import BATCH, JUDGE_PROMPT_VERSION, variant_is_a
from subcore.providers import FakeProvider, QuotaExhausted, RateLimited

from subserver import judge as judge_module
from subserver import library_api
from subserver import queue_policy as policy
from subserver.config import DEFAULT_APP_CONFIG, model_for_task, normalize_app_config
from subserver.jobs import FAILED, JUDGE, READING_KINDS, job_name
from subserver.judge import JudgeService, calls_for
from subserver.read_jobs import JudgeJob
from subserver.redaction import Redactor
from subserver.settings import SettingsService
from subserver.store import Store
from subserver.telemetry import Telemetry as TelemetrySink
from subserver.translation import CallBudgetError, DailyQuotaExceededError, JobStopped, ProviderUnavailableError

from . import test_corrections
from .test_corrections import SETTINGS, sha
from .test_style_profile import Env as StyleEnv


class JudgeEnv(StyleEnv):
    """An episode translated, a variant of it made (a candidate) and a judge that answers with ``self.answer``."""

    def __init__(self, test, *, enabled=True, **settings):
        super().__init__(test)
        self.settings = {"gst_model": "m-1", "judge_enabled": enabled, **settings}
        self.telemetry = TelemetrySink(self.store, usage=self.store, synchronous=True)
        self.service.telemetry = self.telemetry
        self.judge = JudgeService(self.service, self.jobs, self.store, settings_source=lambda: self.settings, telemetry=self.telemetry)
        self.service.judge = self.judge
        self.answer = "A"  # what the judge says for every cue it is shown: A, B, tie, or None to skip them all
        self.models = []  # the model each judge provider was made for
        self.judging = FakeProvider(self.reply)
        self.stages = []

    def reply(self, req, call_no):
        count = len(req.user.split("\n")) - 1
        if self.answer is None:
            return json.dumps({"verdicts": []})
        return json.dumps({"verdicts": [{"i": i, "better": self.answer, "why": f"reason {i}"} for i in range(count)]})

    def factory(self, settings, model, **kwargs):
        self.models.append(model)
        return self.judging

    def judge_job(self):
        return JudgeJob(self.judge, self.factory, settings_source=lambda: self.settings, usage=self.store, telemetry=self.telemetry)

    def candidate(self, episode=1, label="alt"):
        """Translate the episode, then queue and run a variant of it: the candidate version's number."""
        if self.series_id is None:
            self.translated(episode)
        self.prefix = "[alt] "
        self.service.variant(self.unit(episode), {"lang": "vi", "label": label, "style": {"tone": "dry"}})
        job = self.queued()[-1]
        self.jobs.jobs.transition(job["job_id"], "done", result="candidate")
        self.attempt.run(job, "", SETTINGS)
        self.prefix = "[vi] "
        return max(v.version for v in self.store.versions(self.output(episode)) if v.status == "candidate")

    def queued_judges(self):
        return [j for j in self.jobs.jobs.by_state("queued") if j["kind"] == JUDGE]

    def run_queued(self):
        """Run the queued judge job by hand (the queue's own claim is tested separately): ``(the job as it ended, result)``."""
        job = {**self.queued_judges()[-1], "gst_model": "m-1"}
        self.jobs.jobs.transition(job["job_id"], "running")
        self.stages.clear()
        try:
            result = self.judge_job()(job, lambda stage, **f: self.stages.append((stage, f)))
        except BaseException:
            self.jobs.jobs.transition(job["job_id"], "failed")
            raise
        self.jobs.jobs.transition(job["job_id"], "done", result=result)  # it leaves the line: the next request is a new job
        return job, result

    def asked(self):
        return [json.loads(line) for call in self.judging.calls for line in call.user.split("\n")[1:]]

    def verdicts(self, version):
        view = self.service.compare(self.unit(1), version, "vi")
        return view, {r["n"]: r["judge"] for r in view["rows"] if "judge" in r}


class OffByDefaultTests(unittest.TestCase):
    def test_the_judge_is_off_in_a_fresh_configuration_and_asks_nothing(self):
        self.assertIs(DEFAULT_APP_CONFIG["judge_enabled"], False)
        env = JudgeEnv(self, enabled=False)
        version = env.candidate()
        with self.assertRaisesRegex(ValueError, "judge is off"):
            env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        with self.assertRaisesRegex(ValueError, "judge is off"):
            env.service.ask_judge(env.unit(1), version, {"lang": "vi", "estimate_only": True})
        self.assertEqual((env.queued_judges(), env.judging.calls), ([], []))
        self.assertIs(env.service.compare(env.unit(1), version, "vi")["judge"]["enabled"], False)

    def test_the_switch_and_the_model_are_normalised_like_the_other_settings(self):
        self.assertIs(normalize_app_config({"judge_enabled": "on"})["judge_enabled"], True)
        self.assertIs(normalize_app_config({"judge_enabled": "off"})["judge_enabled"], False)
        self.assertEqual(normalize_app_config({"judge_model": "  big-1 "})["judge_model"], "big-1")
        self.assertEqual(model_for_task({"gst_model": "light", "judge_model": "big"}, "judge"), "big")
        self.assertEqual(model_for_task({"gst_model": "light"}, "judge"), "light")  # one model setting is enough

    def test_what_the_console_saves_reaches_the_running_worker(self):
        """The runtime settings carry the switches: before, a switch turned off in the console changed nothing."""
        folder = tempfile.TemporaryDirectory()  # saving settings also writes the languages file: keep it out of the repo
        self.addCleanup(folder.cleanup)
        service = SettingsService(
            Store(":memory:"), config_path=f"{folder.name}/config.json",
            postprocess_targets_path=f"{folder.name}/postprocess/targets.json",
        )
        self.addCleanup(service.repo.close)
        service.start()
        self.assertIs(service.settings()["judge_enabled"], False)
        self.assertIs(service.settings()["suggest_from_edits"], True)
        service.save({"judge_enabled": True, "judge_model": "big", "suggest_from_edits": False, "suggest_from_facts": False,
                      "auto_accept_terms": False})
        runtime = service.settings()
        self.assertEqual(
            (runtime["judge_enabled"], runtime["judge_model"], runtime["suggest_from_edits"], runtime["suggest_from_facts"],
             runtime["auto_accept_terms"]),
            (True, "big", False, False, False),
        )


class RequestTests(unittest.TestCase):
    def test_the_price_is_told_before_anything_is_queued(self):
        env = JudgeEnv(self, judge_model="m-judge")
        version = env.candidate()
        answer = env.service.ask_judge(env.unit(1), version, {"lang": "vi", "estimate_only": True})
        self.assertEqual((answer["cues"], answer["calls"], answer["model"], answer["capped"]), (12, 1, "m-judge", False))
        self.assertEqual(answer["estimate"]["calls"], 1)
        self.assertEqual((env.queued_judges(), env.judging.calls), ([], []))

    def test_a_request_queues_one_interactive_judge_job_and_a_second_is_the_same_job(self):
        env = JudgeEnv(self)
        version = env.candidate()
        first = env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        self.assertTrue(first["new"])
        (job,) = env.queued_judges()
        self.assertEqual((job["kind"], job["origin"], job["prio"]), (JUDGE, "manual", policy.INTERACTIVE))
        self.assertEqual((job["unit_id"], job["version"], job["target_code"], job["cues"], job["calls"]), (env.unit(1), version, "vi", 12, 1))
        self.assertEqual(job_name({**job, "label": "S01E01"}), "Judge variant · S01E01")
        self.assertIn(JUDGE, READING_KINDS)
        second = env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        self.assertEqual((second["job_id"], second["new"]), (first["job_id"], False))
        self.assertEqual(len(env.queued_judges()), 1)
        self.assertEqual(env.judging.calls, [])  # asking spends nothing: the job does

    def test_asking_again_while_a_judge_job_is_open_is_refused_rather_than_lost(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        for body in ({"lang": "vi", "again": True}, {"lang": "vi", "again": True, "estimate_only": True}):
            with self.assertRaisesRegex(ValueError, "already working on this candidate"):
                env.service.ask_judge(env.unit(1), version, body)
        self.assertEqual(len(env.queued_judges()), 1)

    def test_the_comparison_says_a_judge_is_working_on_it(self):
        env = JudgeEnv(self)
        version = env.candidate()
        self.assertIsNone(env.service.compare(env.unit(1), version, "vi")["judge"]["job"])
        asked = env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        job = env.service.compare(env.unit(1), version, "vi")["judge"]["job"]
        self.assertEqual((job["job_id"], job["state"]), (asked["job_id"], "queued"))

    def test_only_a_candidate_can_be_judged_and_two_equal_translations_have_nothing_to_judge(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.choose(env.unit(1), version, {"lang": "vi", "whole": "variant", "apply": True})  # adopted: no longer a candidate
        with self.assertRaisesRegex(ValueError, "not a candidate"):
            env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        same = JudgeEnv(self)
        same.translated(1)
        same.prefix = "[vi] "  # a variant that says exactly what the current text says
        same.service.variant(same.unit(1), {"lang": "vi", "style": {"tone": "dry"}})
        job = same.queued()[-1]
        same.jobs.jobs.transition(job["job_id"], "done", result="candidate")
        same.attempt.run(job, "", SETTINGS)
        candidate = next(v.version for v in same.store.versions(same.output(1)) if v.status == "candidate")
        with self.assertRaisesRegex(ValueError, "the two translations are the same"):
            same.service.ask_judge(same.unit(1), candidate, {"lang": "vi"})

    def test_a_cap_keeps_a_runaway_job_small(self):
        env = JudgeEnv(self)
        version = env.candidate()
        with mock.patch.object(judge_module, "JUDGE_MAX_CUES", 5):
            answer = env.service.ask_judge(env.unit(1), version, {"lang": "vi", "estimate_only": True})
            self.assertEqual((answer["cues"], answer["differing"], answer["capped"]), (5, 12, True))
            env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
            env.run_queued()
        self.assertEqual(len(env.asked()), 5)
        self.assertEqual(calls_for(0), 0)
        self.assertEqual((calls_for(1), calls_for(BATCH), calls_for(BATCH + 1)), (1, 1, 2))

    def test_the_route_maps_a_refusal_to_400_and_a_missing_version_to_404(self):
        env = JudgeEnv(self)
        version = env.candidate()
        route = f"/api/units/{env.unit(1)}/versions/{version}/judge"
        status, answer = library_api.handle(env.service, "POST", route, {}, {"lang": "vi", "estimate_only": True})
        self.assertEqual((status, answer["calls"]), (200, 1))
        self.assertEqual(library_api.handle(env.service, "POST", route.replace(f"/{version}/", "/99/"), {}, {"lang": "vi"})[0], 404)
        env.settings["judge_enabled"] = False
        self.assertEqual(library_api.handle(env.service, "POST", route, {}, {"lang": "vi"})[0], 400)


class RunTests(unittest.TestCase):
    def test_a_run_stores_a_verdict_for_every_cue_in_the_right_side(self):
        env = JudgeEnv(self, judge_model="m-judge")
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        job, result = env.run_queued()
        self.assertEqual(result, "judged")
        self.assertEqual(job["report"], {"judged": 12, "unanswered": 0, "capped": False})
        self.assertEqual(env.models, ["m-judge"])  # the task has a model of its own
        (call,) = env.judging.calls
        self.assertEqual((call.task, call.meta["kind"]), ("judge", "judge"))

        view, found = env.verdicts(version)
        self.assertEqual(sorted(found), list(range(1, 13)))
        for n, verdict in found.items():  # every answer was "A": the side shown first
            self.assertEqual(verdict["winner"], "variant" if variant_is_a(n) else "current")
        self.assertEqual(view["judge"]["judged"], 12)
        self.assertEqual(view["judge"]["todo"], 0)
        self.assertEqual(view["judge"]["variant"] + view["judge"]["current"] + view["judge"]["tie"], 12)
        self.assertEqual(view["judge"]["prompt_version"], JUDGE_PROMPT_VERSION)
        self.assertEqual([found[n]["why"] for n in (1, 12)], ["reason 0", "reason 11"])  # the reason of the item the cue was asked as

    def test_the_judge_sees_texts_and_the_line_before_and_no_timecode(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        items = env.asked()
        self.assertEqual(len(items), 12)
        self.assertNotIn("prev", items[0])  # the first cue has nothing before it
        self.assertEqual(items[1]["prev"], "Line 1 of episode 1")
        self.assertEqual(items[1]["source"], "Line 2 of episode 1")
        for text in (env.judging.calls[0].user, env.judging.calls[0].system):
            self.assertNotIn("-->", text)
            self.assertNotIn("00:00", text)
        self.assertEqual({items[0]["A"], items[0]["B"]}, {"[vi] Line 1 of episode 1", "[alt] Line 1 of episode 1"})

    def test_a_verdict_is_advice_never_a_pick_and_never_an_edit(self):
        env = JudgeEnv(self)
        version = env.candidate()
        target = env.output(1)
        before = sha(target)
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        self.assertEqual(sha(target), before)  # no cue was edited, no version published
        self.assertEqual([s for s in env.store.signals(kind="ab")], [])  # and it is no A/B score
        self.assertEqual(len(env.store.signals(kind="judge")), 12)
        self.assertEqual(len([v for v in env.store.versions(target) if v.status == "candidate"]), 1)
        done = env.service.choose(env.unit(1), version, {"lang": "vi", "picks": {"1": "current"}})
        self.assertEqual(done["recorded"], 1)  # only the admin's pick is recorded as theirs
        self.assertEqual([s.cue for s in env.store.signals(kind="ab")], [1])

    def test_the_judge_is_not_an_ab_figure_in_insights(self):
        from subserver.metrics import Metrics

        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        env.service.choose(env.unit(1), version, {"lang": "vi", "picks": {"1": "variant"}})
        metrics = Metrics(env.store, console=None, settings_source=lambda: env.settings, limits=lambda: (10, 0, 0))
        metrics.rollup(days=1)
        (row,) = metrics.report(days=1)["ab"]
        self.assertEqual((row["judged"], row["variant"]), (1, 1))  # the admin's one pick, not twelve verdicts

    def test_a_second_run_asks_only_about_cues_without_a_verdict_and_again_asks_all(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        self.assertEqual(len(env.asked()), 12)
        with self.assertRaisesRegex(ValueError, "already looked at every cue"):
            env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.service.add_correction(env.unit(1), {"lang": "vi", "kind": "cue_edit", "payload": {"edits": [{"cue": 2, "text": "Sửa tay"}]}})
        view, found = env.verdicts(version)
        self.assertNotIn(2, found)  # the verdict belonged to the text it saw: that text is gone
        self.assertEqual((view["judge"]["judged"], view["judge"]["todo"]), (11, 1))
        env.judging.calls.clear()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        self.assertEqual([item["source"] for item in env.asked()], ["Line 2 of episode 1"])
        self.assertIn(2, env.verdicts(version)[1])

        env.judging.calls.clear()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi", "again": True})
        env.run_queued()
        self.assertEqual(len(env.asked()), 12)

    def test_a_new_rubric_does_not_show_the_verdicts_of_the_old_one(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        with mock.patch.object(judge_module, "JUDGE_PROMPT_VERSION", "j99"):
            view, found = env.verdicts(version)
        self.assertEqual((found, view["judge"]["judged"], view["judge"]["todo"]), ({}, 0, 12))

    def test_verdicts_belong_to_their_candidate(self):
        env = JudgeEnv(self)
        first = env.candidate(label="one")
        env.service.ask_judge(env.unit(1), first, {"lang": "vi"})
        env.run_queued()
        second = env.candidate(label="two")
        self.assertNotEqual(first, second)
        self.assertEqual(env.verdicts(second)[0]["judge"]["judged"], 0)
        self.assertEqual(env.verdicts(first)[0]["judge"]["judged"], 12)

    def test_cues_the_model_skipped_are_counted_and_stay_to_ask(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.answer = None
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        job, result = env.run_queued()
        self.assertEqual(result, "nothing-answered")
        self.assertEqual(job["report"], {"judged": 0, "unanswered": 12, "capped": False})
        self.assertEqual(env.verdicts(version)[0]["judge"]["todo"], 12)

    def test_a_tie_and_the_other_side_are_read_back(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.answer = "tie"
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        view, found = env.verdicts(version)
        self.assertEqual({v["winner"] for v in found.values()}, {"tie"})
        self.assertEqual(view["judge"]["tie"], 12)
        env.answer = "B"
        env.service.ask_judge(env.unit(1), version, {"lang": "vi", "again": True})
        env.run_queued()
        for n, verdict in env.verdicts(version)[1].items():
            self.assertEqual(verdict["winner"], "current" if variant_is_a(n) else "variant")

    def test_an_answer_that_cannot_be_read_fails_the_job_with_a_reason_and_stores_nothing(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.judging = FakeProvider(lambda req, n: "A is better, I think")
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        with self.assertRaisesRegex(RuntimeError, "answer could not be read"):
            env.run_queued()
        self.assertEqual(env.store.signals(kind="judge"), [])

    def test_the_verdicts_do_not_depend_on_the_best_effort_telemetry_sink(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.judge.telemetry = judge_module.NULL  # a sink that drops everything
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        self.assertEqual(env.verdicts(version)[0]["judge"]["judged"], 12)

    def test_a_text_the_redactor_rewrites_is_still_matched_with_its_verdict(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.telemetry.redactor = Redactor(["[alt] Line"])  # what is written down loses it: the live text must be compared the same way
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()
        self.assertIn("[REDACTED]", env.store.signals(kind="judge")[0].after)
        view, found = env.verdicts(version)
        self.assertEqual((view["judge"]["judged"], view["judge"]["todo"]), (12, 0))

    def test_turning_the_judge_off_stops_a_job_already_in_line(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.settings["judge_enabled"] = False
        _, result = env.run_queued()
        self.assertEqual((result, env.judging.calls), ("skipped-judge-off", []))

    def test_a_candidate_that_is_gone_ends_the_job_without_a_call(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.service.choose(env.unit(1), version, {"lang": "vi", "whole": "variant", "apply": True})  # adopted meanwhile
        job, result = env.run_queued()
        self.assertEqual(result, "skipped-not-a-candidate")
        self.assertEqual(env.judging.calls, [])


def long_source(episode, cues=70):
    """An episode of ``cues`` cues, two seconds apart (the shared fixture has twelve and its timecodes stop at a minute)."""
    def stamp(ms):
        s, milli = divmod(ms, 1000)
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d},{milli:03d}"

    return "\n".join(
        f"{n}\n{stamp((n - 1) * 2000)} --> {stamp((n - 1) * 2000 + 1500)}\nLine {n} of episode {episode}\n"
        for n in range(1, cues + 1)
    )


class BatchTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(test_corrections, "source_text", long_source)
        patch.start()
        self.addCleanup(patch.stop)

    def batch_sizes(self, env):
        return [len(call.user.split("\n")) - 1 for call in env.judging.calls]

    def test_a_long_episode_is_judged_in_batches_and_a_stop_loses_nothing_paid_for(self):
        env = JudgeEnv(self)
        version = env.candidate()
        answer = env.service.ask_judge(env.unit(1), version, {"lang": "vi", "estimate_only": True})
        self.assertEqual((answer["cues"], answer["calls"]), (70, 3))
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        job = {**env.queued_judges()[-1]}
        env.jobs.jobs.transition(job["job_id"], "running")
        stages = []

        def stop_before_the_second_batch(stage, **fields):
            stages.append((stage, fields))
            if len(stages) == 2:
                raise JobStopped("the admin stopped the job")  # what the queue's own heartbeat raises when Stop is pressed

        with self.assertRaises(JobStopped):
            env.judge_job()(job, stop_before_the_second_batch)
        self.assertEqual(self.batch_sizes(env), [BATCH])
        self.assertEqual(stages[0], ("Judging cues 1 to 30", {"progress_done": 0, "progress_total": 70}))
        self.assertEqual(stages[1][1], {"progress_done": 30, "progress_total": 70})
        self.assertEqual(env.verdicts(version)[0]["judge"]["judged"], 30)  # the first batch was stored when it came back

        env.jobs.jobs.transition(job["job_id"], "cancelled")
        env.judging.calls.clear()  # the admin asks again: only what is left is asked and paid for
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        _, result = env.run_queued()
        self.assertEqual(result, "judged")
        self.assertEqual(self.batch_sizes(env), [BATCH, 10])
        self.assertEqual(env.verdicts(version)[0]["judge"]["judged"], 70)

    def test_a_judge_job_is_sized_to_the_call_budget_of_every_job_and_the_rest_is_asked_next(self):
        env = JudgeEnv(self, max_calls_per_job=1)
        version = env.candidate()
        answer = env.service.ask_judge(env.unit(1), version, {"lang": "vi", "estimate_only": True})
        self.assertEqual((answer["cues"], answer["calls"], answer["differing"], answer["capped"]), (BATCH, 1, 70, True))
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        job, _ = env.run_queued()  # no call is refused half way: the job asked for what one call can cover
        self.assertEqual((self.batch_sizes(env), job["report"]["capped"]), ([BATCH], True))
        self.assertEqual(env.verdicts(version)[0]["judge"]["judged"], 30)
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})  # the next job takes the next cues
        env.run_queued()
        self.assertEqual(env.verdicts(version)[0]["judge"]["judged"], 60)

    def test_the_retry_of_an_unreadable_answer_still_counts_against_the_budget(self):
        env = JudgeEnv(self, max_calls_per_job=1)
        version = env.candidate()
        env.judging = FakeProvider(lambda req, n: "A is better, I think" if n == 1 else env.reply(req, n))
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        with self.assertRaises(CallBudgetError):
            env.run_queued()
        self.assertEqual(env.store.signals(kind="judge"), [])

    def test_an_again_job_that_runs_a_second_time_does_not_pay_twice_for_what_it_stored(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.run_queued()  # every cue has a verdict
        env.judging.calls.clear()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi", "again": True})
        job = {**env.queued_judges()[-1]}
        env.jobs.jobs.transition(job["job_id"], "running")
        stages = []

        def stop_before_the_second_batch(stage, **fields):
            stages.append(stage)
            if len(stages) == 2:
                raise JobStopped("stopped")

        with self.assertRaises(JobStopped):
            env.judge_job()(job, stop_before_the_second_batch)
        self.assertEqual(self.batch_sizes(env), [BATCH])
        env.judging.calls.clear()
        env.judge_job()(job, lambda *a, **k: None)  # the same job again (a quota pause, a restart, Stop then Retry)
        self.assertEqual(self.batch_sizes(env), [BATCH, 10])  # only the 40 cues it had not judged yet


class QueueTests(unittest.TestCase):
    def test_the_queue_claims_a_judge_job_and_runs_its_handler(self):
        env = JudgeEnv(self)
        version = env.candidate()
        asked = env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        ran = env.jobs.process_once(env.judge_job(), models=("m-1",), settings=env.settings)
        self.assertTrue(ran)
        row = env.jobs.jobs.get(asked["job_id"])
        self.assertEqual((row["state"], row["result"]), ("done", "judged"))
        self.assertEqual(env.verdicts(version)[0]["judge"]["judged"], 12)

    def test_the_provider_errors_mean_what_they_mean_for_every_other_job(self):
        env = JudgeEnv(self)
        version = env.candidate()
        env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        job = env.queued_judges()[-1]
        for error, expected in ((QuotaExhausted("spent"), DailyQuotaExceededError), (RateLimited("slow"), ProviderUnavailableError)):
            def fail(req, n, error=error):
                raise error

            env.judging = FakeProvider(fail)
            with self.subTest(error=type(error).__name__), self.assertRaises(expected):
                env.judge_job()({**job}, lambda *a, **k: None)
        self.assertEqual(env.store.signals(kind="judge"), [])

    def test_a_failed_judge_job_is_retried_at_the_price_of_its_own_calls(self):
        env = JudgeEnv(self)
        version = env.candidate()
        asked = env.service.ask_judge(env.unit(1), version, {"lang": "vi"})
        env.jobs.jobs.transition(asked["job_id"], FAILED, error="boom")
        answer = env.service.estimate_retry({"job_id": asked["job_id"]})
        self.assertEqual((answer["planned_total"], answer["estimate"]["calls"]), (1, 1))
        self.assertEqual(answer["planned"][0]["title"], "Judge variant · S01E01")


if __name__ == "__main__":
    unittest.main()
