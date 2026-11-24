"""Review of P15 to P17 (the findings of the code review recorded in PLAN.md, "Code review P15-P17"): each test holds on to
one finding that was real. No network and no real provider."""

import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from unittest.mock import Mock

from starlette.testclient import TestClient

from subcore.providers import ModelNotFound
from subcore.providers.base import ProviderError
from subserver.adapters import backups
from subserver.adapters.redirects import RedirectResolver
from subserver.adapters.store import Store
from subserver.api import common
from subserver.api.common import error_response
from subserver.api.web import create_app, static_response
from subserver.domain import queue_policy as policy
from subserver.domain.translation import (
    DailyQuotaExceededError, HardJobError, ModelNotFoundError,
)
from subserver.jobs import provider_guard as guard
from subserver.jobs.jobs import DONE, FAILED, JUDGE, QUEUED, WAITING
from subserver.services.auth import SessionStore
from subserver.services.metrics import DAY, Metrics, _day

from tests import test_insights
from tests.queue_harness import SchedulerCase
from tests.test_corrections import CUES, Env as UnitEnv
from tests.test_web_app import OFF, Kit

SECRET_KEY = "AIzaSyA-secret-secret-secret-secret-123456"


def raising(exc):
    def run(job, update_status):
        raise exc
    return run


# -- the HTTP adapter ---------------------------------------------------------------------------------------------------


class HttpFindingsTests(Kit):
    def client(self):
        return TestClient(create_app(self.ctx, OFF), raise_server_exceptions=False)

    def test_a_folder_beside_the_build_folder_with_the_same_beginning_is_never_served(self):
        """R15d-1: ``static`` and ``static-private`` share a prefix; a prefix test let the second one through."""
        sibling = self.root / "static-private"
        sibling.mkdir()
        (sibling / "note.txt").write_text("a private note", encoding="utf-8")
        for path in ("/../static-private/note.txt", "/..//static-private/note.txt", "/%2e%2e/static-private/note.txt"):
            with self.subTest(path=path):
                response = static_response(str(self.root / "static"), path)
                self.assertNotIn(b"private", response.body)
                self.assertIn(b"console", response.body)  # the entry page, as for any path that is not a file
        self.assertIn(b"console", static_response(str(self.root / "static"), "/library/series/3").body)
        self.assertIn(b"console", static_response(str(self.root / "static"), "/nul\x00byte").body)

    def test_an_error_message_is_never_sent_with_a_key_in_it(self):
        """R15d-3: every text that leaves the process passes the redactor, an HTTP error included."""
        self.assertNotIn(SECRET_KEY, error_response(f"failed: https://x/?key={SECRET_KEY}").body.decode())

        class Broken:
            def snapshot(self):
                raise RuntimeError(f"the call to https://x/?key={SECRET_KEY} failed")

        self.ctx["console"] = Broken()
        with self.assertLogs(level="ERROR"):
            response = self.client().get("/api/queue")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(SECRET_KEY, response.text)

    def test_a_refusal_of_a_pure_router_says_it_in_the_same_redacted_words(self):
        # ``style`` is a pure router: its 400 is a ``(400, {"error": ...})`` the table route sends through ``error_response``.
        class Style:
            def presets(self):
                raise ValueError(f"no preset at https://x/?key={SECRET_KEY}")

        self.ctx["style"] = Style()
        response = self.client().get("/api/style/presets")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(SECRET_KEY, response.text)
        self.assertEqual(set(response.json()), {"error"})

    def test_the_three_requests_that_spend_calls_refuse_a_flag_that_is_not_a_boolean(self):
        """R15d-4: ``estimate_only: "true"`` used to queue the job it was meant to price."""
        client = self.client()
        cases = [
            ("/api/units/1/retranslate", {"estimate_only": "true"}, "estimate_only"),
            ("/api/units/1/variant", {"estimate_only": 1}, "estimate_only"),
            ("/api/units/1/versions/2/judge", {"estimate_only": "yes"}, "estimate_only"),
            ("/api/units/1/versions/2/judge", {"again": "true"}, "again"),
        ]
        for path, body, field in cases:
            with self.subTest(path=path, body=body):
                response = client.post(path, json=body)
                self.assertEqual(response.status_code, 400)
                self.assertRegex(response.json()["error"], rf"^{field}: ")

    def test_a_file_that_is_too_large_is_refused_unread(self):
        """R15d-5: the upload of a backup has a ceiling."""
        self.ctx.update(state_dir=str(self.root / "state"), config_path=str(self.root / "c.json"),
                        postprocess_targets_path=str(self.root / "t.json"), settings_service=None)
        with mock.patch.object(common, "MAX_BINARY_BODY", 100):
            response = self.client().post("/api/backups/import", content=b"x" * 101)
            self.assertEqual(response.status_code, 400)
            self.assertIn("larger than", response.json()["error"])
            streamed = self.client().post("/api/backups/import", content=iter([b"x" * 60, b"x" * 60]))
            self.assertEqual(streamed.status_code, 400)

    def test_a_member_of_a_backup_that_unpacks_to_too_much_is_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("backup.json", "{" + " " * 200 + "}")
        with mock.patch.object(backups, "MAX_MEMBER_BYTES", 50):
            with self.assertRaisesRegex(ValueError, "too large"):
                backups.restore_backup_archive(buffer.getvalue(), str(self.root / "s"), str(self.root / "c"), str(self.root / "t"))

    def test_the_docs_do_not_ask_for_a_query_the_console_never_sends(self):
        """R15e-1: restore and copy of a glossary read the language from the body."""
        from subserver.api import library_api

        for route in ("/api/series/(\\d+)/glossary/restore", "/api/series/(\\d+)/glossary/copy", "/api/movies/(\\d+)/glossary/restore"):
            (lang,) = library_api.QUERIES[("POST", route)]
            self.assertFalse(lang["required"], route)
        (lang,) = library_api.QUERIES[("PUT", "/api/series/(\\d+)/glossary")]
        self.assertTrue(lang["required"])


