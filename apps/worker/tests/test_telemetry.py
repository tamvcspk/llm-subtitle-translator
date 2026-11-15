"""P5: event log, call records, prompt bodies, log level, secrets and quality signals (SPEC criterion 17, NFR-11).

Every provider is a fake; nothing touches the network. Telemetry runs on its real writer thread, so a test
calls ``flush()`` before it reads what was written.
"""

import base64
import gzip
import io
import json
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from subcore import srt
from subcore.providers import CallLog, FakeProvider, LLMRequest, ResilientProvider, Unavailable
from subcore.providers.fake import echo_items

import worker
from gst_worker import queue as gst_queue
from gst_worker.bodies import BodyLog, encode_record
from gst_worker.config import normalize_app_config
from gst_worker.glossaries import GlossaryFiles
from gst_worker.library import Library
from gst_worker.logs import RedactingFormatter, set_log_level
from gst_worker.ports import BodyRef, Telemetry as TelemetryPort
from gst_worker.redaction import REDACTED, Redactor
from gst_worker.settings import SettingsService
from gst_worker.store import Store
from gst_worker.telemetry import NULL, Telemetry, events_page
from gst_worker.translation_attempt import TranslationAttempt
from gst_worker.units import UnitService
from gst_worker.versions import TranslationVersions

SECRET = "AIzaSyTESTKEY0123456789abcdefghij"
SOURCE = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello <i>there</i>\n\n"
    "2\n00:00:03,000 --> 00:00:04,500\nHow are you?\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\nFine.\n"
)
DAY = 86_400
START = 1_700_000_000  # 2023-11-14


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now


class Media:
    """MediaLibrary double: Bazarr cannot say anything and nothing is rescanned."""

    def describe(self, *args):
        return None

    def series_episodes(self, *args):
        return []

    def target_still_missing(self, job):
        return None

    def refresh(self, job):
        return None


class Meta:
    def __init__(self, text=""):
        self.text = text

    def description(self, job):
        return self.text


def drop_cue(number):
    return lambda req, call_no: [item for item in echo_items(req, "[vi] ") if item["i"] != number]


class Env:
    """A temp directory with a store, real telemetry, a library, an attempt and a queue worker on fake providers."""

    def __init__(self, test, *, handlers=None, settings=None, clock=None, overview="", queue_size=2000):
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.media = self.root / "media" / "Show" / "Season 1"
        self.media.mkdir(parents=True)
        self.settings = {
            "gst_model": "m-1", "gst_fallback_model": "m-2", "log_level": "INFO", "log_prompt_bodies": "all",
            "job_settle_seconds": 0, "gemini_api_key": SECRET, **(settings or {}),
        }
        self.clock = clock or Clock()
        self.store = Store(self.root / "data" / "app.db")
        test.addCleanup(self.store.close)
        self.redactor = Redactor()
        self.bodies = BodyLog(self.root / "data" / "logs" / "llm", clock=self.clock)
        self.telemetry = Telemetry(
            self.store, usage=self.store, bodies=self.bodies, settings_source=lambda: self.settings,
            redactor=self.redactor, clock=self.clock, queue_size=queue_size,
        )
        test.addCleanup(self.telemetry.close)  # runs before the store closes
        self.telemetry.apply_settings(self.settings)
        self.fakes = {
            model: FakeProvider(handler=(handlers or {}).get(model), prefix="[vi] ", model=model)
            for model in ("m-1", "m-2")
        }

        def factory(settings, model, *, deadline=None, on_call=None):
            return ResilientProvider(
                self.fakes[model], deadline=deadline, on_call=on_call, base_delay=0.0, max_attempts=2,
                sleep=lambda _s: None,
            )

        self.versions = TranslationVersions(self.store, self.root / "data" / "backups", telemetry=self.telemetry)
        self.glossaries = GlossaryFiles(self.root / "data")
        self.library = Library(self.store, self.glossaries, self.versions)
        self.attempt = TranslationAttempt(
            versions=self.versions, usage=self.store, library=self.library, provider_factory=factory,
            telemetry=self.telemetry,
        )
        self.queue_dir = str(self.root / "queue")
        self.worker = worker.QueueWorker(
            self.queue_dir, self.settings, None, None, attempt=self.attempt, media=Media(), metadata=Meta(overview),
            telemetry=self.telemetry, store=self.store,
        )
        self.service = UnitService(
            self.library, self.glossaries, self.versions, self.worker.queue, telemetry=self.telemetry,
        )

    def source(self, episode=1):
        path = self.media / f"Show.S01E{episode:02d}.en.srt"
        if not path.exists():  # each episode differs, so one episode's translation is never reused for another
            path.write_text(SOURCE.replace("Fine.", f"Fine, episode {episode}."), encoding="utf-8")
        return path

    def output(self, episode=1):
        return self.media / f"Show.S01E{episode:02d}.vi.srt"

    def enqueue(self, episode=1):
        created = self.worker.queue.enqueue(
            {
                "subtitle_path": str(self.source(episode)), "video_path": str(self.media / f"Show.S01E{episode:02d}.mkv"),
                "media_type": "series", "series_id": "7", "media_id": str(100 + episode), "source_code": "en",
                "provider": "gemini-console",
            },
            [{"code": "vi", "language": "Vietnamese", "enabled": True}],
        )
        return created[0]

    def run_job(self, episode=1):
        job_id = self.enqueue(episode)
        while self.worker.process_once():
            pass
        self.telemetry.flush()
        return job_id

    def kinds(self, **query):
        return [e.kind for e in reversed(self.store.events(limit=1000, **query))]

    def unit(self, episode=1):
        series = self.store.list_series()[0]
        return next(u for u in self.store.list_units(series.id) if u.episode == episode).id

    def all_text(self):
        """Everything the system wrote down that an admin could read back: events, signals, bodies, queue files."""
        parts = [json.dumps([vars(e) for e in self.store.events(limit=1000)], default=str)]
        parts.append(json.dumps([vars(s) for s in self.store.signals(limit=5000)], default=str))
        for segment in self.bodies.segments():
            raw = segment.path.read_bytes()
            parts.append((gzip.decompress(raw) if segment.closed else raw).decode("utf-8"))
        for path in Path(self.queue_dir).rglob("*"):
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8"))
        parts.append(json.dumps(self.worker.queue.snapshot(), default=str))
        parts.append(json.dumps([dict(row) for row in self.store.query("SELECT * FROM jobs")], default=str))
        parts.append(json.dumps(events_page(self.telemetry, {"limit": ["1000"]}), default=str))
        return "\n".join(parts)


