"""P15: Insights and the feedback loop (SPEC FR-19.5, FR-19.6; criteria 17h, 20 a-d). Every provider is a fake and
every figure is checked against records the test wrote itself: nothing touches the network."""

import base64
import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from subcore import golden
from subcore.feedback import MIN_REPEATS

import worker
from subserver import insight_api, suggestion_api
from subserver.auth import ConsoleAuth
from subserver.console import ConsoleActions
from subserver.queue import JobQueue
from subserver.feedback import FeedbackProposer
from subserver.insight_api import Insights
from subserver.metrics import DAY, MARK_DAY, Metrics
from subserver.ports import MetricRow
from subserver.store import Store
from subserver.suggestions import SuggestionService

from .test_corrections import SETTINGS, Env as CorrectionsEnv

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc).timestamp()
TODAY = NOW - 6 * 3600  # an hour of the same UTC day
EARLIER = NOW - 2 * DAY


def store():
    return Store(":memory:")


class Recorder:
    """What the core hands the telemetry port, kept."""

    def __init__(self):
        self.events, self.signals = [], []

    def event(self, kind, **data):
        self.events.append((kind, data))

    def signal(self, kind, **data):
        self.signals.append((kind, data))

    def llm_call(self, *a, **k):
        pass


# -- the figures -------------------------------------------------------------------------------------------------


class Records:
    """Calls, events and signals written the way the worker writes them, and the figures they must add up to."""

    def __init__(self, db: Store):
        self.db = db
        # model, prompt version, job, task, kind, outcome, tokens in/out, repairs, how many, when
        calls = [
            ("m-1", "p1", "j1", "translate", "translate", "ok", 100, 50, 0, 4, TODAY),
            ("m-1", "p1", "j1", "translate", "repair", "ok", 40, 20, 3, 1, TODAY),
            ("m-1", "p1", "j2", "translate", "translate", "rate_limited", 0, 0, 0, 1, EARLIER),
            ("m-1", "p1", "j2", "translate", "translate", "unavailable", 0, 0, 0, 1, EARLIER),
            ("m-2", "p2", "j3", "translate", "translate", "ok", 100, 50, 0, 2, EARLIER),
            ("m-1", "p1", "j4", "extract", "translate", "ok", 10, 5, 0, 2, TODAY),
        ]
        for model, prompt, job, task, kind, outcome, tin, tout, repairs, n, at in calls:
            for _ in range(n):
                db.record_llm_call(
                    job, provider="gemini", model=model, task=task, tokens_in=tin, tokens_out=tout, latency_ms=1000,
                    outcome=outcome, at=at, kind=kind, prompt_version=prompt, repairs=repairs, cost_est=0.01,
                )
        for job, model, cues, review, at in [
            ("j1", "m-1", 100, {"cps": 5, "line_length": 2}, TODAY),
            ("j2", "m-1", 100, {"cps": 1}, EARLIER),
            ("j3", "m-2", 50, {}, EARLIER),
        ]:
            db.add_event("translation.finished", job_id=job, data={"model": model, "cues": cues, "review": review}, at=at)
        # Three cues were corrected (one of them twice on the same day), one episode was retranslated.
        for unit, cue in [(1, 1), (1, 2), (2, 1), (1, 1)]:
            db.add_signal("cue_edit", unit_id=unit, cue=cue, before="a", after="b", meta={"model": "m-1", "job_id": "j1"}, at=TODAY)
        db.add_signal("retranslate", unit_id=1, meta={"model": "m-1", "whole_episode": True}, at=TODAY)
        db.add_signal("retranslate", unit_id=2, meta={"model": "m-1", "whole_episode": False}, at=TODAY)
        db.add_signal("revert", unit_id=1, at=TODAY)
        db.add_signal("validate", unit_id=1, cue=3, meta={"issue": "missing", "model": "m-1"}, at=TODAY)
        for winner in ("variant", "variant", "tie", "current"):
            db.add_signal("ab", unit_id=1, cue=1, meta={"label": "casual", "prompt_version": "p1", "winner": winner}, at=TODAY)
        db.add_signal("ab", unit_id=1, meta={"label": "casual", "prompt_version": "p1", "winner": "variant", "whole_episode": True}, at=TODAY)