class SessionTests(unittest.TestCase):
    def test_a_refreshed_cookie_is_signed_with_the_secret_that_checked_the_old_one(self):
        """R15d-2: a password change between the check and the signing must not give the old session a new cookie."""
        old, new = b"o" * 32, b"n" * 32
        clock = [1_000.0]
        sessions = SessionStore(ttl=7 * 86_400, clock=lambda: clock[0], secret=lambda: old)
        token = sessions.create("admin")
        clock[0] += 2 * 3_600  # old enough to be refreshed
        reads = iter([old, new, new, new])  # the password changes after the first read
        sessions._secret_fn = lambda: next(reads)
        user, fresh = sessions.resolve(token)
        self.assertEqual(user, "admin")
        self.assertIsNotNone(fresh)
        sessions._secret_fn = lambda: new
        self.assertIsNone(sessions.resolve(fresh))  # signed with the old secret: it dies with the password change


class StoreTransactionTests(unittest.TestCase):
    def test_a_commit_that_fails_does_not_leave_the_connection_inside_a_transaction(self):
        """R15c-1."""
        store = Store(":memory:")
        self.addCleanup(store.close)

        class FlakyCommit:
            def __init__(self, db):
                self.db, self.fail = db, True

            def execute(self, sql, *args):
                if sql == "COMMIT" and self.fail:
                    self.fail = False
                    raise sqlite3.OperationalError("disk I/O error")
                return self.db.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.db, name)

        real = store._db
        store._db = FlakyCommit(real)
        with self.assertRaises(sqlite3.OperationalError):
            with store.transaction():
                pass
        self.assertFalse(real.in_transaction)
        with store.transaction():  # the next write starts as usual
            pass