def call_log(*, outcome="ok", user="hello", system="sys", response="[]", kind="translate", **extra):
    return CallLog(
        provider="fake", model="m-1", task="translate", tokens_in=10, tokens_out=5, latency_ms=7, outcome=outcome,
        kind=kind, request=LLMRequest(system=system, user=user), response_text=response,
        http_status=200 if outcome == "ok" else 503, **extra,
    )


class DecisionEventTests(unittest.TestCase):
    """17a: every decision leaves an event with the job or unit and a reason a person can read."""

    def test_a_job_that_runs_leaves_each_decision_with_its_reason(self):
        env = Env(self)
        job_id = env.run_job()

        events = list(reversed(env.store.events(job_id=job_id, limit=100)))
        self.assertEqual(
            [e.kind for e in events],
            ["job.created", "job.claimed", "model.selected", "translation.finished", "version.published", "job.done"],
        )
        for event in events:
            self.assertTrue(event.reason, f"{event.kind} has no reason")
        selected = events[2]
        self.assertEqual((selected.data["model"], selected.data["layer"]), ("m-1", "light"))
        self.assertEqual(events[-1].data["status"], "translated")
        unit = env.unit()
        self.assertEqual(
            sorted({e.kind for e in env.store.events(unit_id=unit)}), ["translation.finished", "version.published"]
        )
        self.assertTrue(env.output().exists())

    def test_a_job_that_is_skipped_says_why(self):
        env = Env(self)
        job_id = env.enqueue()
        env.output().write_text("someone else's subtitle", encoding="utf-8")  # appears after the job was queued
        self.assertTrue(env.worker.process_once())
        env.telemetry.flush()

        skipped = [e for e in env.store.events(job_id=job_id) if e.kind == "job.skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertIn("already exists", skipped[0].reason)
        self.assertEqual(env.store.llm_calls(), [])
        self.assertEqual(env.output().read_text(encoding="utf-8"), "someone else's subtitle")

    def test_not_queueing_a_job_says_why(self):
        env = Env(self)
        env.output().write_text("exists", encoding="utf-8")
        self.assertEqual(
            env.worker.queue.enqueue(
                {"subtitle_path": str(env.source()), "source_code": "en"},
                [{"code": "vi", "language": "Vietnamese", "enabled": True}],
            ),
            [],
        )
        env.telemetry.flush()
        (event,) = env.store.events(kind="enqueue.skipped")
        self.assertIn("already exists", event.reason)
        self.assertEqual(event.data["target"], "vi")

    def test_a_hard_job_is_escalated_to_the_strong_model_and_both_steps_are_recorded(self):
        env = Env(self, handlers={"m-1": drop_cue(2)})  # the light model never answers cue 2
        job_id = env.run_job()

        events = list(reversed(env.store.events(job_id=job_id, limit=100)))
        kinds = [e.kind for e in events]
        self.assertEqual(kinds.count("model.selected"), 2)
        (escalated,) = [e for e in events if e.kind == "job.escalated"]
        self.assertEqual((escalated.data["from_model"], escalated.data["to_model"]), ("m-1", "m-2"))
        self.assertIn("could not produce valid output", escalated.reason)
        layers = [e.data["layer"] for e in events if e.kind == "model.selected"]
        self.assertEqual(layers, ["light", "strong"])
        strong = [e for e in events if e.kind == "model.selected"][1]
        self.assertEqual(strong.data["escalated_from"], "m-1")
        self.assertEqual(events[-1].kind, "job.done")
        # The strong model's translation is what was published.
        self.assertEqual(env.store.current_version(str(env.output())).model, "m-2")

    def test_an_outage_makes_the_job_wait_with_the_time_to_retry(self):
        def down(req, call_no):
            raise Unavailable("503 overloaded")

        env = Env(self, handlers={"m-1": down}, settings={"gst_fallback_model": ""})
        job_id = env.run_job()

        (deferred,) = [e for e in env.store.events(job_id=job_id) if e.kind == "job.waiting"]
        self.assertEqual(deferred.data["wait"], "rate")
        self.assertGreater(deferred.data["until"], 0)
        self.assertIn("unavailable", deferred.reason)
        self.assertFalse(env.output().exists())

    def test_reusing_a_stored_translation_is_recorded_as_a_decision(self):
        env = Env(self)
        env.run_job()
        env.output().unlink()  # the file went away; the same input and context are asked for again
        env.run_job()
        self.assertIn("translation.cached", env.kinds())

    def test_events_are_found_by_job_unit_and_kind_family(self):
        env = Env(self)
        first = env.run_job(1)
        second = env.run_job(2)

        self.assertTrue(all(e.job_id == first for e in env.store.events(job_id=first)))
        self.assertTrue(all(e.kind.startswith("job.") for e in env.store.events(kind="job.")))
        self.assertEqual([e.kind for e in env.store.events(kind="job.done")], ["job.done", "job.done"])
        self.assertTrue(all(e.unit_id == env.unit(2) for e in env.store.events(unit_id=env.unit(2))))
        newest = env.store.events(limit=3)
        older = env.store.events(before_id=newest[-1].id, limit=3)
        self.assertTrue(all(o.id < newest[-1].id for o in older))
        self.assertNotEqual(first, second)

    def test_the_events_page_filters_and_pages(self):
        env = Env(self)
        env.run_job()
        page = events_page(env.telemetry, {"kind": ["job."], "limit": ["2"]})
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["next_before"], page["items"][-1]["id"])
        self.assertTrue(all(item["kind"].startswith("job.") for item in page["items"]))
        self.assertEqual(page["telemetry"]["dropped"], 0)
        everything = events_page(env.telemetry, {"limit": ["1000"]})
        self.assertIsNone(everything["next_before"])
        self.assertEqual(events_page(env.telemetry, {"level": ["ERROR"]})["items"], [])
        with self.assertRaises(ValueError):
            events_page(env.telemetry, {"unit": ["abc"]})