class FakeConsole:
    def snapshot(self, limit=100):
        return {
            "counts": {"pending": 3, "processing": 1, "deferred": 2, "done": 9, "failed": 1, "cancelled": 0},
            "providers": [{"provider": "gemini", "model": "m-1", "state": "closed"}],
            "daily_quota_pause": None,
        }

    def layer_report(self):
        return {
            "by_model": {"m-1": {"done": 9, "failed": 1}}, "window": {"done_days": 30, "failed_days": 90},
            "escalated": {"total": 4, "done": 3, "failed": 1, "waiting": 0},
        }


def metrics(db, **settings):
    return Metrics(db, console=FakeConsole(), settings_source=lambda: settings, limits=lambda: (10, 0, 0), clock=lambda: NOW)


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.db = store()
        self.addCleanup(self.db.close)
        Records(self.db)
        self.metrics = metrics(self.db, max_calls_per_day=50)

    def test_17h_operations_match_the_records(self):
        ops = self.metrics.report(30)["operations"]
        self.assertEqual(ops["calls"]["total"], 11)
        self.assertEqual(ops["calls"]["by_outcome"], {"ok": 9, "rate_limited": 1, "unavailable": 1})
        self.assertEqual(ops["calls"]["rate_429"], round(1 / 11, 4))
        self.assertEqual(ops["calls"]["rate_503"], round(1 / 11, 4))
        self.assertEqual(ops["usage"]["calls_today"], 7)  # today's seven; the four of two days ago are not
        self.assertEqual(ops["usage"]["limits"], {"rpm": 10, "tpm": 0, "rpd": 0, "max_calls_per_day": 50})
        # Episodes are counted from the events; the cost of an episode is its translation task, not the extraction.
        self.assertEqual(ops["per_episode"]["episodes"], 3)
        translate_in = 4 * 100 + 40 + 2 * 100
        self.assertEqual(ops["per_episode"]["tokens_in"], round(translate_in / 3, 1))
        self.assertEqual(ops["per_episode"]["cost"], round(9 * 0.01 / 3, 4))  # nine translate-task calls
        self.assertEqual(ops["tokens"]["in"], translate_in + 2 * 10)
        self.assertEqual(ops["latency_ms"]["calls"], 9)
        self.assertEqual(ops["latency_ms"]["median"], 1000.0)

    def test_the_queue_and_the_providers_are_the_live_ones(self):
        ops = self.metrics.report(30)["operations"]
        self.assertEqual(ops["queue"]["depth"], 6)  # waiting to run, running, or waiting for a reason
        self.assertEqual(ops["providers"][0]["state"], "closed")
        self.assertEqual(ops["escalation"], {
            "jobs": 10, "escalated": 4, "rescued": 3, "rate": 0.4, "rescue_rate": 0.75, "window_days": 30,
        })

    def test_wait_before_a_job_starts(self):
        for n, wait in enumerate((10, 20, 30, 100)):
            self.db.query(
                "INSERT INTO jobs (job_id, key, kind, state, created_at, updated_at, started_at)"
                " VALUES (?, ?, 'publish', 'done', ?, ?, ?)", (f"w{n}", f"k{n}", TODAY - wait, TODAY, TODAY),
            )
        waits = self.metrics.report(30)["operations"]["wait_seconds"]
        self.assertEqual(waits["jobs"], 4)
        self.assertEqual(waits["median"], 30.0)
        self.assertEqual(waits["p90"], 100.0)

    def test_20d_quality_figures_and_the_share_of_corrected_cues(self):
        quality = self.metrics.report(30)["quality"]
        self.assertEqual((quality["cues_translated"], quality["episodes"]), (250, 3))
        self.assertEqual(quality["repair"]["calls"], 1)
        self.assertEqual(quality["repair"]["rate"], round(1 / 8, 4))  # one repair for eight translate calls
        self.assertEqual(quality["repair"]["cues"], 3)
        # 20d: the share of corrected cues is computed on the records: three distinct cues of 250.
        self.assertEqual(quality["cue_edits"], {"cues": 3, "rate": round(3 / 250, 4)})
        self.assertEqual(quality["retranslations"], {"count": 2, "rate": round(2 / 3, 4), "reverts": 1})
        self.assertEqual(quality["review"]["cps"], 6)
        self.assertEqual(quality["review"]["cps_rate"], round(6 / 250, 4))
        self.assertEqual(quality["review"]["too_long"], 2)
        self.assertEqual(quality["validate"], {"missing": 1})

    def test_figures_split_by_model_and_prompt_version(self):
        report = self.metrics.report(30)
        by_model = {row["model"]: row for row in report["by_model"]}
        self.assertEqual(by_model["m-1"]["cues"], 200)
        self.assertEqual(by_model["m-1"]["cue_edits"], 3)
        self.assertEqual(by_model["m-1"]["cue_edit_rate"], round(3 / 200, 4))
        self.assertEqual(by_model["m-1"]["retranslations"], 2)
        self.assertEqual(by_model["m-2"]["cue_edits"], 0)
        self.assertEqual(by_model["m-2"]["cue_edit_rate"], 0.0)
        by_prompt = {row["prompt_version"]: row for row in report["by_prompt_version"]}
        # The edits name job j1, which wrote its cues with prompt p1.
        self.assertEqual((by_prompt["p1"]["cue_edits"], by_prompt["p2"]["cue_edits"]), (3, 0))
        self.assertIsNone(by_prompt["p1"]["retranslations"])  # a retranslation does not say which prompt it replaces

    def test_20c_the_ab_score_adds_up_by_style_and_prompt_version(self):
        (row,) = self.metrics.report(30)["ab"]
        self.assertEqual(row["style"], "casual")
        self.assertEqual((row["variant"], row["current"], row["tie"], row["judged"]), (2, 1, 1, 4))
        self.assertEqual(row["score"], round((2 + 0.5) / 4, 4))  # a win is one, a tie a half; the whole-episode verdict is apart

    def test_the_daily_series_has_a_row_for_every_day(self):
        daily = self.metrics.report(5)["daily"]
        self.assertEqual(len(daily), 5)
        self.assertEqual([d["day"] for d in daily][-1], "2026-11-20")
        by_day = {d["day"]: d for d in daily}
        self.assertEqual(by_day["2026-11-20"]["calls"], 7)
        self.assertEqual(by_day["2026-11-18"]["calls"], 4)
        self.assertEqual(by_day["2026-11-18"]["failed"], 2)
        self.assertEqual(by_day["2026-11-19"]["calls"], 0)

    def test_no_data_is_none_not_zero(self):
        empty = store()
        self.addCleanup(empty.close)
        report = metrics(empty).report(30)
        self.assertIsNone(report["quality"]["cue_edits"]["rate"])
        self.assertIsNone(report["operations"]["calls"]["rate_429"])
        self.assertIsNone(report["operations"]["wait_seconds"]["median"])
        self.assertEqual(report["ab"], [])