class RedirectTests(unittest.TestCase):
    def test_a_redirect_to_another_google_redirect_is_not_a_page(self):
        """R16-5."""
        resolver = RedirectResolver()
        chained = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/next"
        with mock.patch("subserver.adapters.redirects.http.client.HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status, response.getheader.return_value = 302, chained
            self.assertIsNone(resolver.resolve("https://vertexaisearch.cloud.google.com/grounding-api-redirect/first"))
            response.getheader.return_value = "https://en.wikipedia.org/wiki/Fern"
            self.assertEqual(
                resolver.resolve("https://vertexaisearch.cloud.google.com/grounding-api-redirect/first"),
                "https://en.wikipedia.org/wiki/Fern",
            )


# -- the queue ------------------------------------------------------------------------------------------------------------


class QueueFindingsTests(SchedulerCase):
    MODELS = ("light", "strong")

    def test_a_job_that_went_up_to_the_strong_model_waits_for_it_and_never_goes_back_down(self):
        """R17-1 (a): the light model would only fail it the same way, for good."""
        name = self.add("hard")
        self.queue.jobs.transition(name, QUEUED, tier="strong")
        execute = Mock(side_effect=DailyQuotaExceededError("429 daily quota"))
        self.assertTrue(self.tick(execute))
        self.assertIsNone(self.tick(execute, advance=1))
        job = self.job(name)
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "quota"))
        self.assertEqual([call.args[0]["gst_model"] for call in execute.call_args_list], ["strong"])
        reset = policy.daily_quota_reset_at(self.clock.now)
        self.clock.advance(reset - self.clock.now + 1)
        used = []
        self.assertEqual(self.drain(lambda job, update: used.append(job["gst_model"]) or "translated"), [name])
        self.assertEqual(used, ["strong"])

    def test_a_hard_job_goes_up_to_a_strong_model_that_is_resting_and_waits_there(self):
        """R17-1 (b): a model that rests is a wait, not a reason to fail the job for good."""
        strong_back = self.clock.now + 7_200
        self.queue.jobs.set_health("gemini", "strong", policy.after_quota(policy.Health(), strong_back))
        name = self.add("hard")
        self.assertTrue(self.tick(raising(HardJobError("invalid output after repair"))))
        self.assertIsNone(self.tick(advance=1))
        job = self.job(name)
        self.assertEqual(job["state"], WAITING)
        self.assertNotEqual(job["state"], FAILED)
        self.assertEqual(job["escalated_from"], "light")
        self.clock.advance(7_300)
        used = []
        self.assertEqual(self.drain(lambda job, update: used.append(job["gst_model"]) or "translated"), [name])
        self.assertEqual(used, ["strong"])

    def test_a_job_whose_own_task_model_is_unknown_waits_for_a_time_and_is_not_sent_round_again_at_once(self):
        """R15-3 (b), R16-1: the sweep reads the health of the translation models only; a wait for "the provider" was lifted
        on the next tick, call after call."""
        name = self.add("reader")
        self.tick(raising(ModelNotFoundError("404", model="research-model")))
        job = self.job(name)
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "rate"))
        self.assertGreater(job["not_before"], self.clock.now)
        calls = []
        self.assertIsNone(self.tick(raising(AssertionError("no call while it rests")), advance=60))
        self.assertEqual(self.drain(lambda job, update: calls.append(1) or "translated", step=1_000), [name])

    def test_a_judge_that_runs_out_of_quota_or_cannot_be_reached_waits_alone(self):
        """R15-3 (a): the judge has a model of its own; what it spends must not rest the one translations run on."""
        for exc in (DailyQuotaExceededError("429 daily quota"), guard.ProviderUnavailableError("503")):
            with self.subTest(exc=type(exc).__name__):
                job_id = f"judge-{type(exc).__name__}"
                self.queue.jobs.add(
                    {"unit_id": 1, "target_code": "vi", "version": 2}, key=f"judge:{job_id}", kind=JUDGE, mode=JUDGE,
                    origin="manual", prio=policy.INTERACTIVE, job_id=job_id,
                )
                self.tick(raising(exc))
                job = self.job(job_id)
                self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "rate"))
                states = {h.state for h in self.queue.jobs.health_map().values()}
                self.assertEqual(states - {policy.CLOSED}, set())  # no quota, no breaker: the models are as they were
                self.queue.jobs.delete((WAITING,), job_id)

    def test_a_request_for_what_is_in_line_lifts_it_and_never_makes_an_empty_job(self):
        """R17-2: the job that was open when the request looked may have ended by the time it lifts it."""
        with tempfile.TemporaryDirectory() as folder:
            subtitle = Path(folder) / "Film.en.srt"
            subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
            targets = [{"code": "vi", "language": "Vietnamese", "enabled": True}]
            (made,) = self.queue.enqueue({"subtitle_path": str(subtitle), "source_code": "en"}, targets)
            self.queue.jobs.transition(made, DONE)  # it ended meanwhile...
            with mock.patch.object(self.queue.jobs, "open_for", return_value={"job_id": made}):  # ...but the request saw it open
                self.assertEqual(self.queue.enqueue({"subtitle_path": str(subtitle), "source_code": "en"}, targets), [])
            self.assertEqual(len(self.store.query("SELECT job_id FROM jobs")), 1)

    def test_the_choice_of_a_second_request_for_a_research_reaches_the_job_already_in_line(self):
        """R16-8: the admin said no auto-accept in the Estimate box; the job a translation left behind must hear it."""
        job_id, new = self.queue.enqueue_research(7, "vi", title="Frieren", origin="facts", prio=policy.AUTO)
        self.assertTrue(new)
        again, new = self.queue.enqueue_research(7, "vi", title="Frieren", auto_accept=False)
        self.assertEqual((again, new), (job_id, False))
        self.assertIs(self.job(job_id)["auto_accept"], False)
        self.queue.enqueue_research(7, "vi", title="Frieren")  # a request that says nothing leaves the choice as it is
        self.assertIs(self.job(job_id)["auto_accept"], False)

    def test_the_model_a_call_found_unknown_is_the_one_it_was_made_on(self):
        """R16-1 (b): a research searches on one model and reads on another."""

        class Inner:
            name, model, caps = "gemini", "memory-model", None

            def generate(self, req):
                raise ModelNotFound("404")

        provider = guard.BudgetedProvider(Inner(), guard.Budget(0, 0, None))
        with self.assertRaises(ModelNotFoundError) as caught:
            with guard.mapped_errors({"gst_model": "light"}, "research-model"):
                provider.generate(None)
        self.assertEqual(caught.exception.model, "memory-model")