class CallRecordTests(unittest.TestCase):
    """17b: a call record carries the prompt version, the hashes and the number of cues that needed a repair."""

    def test_calls_keep_how_they_were_made(self):
        calls_seen = []

        def handler(req, call_no):
            calls_seen.append(call_no)
            return drop_cue(3)(req, call_no) if call_no == 1 else echo_items(req, "[vi] ")

        env = Env(self, handlers={"m-1": handler}, settings={"llm_cost_in_per_mtok": 1.0, "llm_cost_out_per_mtok": 2.0})
        job_id = env.run_job()

        first, repair = env.store.llm_calls(job_id)
        self.assertEqual((first.kind, repair.kind), ("translate", "repair"))
        self.assertEqual((first.repairs, repair.repairs), (0, 1))
        self.assertEqual({first.prompt_version, repair.prompt_version}, {"p3-1"})
        self.assertEqual((first.http_status, first.retries, first.batch), (200, 0, 0))
        version = env.store.current_version(str(env.output()))
        self.assertEqual(first.glossary_hash, version.glossary_hash)
        self.assertEqual((first.bundle_hash, first.style_hash), ("", ""))  # P11 and P12 fill these in
        self.assertAlmostEqual(first.cost_est, (first.tokens_in * 1.0 + first.tokens_out * 2.0) / 1_000_000)

    def test_a_failed_call_keeps_its_http_code_and_attempt_number(self):
        failures = []

        def handler(req, call_no):
            if call_no == 1:
                error = Unavailable("overloaded")
                error.http_status = 503
                failures.append(error)
                raise error
            return echo_items(req, "[vi] ")

        env = Env(self, handlers={"m-1": handler})
        job_id = env.run_job()
        failed, good = env.store.llm_calls(job_id)
        self.assertEqual((failed.outcome, failed.http_status, failed.retries), ("unavailable", 503, 0))
        self.assertEqual((good.outcome, good.http_status, good.retries), ("ok", 200, 1))

    def test_cost_is_zero_without_prices(self):
        env = Env(self)
        job_id = env.run_job()
        self.assertEqual([c.cost_est for c in env.store.llm_calls(job_id)], [0.0])