class RollupTests(unittest.TestCase):
    def setUp(self):
        self.db = store()
        self.addCleanup(self.db.close)
        Records(self.db)

    def figures(self, m=None, days=30):
        report = (m or metrics(self.db)).report(days)
        return report["operations"]["calls"]["total"], report["quality"]["cues_translated"], report["quality"]["cue_edits"]["cues"]

    def test_a_report_can_be_asked_again_and_again(self):
        first = self.figures()
        self.assertEqual(self.figures(), first)
        self.assertEqual(self.figures(), first)

    def test_what_arrives_later_is_added_and_nothing_is_counted_twice(self):
        self.figures()
        self.db.record_llm_call("j9", provider="gemini", model="m-1", task="translate", tokens_in=1, tokens_out=1,
                                latency_ms=10, outcome="ok", at=NOW - 60, kind="translate", prompt_version="p1")
        self.db.add_event("translation.finished", job_id="j9", data={"model": "m-1", "cues": 10}, at=NOW - 60)
        self.assertEqual(self.figures(), (12, 260, 3))

    def test_the_figures_outlive_the_events_they_came_from(self):
        """The reason ``metrics_daily`` exists: cleaning up the events does not forget the day's numbers."""
        before = self.figures()
        self.db.prune_events(NOW - DAY)  # the clean-up removes the old events, and the two days ago are old
        self.assertEqual(len(self.db.events(kind="translation.finished")), 1)
        self.assertEqual(self.figures(), before)

    def test_events_of_a_pruned_day_are_not_recomputed_into_nothing(self):
        m = metrics(self.db, events_retention_days=1)  # two days ago is past the retention
        m.report(30)
        self.db.prune_events(NOW - DAY)  # what the retention does
        self.assertEqual(m.report(30)["quality"]["cues_translated"], 250)

    def test_a_longer_window_reaches_back_to_what_was_not_rolled_yet(self):
        short = self.figures(days=1)
        self.assertEqual(short[0], 7)  # only today's calls
        self.assertEqual(self.figures(days=30)[0], 11)
        self.assertEqual(self.figures(days=1)[0], 7)

    def test_the_markers_are_not_figures(self):
        self.figures()
        self.assertTrue(any(r.day == MARK_DAY for r in self.db.metric_rows(MARK_DAY)))
        self.assertFalse(any(r.day == MARK_DAY for r in self.db.metric_rows("2000-01-01")))

    def test_a_failed_roll_up_leaves_the_old_figures(self):
        self.figures()
        rows = self.db.metric_rows("2000-01-01")
        with self.assertRaises(Exception):
            self.db.metrics_replace("2026-11-20", ["calls"], [MetricRow("2026-11-20", "calls", (), float("nan")), object()])
        self.assertEqual(self.db.metric_rows("2000-01-01"), rows)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.db = store()
        self.addCleanup(self.db.close)
        Records(self.db)
        self.service = Insights(metrics(self.db), feedback=None)

    def test_metrics_route(self):
        status, body = insight_api.handle(self.service, "GET", "/api/metrics", {"days": ["7"]})
        self.assertEqual(status, 200)
        self.assertEqual(body["window"]["days"], 7)
        self.assertEqual(body["operations"]["calls"]["total"], 11)

    def test_days_must_be_a_sensible_number(self):
        for bad in ("0", "366", "x"):
            self.assertEqual(insight_api.handle(self.service, "GET", "/api/metrics", {"days": [bad]})[0], 400)

    def test_only_get_and_only_its_own_paths(self):
        self.assertIsNone(insight_api.handle(self.service, "POST", "/api/metrics", {}))
        self.assertTrue(insight_api.handles("/api/metrics"))
        self.assertTrue(insight_api.handles("/api/series/3/golden"))
        self.assertFalse(insight_api.handles("/api/series/3/memory"))
        self.assertFalse(insight_api.handles("/api/metrics/x"))