# -- figures ----------------------------------------------------------------------------------------------------------------


class InsightsFindingsTests(unittest.TestCase):
    def setUp(self):
        self.db = Store(":memory:")
        self.addCleanup(self.db.close)

    def call(self, job, task, prompt, at=None):
        self.db.record_llm_call(
            job, provider="gemini", model="m", task=task, prompt_version=prompt, tokens_in=1, tokens_out=1, latency_ms=10,
            outcome="ok", at=at,
        )

    def test_a_job_that_also_read_story_facts_is_named_after_its_translation_prompt(self):
        """R15-1: ``memory-x3`` sorts before ``p12-1``."""
        for task, prompt in (("extract", "memory-x3"), ("translate", "p12-1"), ("translate", "p12-1")):
            self.call("j1", task, prompt)
        self.call("j2", "extract", "memory-x3")
        self.assertEqual({p.job_id: p.prompt_version for p in self.db.job_prompts(["j1", "j2"])}, {"j1": "p12-1"})

    def test_a_short_window_after_a_long_rest_does_not_leave_a_hole_no_window_fills(self):
        """R15-2."""
        now = [test_insights.NOW]
        metrics = Metrics(self.db, console=test_insights.FakeConsole(), settings_source=lambda: {}, clock=lambda: now[0])
        metrics.rollup(7)
        later = test_insights.NOW + 12 * DAY
        self.call("j9", "translate", "p", at=later)
        now[0] = test_insights.NOW + 20 * DAY
        metrics.rollup(3)  # a window that starts after the day with the call
        rows = [r for r in self.db.metric_rows(_day(later)) if r.day == _day(later) and r.name == "calls"]
        self.assertEqual([r.value for r in rows], [1.0])

    def test_two_languages_edited_on_one_day_are_two_edits(self):
        """R15-10."""
        at = test_insights.TODAY
        for lang in ("vi", "fr"):
            self.db.add_signal("cue_edit", unit_id=1, cue=1, before="a", after="b", meta={"model": "m", "job_id": "", "lang": lang}, at=at)
        metrics = Metrics(self.db, console=test_insights.FakeConsole(), settings_source=lambda: {}, clock=lambda: test_insights.NOW)
        edits = [r.value for r in metrics._signal_rows(at - 60) if r.name == "cue_edits"]
        self.assertEqual(sum(edits), 2.0)

    def test_an_escalation_that_has_not_ended_is_not_counted_against_the_jobs_that_have(self):
        """R15-5: ``1 of 0 jobs``."""
        metrics = Metrics(self.db, console=test_insights.FakeConsole(), settings_source=lambda: {}, clock=lambda: test_insights.NOW)
        waiting = {
            "by_model": {"m-1": {"done": 1, "failed": 0}}, "window": {"done_days": 30, "failed_days": 90},
            "escalated": {"total": 2, "done": 1, "failed": 0, "waiting": 1},
        }
        with mock.patch.object(test_insights.FakeConsole, "layer_report", lambda self: waiting):
            escalation = metrics.report(30)["operations"]["escalation"]
        self.assertEqual((escalation["jobs"], escalation["rate"], escalation["rescue_rate"]), (1, 1.0, 1.0))