class BodyTests(unittest.TestCase):
    """17c: prompts and answers go to rotating JSONL, within the limits and with a consistent index."""

    def test_the_prompt_and_the_answer_of_each_call_can_be_read_back(self):
        env = Env(self)
        job_id = env.run_job()
        (call,) = env.store.llm_calls(job_id)
        ref = env.store.body_ref(call.id)
        self.assertIsInstance(ref, BodyRef)
        record = env.bodies.read(ref)
        self.assertEqual((record["call"], record["job"], record["kind"], record["outcome"]), (call.id, job_id, "translate", "ok"))
        self.assertIn("Hello", record["user"])
        self.assertIn("[vi] Hello", record["response"])
        self.assertIn("Vietnamese", record["system"])

    def test_off_keeps_no_body_and_writes_no_file(self):
        env = Env(self, settings={"log_prompt_bodies": "off"})
        env.run_job()
        self.assertIsNone(env.store.body_ref(env.store.llm_calls()[0].id))
        self.assertEqual(env.bodies.segments(), [])

    def test_errors_keeps_only_failed_calls(self):
        def handler(req, call_no):
            if call_no == 1:
                raise Unavailable("overloaded")
            return echo_items(req, "[vi] ")

        env = Env(self, handlers={"m-1": handler}, settings={"log_prompt_bodies": "errors"})
        env.run_job()
        failed, good = env.store.llm_calls()
        self.assertIsNotNone(env.store.body_ref(failed.id))
        self.assertIsNone(env.store.body_ref(good.id))

    def test_sample_keeps_failures_and_one_call_in_ten(self):
        env = Env(self, settings={"log_prompt_bodies": "sample"})
        for _ in range(30):
            env.telemetry.llm_call("j", call_log())
        env.telemetry.llm_call("j", call_log(outcome="unavailable"))
        env.telemetry.flush()
        calls = env.store.llm_calls("j")
        kept = [c for c in calls if env.store.body_ref(c.id)]
        self.assertEqual(len(kept), 3 + 1)  # calls 10, 20, 30 and the failure
        self.assertEqual(kept[-1].outcome, "unavailable")

    def test_a_record_longer_than_the_limit_is_cut_to_fit(self):
        line = encode_record({"call": 1, "user": "x" * 50_000, "system": "s" * 10, "response": "r" * 100}, 4096)
        self.assertLessEqual(len(line), 4096)
        record = json.loads(line)
        self.assertTrue(record["truncated"])
        self.assertTrue(record["user"].endswith("[truncated]"))
        self.assertEqual(record["response"], "r" * 100)  # the short fields stay whole

        env = Env(self, settings={"log_body_max_bytes": 2048})
        env.telemetry.llm_call("j", call_log(user="u" * 40_000))
        env.telemetry.flush()
        ref = env.store.body_ref(env.store.llm_calls("j")[0].id)
        self.assertLessEqual(ref.length, 2048)

    def test_segments_are_compressed_when_closed_and_still_readable(self):
        env = Env(self)
        env.telemetry.llm_call("a", call_log(user="first day"))
        env.telemetry.flush()
        first_ref = env.store.body_ref(env.store.llm_calls("a")[0].id)
        env.clock.now += DAY
        env.telemetry.llm_call("b", call_log(user="second day"))
        env.telemetry.flush()

        names = sorted(p.name for p in env.bodies.directory.iterdir())
        self.assertEqual(names[0].endswith(".jsonl.gz"), True)
        self.assertTrue(names[1].endswith(".jsonl"))
        self.assertEqual(env.bodies.read(first_ref)["user"], "first day")
        second_ref = env.store.body_ref(env.store.llm_calls("b")[0].id)
        self.assertNotEqual(first_ref.file, second_ref.file)

    def test_files_past_the_retention_are_deleted_and_the_index_says_rotated(self):
        env = Env(self, settings={"log_retention_days": 30})
        for day in (0, 1, 40):
            env.clock.now = START + day * DAY
            env.telemetry.llm_call(f"d{day}", call_log(user=f"day {day}"))
            env.telemetry.flush()
        env.telemetry.maintain()

        refs = {day: env.store.body_ref(env.store.llm_calls(f"d{day}")[0].id) for day in (0, 1, 40)}
        self.assertTrue(refs[0].rotated and refs[1].rotated)
        self.assertFalse(refs[40].rotated)
        self.assertIsNone(env.bodies.read(refs[0]))
        self.assertEqual(env.bodies.read(refs[40])["user"], "day 40")
        self.assertEqual(len(env.bodies.segments()), 1)  # only the newest file is left

    def test_the_size_limit_removes_the_oldest_files_first_and_keeps_the_index_consistent(self):
        env = Env(self, settings={"log_max_mb": 1})
        for number in range(16):
            noise = base64.b64encode(os.urandom(150_000)).decode()  # does not compress away
            env.telemetry.llm_call(f"c{number}", call_log(user=noise))
        env.telemetry.flush()
        env.telemetry.maintain()

        segments = env.bodies.segments()
        closed = sum(s.size for s in segments if s.closed)
        self.assertLessEqual(closed, 1024 * 1024)
        calls = env.store.llm_calls()
        refs = [env.store.body_ref(c.id) for c in calls]
        rotated = [r for r in refs if r.rotated]
        kept = [r for r in refs if not r.rotated]
        self.assertTrue(rotated, "nothing was removed although the limit was passed")
        self.assertTrue(kept)
        for ref in rotated:
            self.assertIsNone(env.bodies.read(ref))
        for ref in kept:
            self.assertIsNotNone(env.bodies.read(ref), f"{ref.file} is indexed but unreadable")
        # What went first is what was removed: every rotated call precedes every kept one.
        order = {c.id: n for n, c in enumerate(calls)}
        self.assertLess(max(order[r.call_id] for r in rotated), min(order[r.call_id] for r in kept))

    def test_events_past_their_retention_are_pruned(self):
        env = Env(self, settings={"events_retention_days": 90})
        env.telemetry.event("old.thing", reason="long ago")
        env.telemetry.flush()
        env.clock.now += 100 * DAY
        env.telemetry.event("new.thing", reason="today")
        env.telemetry.flush()
        env.telemetry.maintain()
        self.assertEqual([e.kind for e in env.store.events()], ["new.thing"])