# -- the feedback loop ---------------------------------------------------------------------------------------------

MOM_LINES = {
    1: ["Mom, wait!", "I am here.", "Where is Mom?", "Dad is out.", "Mom said no."],
    2: ["I love you, Mom.", "Be careful.", "Dad is back.", "Go home.", "Mom, please!"],
}


def source_srt(lines):
    stamp = lambda ms: f"00:00:{ms // 1000:02d},{ms % 1000:03d}"  # noqa: E731
    return "\n".join(
        f"{n}\n{stamp((n - 1) * 2000)} --> {stamp((n - 1) * 2000 + 1500)}\n{text}\n" for n, text in enumerate(lines, start=1)
    )


class FeedbackEnv(CorrectionsEnv):
    def __init__(self, test, **settings):
        super().__init__(test)
        self.settings = dict(settings)
        self.telemetry = Recorder()
        self.suggestions = SuggestionService(self.store, self.library, settings_source=lambda: self.settings)
        self.feedback = FeedbackProposer(self.suggestions, settings_source=lambda: self.settings, telemetry=self.telemetry)
        self.suggestions.feedback = self.feedback
        self.service.feedback = self.feedback
        for episode, lines in MOM_LINES.items():
            (self.media / f"Show.S01E{episode:02d}.en.srt").write_text(source_srt(lines), encoding="utf-8")
            self.translate(episode)

    def handler(self, req, call_no):
        return [{"i": i, "text": "[vi] " + req.meta["source"][i]} for i in req.meta["targets"]]

    def edit(self, episode, cue, text, **extra):
        return self.service.add_correction(
            self.unit(episode), {"kind": "cue_edit", "payload": {"cue": cue, "text": text, **extra}},
        )

    def mom(self, episode, cue):
        """The machine wrote "[vi] <source>" and the admin puts "Má" where it left "Mom"."""
        source = MOM_LINES[episode][cue - 1]
        return self.edit(episode, cue, "[vi] " + source.replace("Mom", "Má"))

    @property
    def series_id(self):
        return self.store.list_series()[0].id

    def pending(self):
        return self.suggestions.listing(status="pending")["items"]