class LayerReportTests(SchedulerCase):
    def test_an_escalated_variant_candidate_is_neither_a_rescue_nor_a_loss(self):
        """R15-5: the escalated count is over the same jobs as ``by_model``."""
        for name, result in (("a", "translated"), ("b", "candidate"), ("c", "")):
            self.add(name, escalated_from="light", gst_model="light", result=result)
            if result:  # the third is still in line
                self.queue.jobs.transition(name, DONE, result=result)
        report = self.queue.layer_report()
        self.assertEqual(report["escalated"], {"total": 2, "done": 1, "failed": 0, "waiting": 1})
        self.assertEqual(report["by_model"], {"light": {"done": 1, "failed": 0}})


# -- the life of a version ----------------------------------------------------------------------------------------------------


class VersionFindingsTests(unittest.TestCase):
    def test_a_cue_edit_keeps_what_the_translation_was_made_with(self):
        """R17-3: the Context tab, the stale flag and the incomplete flag all read it."""
        env = UnitEnv(self)
        env.translate(1)
        unit, output = env.unit(1), env.output(1)
        env.store.query(
            "UPDATE translations SET memory_hash = 'abc123', context_complete = 0, missing_units_json = '[\"S01E00\"]'"
            " WHERE output_path = ? AND version = 1", (output,),
        )
        env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 2, "text": "Sửa tay"}})
        current = env.versions.repo.current_version(output)
        self.assertEqual(current.version, 2)
        self.assertEqual((current.memory_hash, current.context_complete, current.missing_units), ("abc123", False, ("S01E00",)))
        env.service.revert(unit, 1, "vi")
        reverted = env.versions.repo.current_version(output)
        self.assertEqual((reverted.memory_hash, reverted.context_complete), ("abc123", False))

    def test_an_empty_list_of_edits_makes_no_version(self):
        """R17-7."""
        env = UnitEnv(self)
        env.translate(1)
        with self.assertRaisesRegex(ValueError, "at least one"):
            env.service.add_correction(env.unit(1), {"lang": "vi", "kind": "cue_edit", "payload": {"edits": []}})
        self.assertEqual(len(env.versions.repo.versions(env.output(1))), 1)

    def test_a_cue_edit_is_credited_to_the_job_that_wrote_the_text_not_to_an_unadopted_variant(self):
        """R15-4."""
        env = UnitEnv(self)
        env.translate(1)  # job ``auto-1``
        unit, output = env.unit(1), env.output(1)
        env.versions.publish(  # a variant nobody adopted: newest, and made by a job
            output, "1\n00:00:00,000 --> 00:00:01,000\nx\n", trigger="variant", source_path="", target_lang="vi", job_id="variant-9",
        )
        signals = []
        env.service.telemetry = mock.Mock(signal=lambda kind, **data: signals.append((kind, data)), event=lambda *a, **k: None)
        env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 2, "text": "Sửa tay"}})
        (edit,) = [data for kind, data in signals if kind == "cue_edit"]
        self.assertEqual(edit["job_id"], "auto-1")

    def test_adopting_a_candidate_undoes_the_cue_edits_it_does_not_contain(self):
        """R17-4: an old hand edit must not lock a cue of the text that replaced it."""
        env = UnitEnv(self)
        env.translate(1)
        unit, output = env.unit(1), env.output(1)
        env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 3, "text": "Sửa tay ba"}})
        candidate = env.versions.publish(
            output, "\n".join(f"{n}\n00:00:{(n - 1) * 2:02d},000 --> 00:00:{(n - 1) * 2:02d},500\nBản khác {n}\n" for n in range(1, CUES + 1)),
            trigger="variant", source_path="", target_lang="vi", job_id="variant-1",
        )
        env.service.adopt(unit, candidate.version, "vi")
        self.assertEqual([c for c in env.store.corrections(unit, "vi") if c.kind == "cue_edit"], [])

    def test_a_refused_retranslation_leaves_no_correction_behind(self):
        """R17-5."""
        env = UnitEnv(self)
        env.translate(1)
        unit = env.unit(1)
        before = len(env.store.corrections(unit, "vi"))
        new = [{"kind": "note", "payload": {"text": "left behind"}}]
        with self.assertRaisesRegex(ValueError, "unknown or inactive"):  # a wrong id is found before the new one is written
            env.service.retranslate(unit, {"lang": "vi", "correction_ids": [9999], "new_corrections": new})
        self.assertEqual(len(env.store.corrections(unit, "vi")), before)
        env.service.retranslate(unit, {"lang": "vi"})  # one is queued: the next is refused
        with self.assertRaisesRegex(ValueError, "already queued"):
            env.service.retranslate(unit, {"lang": "vi", "new_corrections": new})
        self.assertEqual(len(env.store.corrections(unit, "vi")), before)

    def test_a_verdict_is_recorded_once_it_has_been_acted_on(self):
        """R15-9: a verdict whose request was refused, and sent again, is one verdict."""
        env = UnitEnv(self)
        env.translate(1)
        unit, output = env.unit(1), env.output(1)
        text = "\n".join(
            f"{n}\n00:00:{(n - 1) * 2:02d},000 --> 00:00:{(n - 1) * 2:02d},500\nBản khác {n}\n" for n in range(1, CUES + 1)
        )
        candidate = env.versions.publish(
            output, text, trigger="variant", source_path="", target_lang="vi", job_id="variant-2",
            params={"variant": {"label": "casual"}},
        )
        signals = []
        env.service.telemetry = mock.Mock(signal=lambda kind, **data: signals.append(kind), event=lambda *a, **k: None)
        picks = {"picks": {"1": "variant"}, "apply": True, "lang": "vi"}
        with mock.patch.object(env.service, "add_correction", side_effect=ValueError("refused")):
            with self.assertRaises(ValueError):
                env.service.choose(unit, candidate.version, picks)
        self.assertEqual(signals, [])
        with mock.patch.object(env.service, "add_correction", return_value={"version": 3}):
            self.assertEqual(env.service.choose(unit, candidate.version, picks)["recorded"], 1)
        self.assertEqual(signals, ["ab"])


if __name__ == "__main__":
    unittest.main()