class LogLevelTests(unittest.TestCase):
    """17d: a new log level is in force at once, without a restart."""

    def setUp(self):
        self.addCleanup(set_log_level, "INFO")

    def test_changing_the_level_changes_the_log_and_which_events_are_kept(self):
        env = Env(self)
        env.telemetry.event("probe.debug", level="DEBUG", reason="a detail")
        env.telemetry.event("probe.info", reason="a decision")
        env.telemetry.flush()
        self.assertEqual(env.kinds(kind="probe."), ["probe.info"])

        env.settings["log_level"] = "DEBUG"
        env.worker.apply_settings(env.settings)  # what saving the settings does
        self.assertEqual(logging.getLogger().level, logging.DEBUG)
        env.telemetry.event("probe.debug2", level="DEBUG", reason="a detail")
        env.telemetry.flush()
        self.assertIn("probe.debug2", env.kinds(kind="probe."))

        env.settings["log_level"] = "WARNING"
        env.worker.apply_settings(env.settings)
        self.assertEqual(logging.getLogger().level, logging.WARNING)
        env.telemetry.event("probe.info2", reason="a decision")
        env.telemetry.event("probe.warn", level="WARNING", reason="a problem")
        env.telemetry.flush()
        kinds = env.kinds(kind="probe.")
        self.assertNotIn("probe.info2", kinds)
        self.assertIn("probe.warn", kinds)

    def test_an_event_is_one_json_line_in_the_log(self):
        env = Env(self)
        with self.assertLogs("gst.event", level="INFO") as captured:
            env.telemetry.event("job.skipped", job_id="abc", unit_id=4, reason="because", target="vi")
        message = captured.records[0].getMessage()
        self.assertTrue(message.startswith("EVENT {"))
        self.assertNotIn("\n", message)
        body = json.loads(message[len("EVENT "):])
        self.assertEqual((body["kind"], body["job"], body["unit"], body["reason"], body["target"]), ("job.skipped", "abc", 4, "because", "vi"))

    def test_the_settings_are_validated_and_saved(self):
        config = normalize_app_config({"log_level": "debug", "log_prompt_bodies": "SAMPLE", "log_max_mb": "-4",
                                       "log_body_max_bytes": 3, "log_retention_days": "x"})
        self.assertEqual((config["log_level"], config["log_prompt_bodies"]), ("DEBUG", "sample"))
        self.assertEqual((config["log_max_mb"], config["log_body_max_bytes"], config["log_retention_days"]), (0, 1024, 30))
        junk = normalize_app_config({"log_level": "loud", "log_prompt_bodies": "everything"})
        self.assertEqual((junk["log_level"], junk["log_prompt_bodies"]), ("INFO", "all"))
        store = Store(":memory:")
        try:
            service = SettingsService(store, env={"BAZARR_CONFIG_PATH": ""})
            service.save({"log_level": "ERROR", "log_max_mb": 7, "llm_cost_in_per_mtok": "0.5"})
            settings = service.settings()
        finally:
            store.close()
        self.assertEqual((settings["log_level"], settings["log_max_mb"], settings["llm_cost_in_per_mtok"]), ("ERROR", 7, 0.5))