class RepeatedEditTests(unittest.TestCase):
    def test_20a_the_same_word_changed_in_three_cues_is_a_pending_term_with_the_cues_as_evidence(self):
        env = FeedbackEnv(self, suggest_from_edits=False)
        calls = len(env.fake.calls)
        env.mom(1, 1)
        env.mom(1, 3)
        self.assertEqual(env.feedback.run(env.series_id, "vi")["proposed"], 0)  # two are not a habit
        env.mom(2, 1)
        answer = env.feedback.run(env.series_id, "vi")
        self.assertEqual((answer["patterns"], answer["proposed"], answer["report"]["added"]), (1, 1, 1))
        (item,) = env.pending()
        self.assertEqual((item["kind"], item["source"]), ("term", "feedback"))
        self.assertEqual((item["payload"]["source"], item["payload"]["target"]), ("Mom", "Má"))
        self.assertIn("3 cues", item["payload"]["note"])
        self.assertEqual(sorted((e["episode"], e["cue"]) for e in item["evidence"]), [("S01E01", 1), ("S01E01", 3), ("S01E02", 1)])
        self.assertTrue(all(e["unit_id"] for e in item["evidence"]))
        self.assertIn("[vi] Mom, wait! → [vi] Má, wait!", [e["text"] for e in item["evidence"]])
        self.assertEqual(env.glossaries.load("series", "bazarr-7", "vi").terms, ())  # nothing is applied
        self.assertEqual(len(env.fake.calls), calls)  # and no model was asked

    def test_it_looks_by_itself_after_each_edit(self):
        env = FeedbackEnv(self)
        env.mom(1, 1)
        env.mom(1, 3)
        self.assertEqual(env.pending(), [])
        env.mom(2, 1)
        (item,) = env.pending()
        self.assertEqual(item["source"], "feedback")
        self.assertTrue(any(kind == "feedback.mined" for kind, _ in env.telemetry.events))

    def test_the_switch_turns_it_off_but_the_admin_can_still_ask(self):
        env = FeedbackEnv(self, suggest_from_edits=False)
        for episode, cue in [(1, 1), (1, 3), (2, 1)]:
            env.mom(episode, cue)
        self.assertEqual(env.pending(), [])
        answer = suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/from-edits", {}, {"series_id": env.series_id, "lang": "vi"})
        self.assertEqual((answer[0], answer[1]["proposed"]), (200, 1))
        self.assertEqual(len(env.pending()), 1)

    def test_asking_again_does_not_make_a_second_one_and_a_rejection_holds(self):
        env = FeedbackEnv(self)
        for episode, cue in [(1, 1), (1, 3), (2, 1)]:
            env.mom(episode, cue)
        env.feedback.run(env.series_id, "vi")
        self.assertEqual(len(env.pending()), 1)
        env.suggestions.reject(env.pending()[0]["id"])
        env.mom(1, 5)  # a fourth cue: the same habit, said no to already
        env.feedback.run(env.series_id, "vi")
        self.assertEqual(env.pending(), [])
        self.assertEqual(env.suggestions.listing(status="rejected")["counts"]["rejected"], 1)

    def test_accepting_it_is_the_normal_way_into_the_glossary(self):
        env = FeedbackEnv(self)
        for episode, cue in [(1, 1), (1, 3), (2, 1)]:
            env.mom(episode, cue)
        env.suggestions.accept(env.pending()[0]["id"])
        (term,) = env.glossaries.load("series", "bazarr-7", "vi").terms
        self.assertEqual((term.source, term.target), ("Mom", "Má"))

    def test_a_word_added_where_there_was_none_is_no_pattern(self):
        env = FeedbackEnv(self)
        for episode, cue in [(1, 2), (1, 4), (2, 2)]:
            env.edit(episode, cue, "[vi] " + MOM_LINES[episode][cue - 1] + " nhé")  # only a word added
        self.assertEqual(env.pending(), [])

    def test_the_machine_text_survives_editing_a_cue_again(self):
        env = FeedbackEnv(self, suggest_from_edits=False)
        env.edit(1, 1, "[vi] Mẹ, wait!")
        env.edit(1, 1, "[vi] Má, wait!")
        (pair,) = env.store.series_cue_edit_pairs(env.series_id, "vi")
        self.assertEqual((pair.before, pair.after, pair.source), ("[vi] Mom, wait!", "[vi] Má, wait!", "Mom, wait!"))
        self.assertEqual((pair.season, pair.episode, pair.cue), (1, 1, 1))

    def test_a_loop_that_fails_never_fails_the_edit(self):
        env = FeedbackEnv(self)
        env.feedback.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        answer = env.mom(1, 1)
        self.assertTrue(answer["ok"])

    def test_a_fake_hook_is_told_which_unit_and_language(self):
        env = FeedbackEnv(self)
        told = []
        env.service.feedback = type("Hook", (), {"after_edit": lambda self, unit, lang: told.append((unit.id, lang))})()
        env.mom(2, 1)
        self.assertEqual(told, [(env.unit(2), "vi")])

    def test_bad_requests_say_what_is_wrong(self):
        env = FeedbackEnv(self)
        handle = lambda body: suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/from-edits", {}, body)  # noqa: E731
        self.assertEqual(handle({"lang": "vi"})[0], 400)
        self.assertEqual(handle({"series_id": env.series_id, "lang": "../x"})[0], 400)
        self.assertEqual(handle({"series_id": 999, "lang": "vi"})[0], 404)
        self.assertEqual(MIN_REPEATS, 3)


class GoldenSetTests(unittest.TestCase):
    def build(self):
        env = FeedbackEnv(self, suggest_from_edits=False)
        env.mom(1, 1)
        env.mom(2, 1)
        env.edit(1, 2, "[vi] Tôi ở đây.")
        return env

    def test_the_golden_set_is_exactly_the_corrected_cues(self):
        env = self.build()
        name, text = env.feedback.golden(env.series_id, "vi")
        self.assertEqual(name, "bazarr-7.vi.golden.json")
        cues = golden.loads(text)
        self.assertEqual(
            [(c.season, c.episode, c.cue, c.source, c.text) for c in cues],
            [(1, 1, 1, "Mom, wait!", "[vi] Má, wait!"), (1, 1, 2, "I am here.", "[vi] Tôi ở đây."),
             (1, 2, 1, "I love you, Mom.", "[vi] I love you, Má.")],
        )

    def test_a_correction_that_was_undone_is_not_in_it(self):
        env = self.build()
        (correction,) = [c for c in env.store.corrections(env.unit(1), "vi") if c.payload["cue"] == 2]
        env.service.remove_correction(env.unit(1), correction.id)
        _, text = env.feedback.golden(env.series_id, "vi")
        self.assertEqual([(c.episode, c.cue) for c in golden.loads(text)], [(1, 1), (2, 1)])

    def test_each_entry_still_matches_its_source_file(self):
        env = self.build()
        _, text = env.feedback.golden(env.series_id, "vi")
        from subcore import srt

        source = srt.parse_file(env.media / "Show.S01E01.en.srt")
        self.assertEqual(sorted(golden.for_episode(golden.loads(text), 1, 1, source)), [1, 2])

    def test_routes_serve_it_inline_and_as_a_download(self):
        env = self.build()
        service = Insights(metrics(env.store), env.feedback)
        status, body = insight_api.handle(service, "GET", f"/api/series/{env.series_id}/golden", {"lang": ["vi"]})
        self.assertEqual((status, len(body["cues"]), body["series"]), (200, 3, "Show"))
        status, name, text = insight_api.download(service, f"/api/series/{env.series_id}/golden", {"lang": ["vi"], "download": ["1"]})
        self.assertEqual((status, name), (200, "bazarr-7.vi.golden.json"))
        self.assertEqual(json.loads(text), body)
        self.assertIsNone(insight_api.download(service, f"/api/series/{env.series_id}/golden", {"lang": ["vi"]}))
        self.assertEqual(insight_api.handle(service, "GET", f"/api/series/{env.series_id}/golden", {})[0], 400)
        self.assertEqual(insight_api.handle(service, "GET", "/api/series/999/golden", {"lang": ["vi"]})[0], 404)
        self.assertEqual(insight_api.download(service, "/api/series/999/golden", {"lang": ["vi"], "download": ["1"]})[0], 404)