class SecretTests(unittest.TestCase):
    """17e: no key, password, cookie or header in the log, an event, a body or an API answer."""

    def test_known_values_and_shapes_are_removed(self):
        redactor = Redactor(["hunter2-password"])
        text = (
            "key=AIzaSyABCDEFGHIJ0123 token Bearer abcdefghijklmnop1234 Cookie: gst_session=abc.def; other=1\n"
            "Authorization: Basic dXNlcjpwYXNz\npassword=swordfish and hunter2-password ok scrypt$16384$8$1$aa$bb"
        )
        clean = redactor.redact(text)
        for leaked in ("AIzaSyABCDEFGHIJ0123", "abcdefghijklmnop1234", "gst_session=abc", "dXNlcjpwYXNz", "swordfish",
                       "hunter2-password", "scrypt$"):
            self.assertNotIn(leaked, clean)
        self.assertIn(" ok ", clean)  # the text around the secrets is untouched

    def test_dialogue_that_mentions_a_password_is_left_alone_in_bodies(self):
        redactor = Redactor()
        line = "- The password: swordfish, remember it."
        self.assertEqual(redactor.redact(line, loose=False), line)
        self.assertNotIn("swordfish", redactor.redact(line))
        self.assertNotIn("AIzaSyABCDEFGHIJ0123", redactor.redact(line + " AIzaSyABCDEFGHIJ0123", loose=False))

    def test_a_mapping_key_that_names_a_secret_loses_its_value(self):
        clean = Redactor().clean({"api_key": "plain", "nested": {"Authorization": "Basic x", "tokens_in": 5, "ok": "fine"},
                                  "list": [{"password": "p"}], "n": 3})
        self.assertEqual(clean["api_key"], REDACTED)
        self.assertEqual(clean["nested"], {"Authorization": REDACTED, "tokens_in": 5, "ok": "fine"})
        self.assertEqual(clean["list"], [{"password": REDACTED}])
        self.assertEqual(clean["n"], 3)

    def test_the_log_formatter_hides_secrets_in_messages_and_tracebacks(self):
        redactor = Redactor(["very-secret-value"])
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s", redactor=redactor))
        logger = logging.getLogger("test.redaction")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        try:
            raise RuntimeError("the key very-secret-value was refused")
        except RuntimeError:
            logger.exception("call failed for %s", "very-secret-value")
        self.assertNotIn("very-secret-value", stream.getvalue())
        self.assertIn(REDACTED, stream.getvalue())

    def test_the_process_log_is_configured_with_the_redacting_formatter(self):
        from gst_worker.logs import configure_logging
        from gst_worker.redaction import DEFAULT

        root = logging.getLogger()
        saved, level = list(root.handlers), root.level
        self.addCleanup(lambda: (root.handlers.clear(), root.handlers.extend(saved), root.setLevel(level)))
        DEFAULT.add_values(["configured-secret-value"])
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, "DEBUG")
            logging.getLogger("probe").warning("the key configured-secret-value and Bearer abcdefghijklmnop1234")
            for handler in root.handlers:
                handler.flush()
            text = (Path(tmp) / "worker.log").read_text(encoding="utf-8")
            for handler in list(root.handlers):  # release the file before the folder goes
                handler.close()
        self.assertIn("the key", text)
        self.assertNotIn("configured-secret-value", text)
        self.assertNotIn("abcdefghijklmnop1234", text)
        self.assertEqual(root.level, logging.DEBUG)

    def test_a_secret_never_reaches_events_bodies_queue_files_or_the_log(self):
        def broken(req, call_no):
            raise RuntimeError(f"transport failed for key {SECRET}")  # not a typed provider error: the job fails

        def leaky(req, call_no):
            if call_no == 1:  # the first episode: the model's answer repeats the key
                return [{"i": i, "text": f"{req.meta['source'][i]} leak {SECRET}"} for i in req.meta["targets"]]
            return broken(req, call_no)

        env = Env(self, handlers={"m-1": leaky}, overview=f"A film. (note to self: {SECRET})")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s", redactor=env.redactor))
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        env.run_job(1)
        failing = env.run_job(2)

        failed = [e for e in env.store.events(job_id=failing) if e.kind == "job.failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn(REDACTED, failed[0].reason)
        everything = env.all_text() + stream.getvalue()
        self.assertNotIn(SECRET, everything)
        self.assertIn(REDACTED, everything)
        # The bodies were written, and so the scan above looked at the prompt and the answer that held the key.
        (call,) = env.store.llm_calls(env.store.events(kind="job.done")[0].job_id)
        record = env.bodies.read(env.store.body_ref(call.id))
        self.assertIn(REDACTED, record["system"])
        self.assertIn(REDACTED, record["response"])

    def test_a_key_in_the_settings_is_hidden_from_events_about_them(self):
        env = Env(self)
        env.settings = {**env.settings, "gemini_api_key": "NEWKEY-0123456789", "gst_model": "m-9"}
        env.worker.apply_settings(env.settings)
        env.telemetry.flush()
        (event,) = env.store.events(kind="settings.changed")
        self.assertIn("gemini_api_key", event.data["keys"])
        self.assertNotIn("NEWKEY-0123456789", json.dumps(event.data))
        self.assertEqual(event.data["changes"]["gst_model"], ["m-1", "m-9"])


class QualitySignalTests(unittest.TestCase):
    """17f: editing a cue, reverting, retranslating and failing validation each leave a quality signal."""

    def test_an_edited_cue_records_what_it_said_before_and_after(self):
        env = Env(self)
        env.run_job()
        unit = env.unit()
        result = env.service.add_correction(unit, {"kind": "cue_edit", "payload": {"cue": 2, "text": "Bạn khỏe không?"}})
        env.telemetry.flush()

        (signal,) = env.store.signals(kind="cue_edit")
        self.assertEqual((signal.unit_id, signal.cue, signal.version), (unit, 2, result["version"]))
        self.assertEqual(signal.before, "[vi] How are you?")
        self.assertEqual(signal.after, "Bạn khỏe không?")
        self.assertEqual((signal.meta["lang"], signal.meta["source"], signal.meta["model"]), ("vi", "How are you?", "m-1"))
        self.assertIn("version.cue_edit", env.kinds(unit_id=unit))

    def test_a_revert_records_which_version_was_restored_and_which_it_replaced(self):
        env = Env(self)
        env.run_job()
        unit = env.unit()
        env.service.add_correction(unit, {"kind": "cue_edit", "payload": {"cue": 1, "text": "Xin chào"}})
        result = env.service.revert(unit, 1)
        env.telemetry.flush()

        (signal,) = env.store.signals(kind="revert")
        self.assertEqual((signal.unit_id, signal.version, signal.meta["restored"], signal.meta["replaced"]),
                         (unit, result["version"], 1, 2))
        self.assertIn("version.reverted", env.kinds(unit_id=unit))

    def test_a_retranslation_request_records_the_range_and_the_version_it_questions(self):
        env = Env(self)
        env.run_job()
        unit = env.unit()
        result = env.service.retranslate(unit, {"range": {"cues": [2, 3]}})
        env.telemetry.flush()

        (signal,) = env.store.signals(kind="retranslate")
        self.assertEqual((signal.unit_id, signal.version, signal.cue), (unit, 1, 2))
        self.assertEqual((signal.meta["range"], signal.meta["whole_episode"], signal.meta["job_id"]),
                         ({"first": 2, "last": 3}, False, result["job_id"]))
        self.assertIn("job.created", env.kinds(job_id=result["job_id"]))

    def test_every_problem_the_validator_found_is_a_signal_even_when_repaired(self):
        env = Env(self, handlers={"m-1": lambda req, n: drop_cue(3)(req, n) if n == 1 else echo_items(req, "[vi] ")})
        env.run_job()
        (signal,) = env.store.signals(kind="validate")
        self.assertEqual((signal.cue, signal.meta["issue"], signal.meta["call"], signal.meta["error"]), (3, "missing", "translate", True))
        self.assertEqual(signal.before, "Fine, episode 1.")  # the source cue the model left out
        self.assertEqual(signal.unit_id, env.unit())

    def test_a_failed_translation_still_records_what_the_validator_saw(self):
        env = Env(self, handlers={"m-1": drop_cue(2), "m-2": drop_cue(2)})
        env.run_job()
        signals = env.store.signals(kind="validate", limit=100)
        self.assertTrue(signals)
        self.assertEqual({s.cue for s in signals}, {2})
        self.assertEqual({s.meta["model"] for s in signals}, {"m-1", "m-2"})
        self.assertIn("job.failed", env.kinds())


class NeverBlocksTests(unittest.TestCase):
    """17g and NFR-11: a failing or slow telemetry never fails or holds a job."""

    def test_a_database_that_refuses_every_write_does_not_fail_the_job(self):
        env = Env(self)
        with patch.object(Store, "add_event", side_effect=OSError("disk full")), \
                patch.object(Store, "add_signal", side_effect=OSError("disk full")):
            job_id = env.run_job()
        self.assertTrue(env.output().exists())
        self.assertEqual(env.store.llm_calls(job_id)[0].outcome, "ok")
        self.assertGreater(env.telemetry.dropped, 0)

    def test_a_full_disk_for_the_bodies_keeps_the_call_record_and_the_job(self):
        env = Env(self)
        with patch.object(BodyLog, "write", side_effect=OSError("no space left on device")):
            job_id = env.run_job()
        self.assertEqual(len(env.store.llm_calls(job_id)), 1)
        self.assertIsNone(env.store.body_ref(env.store.llm_calls(job_id)[0].id))
        self.assertTrue(env.output().exists())
        self.assertGreaterEqual(env.telemetry.dropped, 1)

    def test_a_call_record_that_cannot_be_written_does_not_fail_the_call(self):
        env = Env(self)
        with patch.object(Store, "record_llm_call", side_effect=OSError("database is locked")):
            env.run_job()
        self.assertTrue(env.output().exists())
        self.assertEqual(env.store.llm_calls(), [])
        self.assertGreaterEqual(env.telemetry.dropped, 1)

    def test_a_writer_that_stalls_makes_the_sink_drop_instead_of_wait(self):
        release = threading.Event()
        started = threading.Event()

        class Stuck(Store):
            def add_event(self, *args, **kwargs):
                started.set()
                release.wait(10)
                return super().add_event(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            store = Stuck(Path(tmp) / "app.db")
            telemetry = Telemetry(store, queue_size=3)
            try:
                telemetry.event("first", reason="r")
                self.assertTrue(started.wait(5))
                for number in range(50):  # the writer is stuck on the first; three fit in the queue
                    telemetry.event(f"more.{number}", reason="r")
                self.assertGreaterEqual(telemetry.dropped, 40)
                self.assertFalse(telemetry.flush(0.05))  # nothing is blocked by it, but it is not drained either
            finally:
                release.set()
                telemetry.close()
                store.close()

    def test_the_synchronous_sink_swallows_failures_too(self):
        class Broken(Store):
            def record_llm_call(self, *args, **kwargs):
                raise OSError("locked")

        with tempfile.TemporaryDirectory() as tmp:
            store = Broken(Path(tmp) / "app.db")
            try:
                telemetry = Telemetry(usage=store, synchronous=True)
                telemetry.llm_call("j", call_log())  # does not raise
                self.assertEqual(telemetry.dropped, 1)
            finally:
                store.close()

    def test_the_null_telemetry_accepts_everything(self):
        NULL.event("x", reason="y", anything=1)
        NULL.signal("cue_edit", unit_id=1)
        NULL.llm_call("j", call_log())
        self.assertIsInstance(Telemetry(), TelemetryPort)


class StoreRecordTests(unittest.TestCase):
    """The SQLite side of the ports: events, signals and the body index."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "app.db")
        self.addCleanup(self.store.close)

    def test_events_come_back_newest_first_as_named_values_and_filter(self):
        a = self.store.add_event("job.claimed", job_id="j1", unit_id=1, reason="first", data={"model": "m"}, at=10.0)
        b = self.store.add_event("job.done", level="WARNING", job_id="j2", reason="second", at=20.0)
        c = self.store.add_event("version.published", unit_id=1, at=30.0)
        self.assertEqual([e.id for e in self.store.events()], [c, b, a])
        first = self.store.events(job_id="j1")[0]
        self.assertEqual((first.kind, first.unit_id, first.reason, first.data, first.at), ("job.claimed", 1, "first", {"model": "m"}, 10.0))
        self.assertEqual([e.id for e in self.store.events(unit_id=1)], [c, a])
        self.assertEqual([e.id for e in self.store.events(kind="job.")], [b, a])
        self.assertEqual([e.id for e in self.store.events(kind="job.done")], [b])
        self.assertEqual([e.id for e in self.store.events(since=15.0)], [c, b])
        self.assertEqual([e.id for e in self.store.events(before_id=c, limit=1)], [b])
        with self.assertRaises(AttributeError):
            first.kind = "other"

    def test_a_kind_filter_is_not_a_pattern(self):
        self.store.add_event("job_x")
        self.store.add_event("job.y")
        self.assertEqual([e.kind for e in self.store.events(kind="job_")], [])
        self.assertEqual([e.kind for e in self.store.events(kind="job.")], ["job.y"])

    def test_prune_events_deletes_only_the_old_ones(self):
        self.store.add_event("old", at=10.0)
        self.store.add_event("new", at=100.0)
        self.assertEqual(self.store.prune_events(50.0), 1)
        self.assertEqual([e.kind for e in self.store.events()], ["new"])

    def test_signals_round_trip_and_filter(self):
        a = self.store.add_signal("cue_edit", unit_id=3, version=2, cue=5, before="b", after="a", meta={"lang": "vi"}, at=1.0)
        self.store.add_signal("revert", unit_id=4, at=2.0)
        (signal,) = self.store.signals(kind="cue_edit")
        self.assertEqual((signal.id, signal.unit_id, signal.version, signal.cue, signal.before, signal.after, signal.meta),
                         (a, 3, 2, 5, "b", "a", {"lang": "vi"}))
        self.assertEqual(len(self.store.signals()), 2)
        self.assertEqual([s.kind for s in self.store.signals(unit_id=4)], ["revert"])

    def test_the_body_index_survives_rotation(self):
        call = self.store.record_llm_call("j", provider="p", model="m", task="translate", tokens_in=1, tokens_out=1,
                                          latency_ms=1, outcome="ok")
        self.assertIsNone(self.store.body_ref(call))
        self.store.add_body_ref(call, "2026-11-15.001", 120, 300)
        self.assertEqual(self.store.body_ref(call), BodyRef(call, "2026-11-15.001", 120, 300, False))
        self.assertEqual(self.store.mark_bodies_rotated(["2026-11-15.001", "other"]), 1)
        self.assertTrue(self.store.body_ref(call).rotated)
        self.assertEqual(self.store.mark_bodies_rotated([]), 0)


if __name__ == "__main__":
    unittest.main()