class TranslationEventTests(unittest.TestCase):
    def test_a_finished_translation_says_how_many_cues_and_what_the_review_found(self):
        env = CorrectionsEnv(self)
        recorder = Recorder()
        env.attempt.telemetry = recorder
        env.translate(1)
        (data,) = [d for kind, d in recorder.events if kind == "translation.finished"]
        self.assertEqual(data["cues"], 12)
        self.assertIsInstance(data["review"], dict)
        self.assertEqual(SETTINGS["gst_model"], "m-1")


class HttpTests(unittest.TestCase):
    """The worker's own server routes them (and asks for a login first): the handler is wired, not only the router."""

    def setUp(self):
        self.env = FeedbackEnv(self, suggest_from_edits=False)
        for episode, cue in [(1, 1), (1, 3), (2, 1)]:
            self.env.mom(episode, cue)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        static = Path(tmp.name)
        (static / "index.html").write_text("<html>console</html>", encoding="utf-8")
        ctx = {
            "static_dir": str(static), "queue_dir": str(static / "queue"), "log_dir": str(static / "logs"),
            "console": ConsoleActions(JobQueue(self.env.store)),
            "insights": Insights(metrics(self.env.store), self.env.feedback), "suggestions": self.env.suggestions,
        }
        auth = ConsoleAuth.from_env({"CONSOLE_USER": "admin", "CONSOLE_PASSWORD": "correct horse battery"})
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.ConsoleHandler)
        server.ctx, server.auth = ctx, auth
        from subserver.auth import LoginThrottle, SessionStore

        server.sessions, server.throttle = SessionStore.for_auth(auth), LoginThrottle()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def get(self, path, login=True, method="GET", body=None):
        headers = {"Authorization": "Basic " + base64.b64encode(b"admin:correct horse battery").decode()} if login else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            response = conn.getresponse()
            return response.status, response.headers, response.read().decode("utf-8")
        finally:
            conn.close()

    def test_metrics_and_the_golden_set_need_a_login(self):
        sid = self.env.series_id
        for path in ("/api/metrics", f"/api/series/{sid}/golden?lang=vi"):
            self.assertEqual(self.get(path, login=False)[0], 401, path)

    def test_metrics_over_http(self):
        status, headers, body = self.get("/api/metrics?days=7")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body)["window"]["days"], 7)
        self.assertEqual(self.get("/api/metrics?days=0")[0], 400)

    def test_the_golden_set_is_a_json_answer_or_a_file(self):
        sid = self.env.series_id
        status, _, body = self.get(f"/api/series/{sid}/golden?lang=vi")
        self.assertEqual((status, len(json.loads(body)["cues"])), (200, 3))
        status, headers, text = self.get(f"/api/series/{sid}/golden?lang=vi&download=1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="bazarr-7.vi.golden.json"')
        self.assertEqual(json.loads(text), json.loads(body))
        self.assertEqual(self.get(f"/api/series/{sid}/golden")[0], 400)

    def test_looking_at_the_edits_over_http(self):
        status, _, body = self.get(
            "/api/suggestions/from-edits", method="POST", body={"series_id": self.env.series_id, "lang": "vi"},
        )
        answer = json.loads(body)
        self.assertEqual((status, answer["proposed"], answer["report"]["added"]), (200, 1, 1))


if __name__ == "__main__":
    unittest.main()
