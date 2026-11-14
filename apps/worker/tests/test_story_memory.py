"""P4: story memory (SPEC FR-5, criteria 1 and 2). Every provider is a fake; nothing touches the network.

The fake model answers extraction with canned facts that carry unique markers, so a test can assert on
exactly which episodes' facts reached which translation prompt.
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from subcore.memory import EXTRACT_PROMPT_VERSION
from subcore.providers import FakeProvider, QuotaExhausted
from subcore.providers.fake import echo_items

from gst_worker import library_api
from gst_worker import queue as gst_queue
from gst_worker.bazarr import list_series_episodes
from gst_worker.glossaries import GlossaryFiles
from gst_worker.queue import FileJobQueue
from gst_worker.library import Library
from gst_worker.memory import StoryMemory, parse_anchor, unit_label
from gst_worker.ports import EpisodeListing, Series, Unit
from gst_worker.store import Store
from gst_worker.translation import ContextGapError, DailyQuotaExceededError
from gst_worker.translation_attempt import TranslationAttempt
from gst_worker.units import UnitService
from gst_worker.versions import TranslationVersions

CUES = 12
SETTINGS = {"gst_model": "m-1", "memory_enabled": True, "memory_gap_wait_seconds": 1800}


def source_text(episode):
    blocks = []
    for n in range(1, CUES + 1):
        start, end = (n - 1) * 2000, (n - 1) * 2000 + 1500
        stamp = lambda ms: f"00:00:{ms // 1000:02d},{ms % 1000:03d}"  # noqa: E731
        blocks.append(f"{n}\n{stamp(start)} --> {stamp(end)}\nLine {n} of episode {episode}\n")
    return "\n".join(blocks)


def facts_for(episode, filler=""):
    events = []
    if episode == 1:
        events.append({"a": "Alice", "b": "Bob", "relation": "romantic", "intimacy": 5, "hierarchy": "equal",
                       "cue": 2, "evidence": "EP1-LOVERS-MARK", "confidence": 0.9})
    if episode == 6:
        events.append({"a": "Alice", "b": "Bob", "relation": "enemy", "intimacy": 0, "hierarchy": "equal",
                       "cue": 4, "evidence": "EP6-ENEMIES-MARK", "confidence": 0.9})
    return {
        "characters": [{"name": "Alice", "gender": "f"}, {"name": "Bob", "gender": "m"}],
        "relationship_events": events,
        "terms": [{"source": "Kyber crystal", "kind": "object"}],
        "summary": f"EP{episode}-SUMMARY-MARK {filler}".strip(),
    }


class Env:
    def __init__(self, test, *, filler=""):
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.media = self.root / "media" / "Show" / "Season 1"
        self.media.mkdir(parents=True)
        self.filler = filler
        self.now = 1_000_000.0
        self.available = set()  # episodes whose source subtitle exists
        self.store = Store(self.data / "app.db")
        test.addCleanup(self.store.close)
        self.versions = TranslationVersions(self.store, self.data / "backups" / "subtitles")
        self.glossaries = GlossaryFiles(self.data)
        self.settings = dict(SETTINGS)
        self.memory = StoryMemory(
            self.store, self.store, settings_source=lambda: self.settings, media=self, clock=lambda: self.now
        )
        self.library = Library(self.store, self.glossaries, self.versions, memory=self.memory)
        self.fake = FakeProvider(handler=self.handler)
        self.attempt = TranslationAttempt(
            versions=self.versions, usage=self.store, library=self.library, memory=self.memory,
            provider_factory=lambda settings, model, **kw: self.fake,
        )
        self.service = UnitService(
            self.library, self.glossaries, self.versions, FileJobQueue(str(self.root / "queue")), memory=self.memory,
            settings_source=lambda: self.settings, provider_factory=lambda settings, model, **kw: self.fake,
        )
        self.episodes = range(1, 7)

    # -- the fake model ----------------------------------------------------------------
    def handler(self, req, call_no):
        if req.task == "extract":
            # The summary also depends on the subtitle text, so a changed source gives different facts.
            digest = hashlib.sha1(req.user.encode("utf-8")).hexdigest()[:6]
            return json.dumps(facts_for(req.meta["episode"], f"{self.filler} #{digest}"))
        if req.task == "summarize":
            return "ARC-SUMMARY-MARK " + ", ".join(req.meta["entries"])
        return echo_items(req, "[vi] ")

    def calls(self, task):
        return [c for c in self.fake.calls if c.task == task]

    def translate_prompts(self):
        return [c.system for c in self.calls("translate")]

    # -- the library as Bazarr lists it -------------------------------------------------------
    def source(self, episode):
        return self.media / f"Show.S01E{episode:02d}.en.srt"

    def add_source(self, episode):
        self.source(episode).write_text(source_text(episode), encoding="utf-8")
        self.available.add(episode)

    def remove_source(self, episode):
        self.source(episode).unlink(missing_ok=True)
        self.available.discard(episode)

    # the MediaLibrary port, as this fake Bazarr answers it
    def describe(self, media_type, series_id, media_id):
        return None

    def series_episodes(self, series_id, source_code):
        return [
            EpisodeListing(
                season=1, episode=n, title=f"Ep {n}", bazarr_id=str(100 + n),
                video_path=str(self.media / f"Show.S01E{n:02d}.mkv"),
                source_path=str(self.source(n)) if n in self.available else "",
            )
            for n in self.episodes
        ]

    # -- jobs ----------------------------------------------------------------------------
    def job(self, episode, **extra):
        return {
            "job_id": f"auto-{episode}", "provider": "gemini-console", "media_type": "series",
            "series_id": "7", "media_id": str(100 + episode),
            "video_path": str(self.media / f"Show.S01E{episode:02d}.mkv"), "subtitle_path": str(self.source(episode)),
            "output_path": str(self.media / f"Show.S01E{episode:02d}.vi.srt"),
            "source_code": "en", "target_code": "vi", "target_language": "Vietnamese", **extra,
        }

    def translate(self, episode, **extra):
        return self.attempt.run(self.job(episode, **extra), "", self.settings)

    def unit_id(self, episode):
        series = self.store.list_series()[0]
        return self.store.find_unit(series.id, 1, episode).id

    def status(self, episode):
        series = self.store.list_series()[0]
        rows = self.library.series_units(series.id)["units"]
        row = next(u for u in rows if u["episode"] == episode)
        return row["status"]


def all_sources(test, **kw):
    env = Env(test, **kw)
    for n in range(1, 7):
        env.add_source(n)
    return env


class Criterion1OutOfOrderTests(unittest.TestCase):
    """SPEC section 6, criterion 1: episode 6 first, then episode 1."""

    def test_episode_one_never_sees_later_facts_and_episode_six_sees_one_to_five(self):
        env = all_sources(self)
        env.translate(6)
        env.translate(1)
        prompt_six, prompt_one = env.translate_prompts()

        for n in range(1, 6):
            self.assertIn(f"EP{n}-SUMMARY-MARK", prompt_six, f"episode 6 should know episode {n}")
        self.assertIn("EP6-SUMMARY-MARK", prompt_six)  # its own facts
        self.assertIn("EP6-ENEMIES-MARK", prompt_six)

        self.assertIn("EP1-SUMMARY-MARK", prompt_one)  # its own facts
        for n in range(2, 7):
            self.assertNotIn(f"EP{n}-SUMMARY-MARK", prompt_one, f"episode 1 must not see episode {n}")
        self.assertNotIn("EP6-ENEMIES-MARK", prompt_one)

    def test_prerequisites_are_extracted_before_translating_and_only_once(self):
        env = all_sources(self)
        env.translate(6)
        order = [c.task for c in env.fake.calls]
        self.assertEqual(order, ["extract"] * 6 + ["translate"])  # 1..5 as prerequisites, then its own
        self.assertEqual([c.meta["episode"] for c in env.calls("extract")], [1, 2, 3, 4, 5, 6])

        env.translate(1)  # every fact it needs is stored: only the translation call is new
        self.assertEqual(len(env.calls("extract")), 6)
        self.assertEqual(len(env.calls("translate")), 2)

    def test_the_story_state_at_episode_six_is_the_state_after_five(self):
        env = all_sources(self)
        env.translate(6)
        prompt = env.translate_prompts()[0]
        self.assertIn("romantic", prompt)  # where Alice and Bob stood after episode 1..5
        self.assertIn("(as of S01E01)", prompt)
        self.assertIn("from item 4: Alice & Bob: enemy", prompt)  # the change inside episode 6, by item number

    def test_extraction_prompt_carries_neither_timecodes_nor_translations(self):
        env = all_sources(self)
        env.translate(2)
        extraction = env.calls("extract")[0]
        self.assertNotIn("-->", extraction.user)
        self.assertNotIn("00:00:", extraction.user)
        self.assertIn("Line 3 of episode 1", extraction.user)

    def test_memory_sections_come_after_the_glossary_and_before_metadata(self):
        env = all_sources(self)
        env.translate(2)
        prompt = env.translate_prompts()[0]
        story, relationship, episode = (prompt.index(t) for t in ("Story so far", "Relationship state", "This episode"))
        self.assertLess(prompt.index("Core rules"), story)
        self.assertLess(story, relationship)
        self.assertLess(relationship, episode)
        self.assertLess(episode, prompt.index("Metadata"))


class Criterion2GapTests(unittest.TestCase):
    """SPEC section 6, criterion 2: episode 3 has no source."""

    def setUp(self):
        self.env = Env(self)
        for n in (1, 2, 4, 5, 6):
            self.env.add_source(n)

    def test_the_job_waits_for_the_missing_episode_then_runs_incomplete(self):
        env = self.env
        env.translate(1)
        env.translate(2)
        with self.assertRaises(ContextGapError) as waiting:
            env.translate(4)
        self.assertEqual(waiting.exception.missing, ["S01E03"])
        self.assertEqual(waiting.exception.since, env.now)
        self.assertEqual(waiting.exception.retry_at, env.now + 300)  # look again soon, not only after 30 minutes
        self.assertFalse(Path(env.job(4)["output_path"]).exists())

        env.now += 1801
        self.assertEqual(env.translate(4, gap_since=waiting.exception.since), "translated")
        current = env.store.current_version(env.job(4)["output_path"])
        self.assertFalse(current.context_complete)
        self.assertEqual(current.missing_units, ("S01E03",))
        self.assertEqual(env.status(4), "incomplete")
        prompt = env.translate_prompts()[-1]
        self.assertIn("EP1-SUMMARY-MARK", prompt)
        self.assertIn("EP2-SUMMARY-MARK", prompt)
        self.assertNotIn("EP3-SUMMARY-MARK", prompt)

    def test_adding_the_missing_episode_makes_the_later_translations_stale(self):
        env = self.env
        env.translate(1)
        env.translate(2)
        for n in (4, 5, 6):
            env.translate(n, gap_since=env.now - 5000)  # the wait is over: translate without episode 3
        self.assertEqual([env.status(n) for n in (1, 2, 4, 5, 6)], ["current", "current"] + ["incomplete"] * 3)

        env.add_source(3)
        env.translate(3)  # its facts now exist

        self.assertEqual([env.status(n) for n in (1, 2, 3)], ["current"] * 3)  # earlier units are not affected
        self.assertEqual([env.status(n) for n in (4, 5, 6)], ["stale"] * 3)

    def test_a_source_that_appears_flags_the_incomplete_translation_before_any_extraction(self):
        env = self.env
        env.translate(1)
        env.translate(2)
        env.translate(4, gap_since=env.now - 5000)
        self.assertEqual(env.status(4), "incomplete")
        # Episode 3's subtitle arrives; the worker has registered it but not yet read it.
        env.add_source(3)
        env.store.upsert_unit(
            series_id=env.store.list_series()[0].id, season=1, episode=3, source_path=str(env.source(3))
        )
        self.assertEqual(env.calls("extract")[-1].meta["episode"], 4)  # still no facts for episode 3
        self.assertEqual(env.status(4), "stale")

    def test_retranslation_does_not_wait_for_a_gap(self):
        env = self.env
        env.translate(1)
        env.translate(2)
        env.translate(4, gap_since=env.now - 5000)
        # A manual retranslation runs at once with whatever exists.
        job = env.job(4, trigger="retranslate", retranslate={})
        self.assertEqual(env.attempt.run(job, "", env.settings), "translated")

    def test_gap_wait_zero_never_waits(self):
        env = self.env
        env.settings["memory_gap_wait_seconds"] = 0
        env.translate(1)
        self.assertEqual(env.translate(4), "translated")
        self.assertEqual(env.status(4), "incomplete")

    def test_a_deleted_source_keeps_its_stored_facts(self):
        env = Env(self)
        for n in range(1, 7):
            env.add_source(n)
        env.translate(3)  # extracts 1, 2 and 3
        env.remove_source(2)
        env.translate(4)  # episode 2 is gone from disk but its facts are still stored: nothing to wait for
        current = env.store.current_version(env.job(4)["output_path"])
        self.assertTrue(current.context_complete)
        self.assertIn("EP2-SUMMARY-MARK", env.translate_prompts()[-1])


class MemorySwitchTests(unittest.TestCase):
    def test_memory_is_off_by_default_and_changes_nothing(self):
        env = all_sources(self)
        env.settings["memory_enabled"] = False
        env.translate(3)
        self.assertEqual([c.task for c in env.fake.calls], ["translate"])
        self.assertNotIn("Story so far", env.translate_prompts()[0])
        row = env.store.current_version(env.job(3)["output_path"])
        self.assertEqual((row.memory_hash, row.context_complete), ("", True))
        self.assertEqual(env.status(3), "current")

    def test_a_series_can_turn_memory_on_or_off_against_the_global_setting(self):
        env = all_sources(self)
        env.settings["memory_enabled"] = False
        env.translate(1)
        series_id = env.store.list_series()[0].id

        env.service.set_series_memory(series_id, {"policy": "on"})
        env.translate(2)
        self.assertTrue(env.calls("extract"))

        env.settings["memory_enabled"] = True
        env.service.set_series_memory(series_id, {"policy": "off"})
        before = len(env.calls("extract"))
        env.translate(3)
        self.assertEqual(len(env.calls("extract")), before)

    def test_a_translation_made_without_memory_is_not_stale_when_memory_is_switched_on(self):
        env = all_sources(self)
        env.settings["memory_enabled"] = False
        env.translate(2)
        env.settings["memory_enabled"] = True
        self.assertEqual(env.status(2), "current")  # only flagged when it used memory and the facts moved

    def test_movies_have_no_story_memory(self):
        env = Env(self)
        env.add_source(1)
        job = env.job(1, media_type="movie", series_id="")
        job["video_path"] = str(env.media / "Heat (1995).mkv")
        self.assertEqual(env.attempt.run(job, "", env.settings), "translated")
        self.assertEqual(env.calls("extract"), [])


class FactsAreCachedTests(unittest.TestCase):
    def test_changed_source_is_extracted_again_and_marks_later_episodes_stale(self):
        env = all_sources(self)
        env.translate(1)
        env.translate(2)
        env.translate(3)
        self.assertEqual(env.status(3), "current")

        env.source(1).write_text(source_text(1).replace("Line 5", "A different line 5"), encoding="utf-8")
        env.translate(1, trigger="retranslate", retranslate={})  # re-reads the source, so its facts are new
        extractions = [c.meta["episode"] for c in env.calls("extract")]
        self.assertEqual(extractions.count(1), 2)  # the old facts are kept, the new ones were added
        self.assertEqual(env.status(2), "stale")
        self.assertEqual(env.status(3), "stale")

    def test_a_bad_extraction_answer_leaves_a_gap_instead_of_failing_the_job(self):
        env = all_sources(self)
        original = env.handler

        def handler(req, call_no):
            if req.task == "extract" and req.meta["episode"] == 1:
                return "this is not json"
            return original(req, call_no)

        env.fake.handler = handler
        env.settings["memory_gap_wait_seconds"] = 0
        self.assertEqual(env.translate(2), "translated")
        current = env.store.current_version(env.job(2)["output_path"])
        self.assertEqual(current.missing_units, ("S01E01",))
        self.assertFalse(current.context_complete)

    def test_quota_exhaustion_during_extraction_pauses_like_a_translation_would(self):
        env = all_sources(self)

        def handler(req, call_no):
            raise QuotaExhausted("daily quota")

        env.fake.handler = handler
        with self.assertRaises(DailyQuotaExceededError):
            env.translate(3)
        self.assertFalse(Path(env.job(3)["output_path"]).exists())


class CompressionTests(unittest.TestCase):
    def test_old_summaries_are_condensed_once_and_cached(self):
        env = Env(self, filler="word " * 400)  # each summary is long
        env.episodes = range(1, 9)
        for n in range(1, 9):
            env.add_source(n)
        env.settings.update(memory_budget_tokens=500, memory_recent_episodes=1)
        env.translate(8)
        self.assertEqual(len(env.calls("summarize")), 1)
        prompt = env.translate_prompts()[0]
        self.assertIn("ARC-SUMMARY-MARK", prompt)
        self.assertIn("EP7-SUMMARY-MARK", prompt)  # the newest episode before this one stays in full
        self.assertNotIn("EP3-SUMMARY-MARK", prompt)  # older ones are only in the arc

        # Same facts again: the condensed text comes from the cache, no second call.
        env.translate(8, trigger="retranslate", retranslate={})
        self.assertEqual(len(env.calls("summarize")), 1)

    def test_a_failed_compression_falls_back_to_a_first_sentence_digest(self):
        env = Env(self, filler="word " * 400)
        for n in range(1, 7):
            env.add_source(n)
        original = env.handler

        def handler(req, call_no):
            return "" if req.task == "summarize" else original(req, call_no)

        env.fake.handler = handler
        env.settings.update(memory_budget_tokens=500, memory_recent_episodes=1)
        env.translate(6)
        self.assertIn("first sentence of each", env.translate_prompts()[0])


class ContextEndpointTests(unittest.TestCase):
    def test_context_shows_what_a_translation_used_and_what_it_would_get_now(self):
        env = all_sources(self)
        env.translate(2)
        context = env.service.context(env.unit_id(2))

        self.assertTrue(context["applicable"] and context["enabled"])
        used = context["used"]
        self.assertEqual((used["version"], used["complete"], used["missing"], used["changed"]), (1, True, [], False))
        titles = [s["title"] for s in used["sections"]]
        self.assertEqual(titles, ["Story so far", "Relationship state", "This episode"])
        self.assertIn("EP1-SUMMARY-MARK", used["sections"][0]["body"])
        self.assertEqual([p["label"] for p in context["now"]["prior"]], ["S01E01"])

        # Episode 1's subtitle is replaced and its facts re-read: what episode 2 was built on has moved.
        env.source(1).write_text(source_text(1).replace("Line 5", "Another line 5"), encoding="utf-8")
        env.service.refresh_context(env.unit_id(2))
        self.assertTrue(env.service.context(env.unit_id(2))["used"]["changed"])
        self.assertEqual(env.status(2), "stale")

    def test_series_memory_settings_are_validated(self):
        env = all_sources(self)
        env.translate(1)
        series_id = env.store.list_series()[0].id
        result = env.service.set_series_memory(series_id, {"policy": "on", "specials_after": "s02e05"})
        self.assertEqual((result["policy"], result["specials_after"]), ("on", "S02E05"))
        with self.assertRaises(ValueError):
            env.service.set_series_memory(series_id, {"policy": "maybe"})
        with self.assertRaises(ValueError):
            env.service.set_series_memory(series_id, {"specials_after": "next week"})
        self.assertEqual(env.service.series_memory(series_id)["units"][0]["label"], "S01E01")

    def test_refresh_extracts_facts_for_the_episode_and_those_before_it(self):
        env = all_sources(self)
        env.translate(2)  # extracts episodes 1 and 2
        before = len(env.calls("extract"))
        result = env.service.refresh_context(env.unit_id(2))
        self.assertEqual((result["ok"], result["extracted"]), (True, []))  # everything is already stored
        self.assertEqual(len(env.calls("extract")), before)

        env.source(1).write_text(source_text(1).replace("Line 5", "Another line 5"), encoding="utf-8")
        result = env.service.refresh_context(env.unit_id(2))
        self.assertEqual(result["extracted"], ["S01E01"])  # only what changed

    def test_movies_report_that_memory_does_not_apply(self):
        env = Env(self)
        env.add_source(1)
        job = env.job(1, media_type="movie", series_id="")
        job["video_path"] = str(env.media / "Heat (1995).mkv")
        env.attempt.run(job, "", env.settings)
        movie = env.store.list_units(movies=True)[0]
        self.assertEqual(env.service.context(movie.id), {"applicable": False, "enabled": False})


class RoutesTests(unittest.TestCase):
    def test_memory_routes(self):
        env = all_sources(self)
        env.translate(2)
        unit, series = env.unit_id(2), env.store.list_series()[0].id
        handle = lambda method, path, body=None: library_api.handle(env.service, method, path, {}, body)  # noqa: E731

        status, payload = handle("GET", f"/api/units/{unit}/context")
        self.assertEqual((status, payload["applicable"], payload["label"]), (200, True, "S01E02"))
        status, payload = handle("GET", f"/api/series/{series}/memory")
        self.assertEqual((status, payload["policy"], payload["global_enabled"]), (200, "", True))
        self.assertEqual([u["label"] for u in payload["units"]][:2], ["S01E01", "S01E02"])
        status, payload = handle("PUT", f"/api/series/{series}/memory", {"policy": "off"})
        self.assertEqual((status, payload["policy"]), (200, "off"))
        self.assertEqual(handle("PUT", f"/api/series/{series}/memory", {"policy": "bogus"})[0], 400)
        self.assertEqual(handle("POST", f"/api/units/{unit}/context/extract")[0], 200)
        self.assertEqual(handle("GET", "/api/units/9999/context")[0], 404)
        self.assertEqual(handle("GET", "/api/series/9999/memory")[0], 404)

    def test_the_provider_failing_during_a_manual_read_is_a_clear_error(self):
        env = all_sources(self)
        env.translate(2)
        env.source(1).write_text(source_text(1).replace("Line 5", "Other"), encoding="utf-8")

        def refuse(req, n):
            raise QuotaExhausted("daily quota")

        env.fake.handler = refuse
        with self.assertRaisesRegex(ValueError, "QuotaExhausted"):
            env.service.refresh_context(env.unit_id(2))


class StoryOrderTests(unittest.TestCase):
    def units(self):
        return [Unit(id=i, season=s, episode=e, series_id=1) for i, (s, e) in enumerate(
            [(0, 1), (1, 1), (1, 2), (2, 1), (2, 2)], start=1)]

    def prior(self, season, episode, anchor=""):
        store = Store(":memory:")
        memory = StoryMemory(store, store)
        self.addCleanup(store.close)
        units = self.units()
        unit = next(u for u in units if (u.season, u.episode) == (season, episode))
        return [unit_label(u) for u in memory.prior_units(unit, units, Series(id=1, key="s", specials_after=anchor))]

    def test_story_order_is_season_then_episode_and_never_includes_later_units(self):
        self.assertEqual(self.prior(1, 1), [])
        self.assertEqual(self.prior(2, 1), ["S01E01", "S01E02"])
        self.assertEqual(self.prior(2, 2), ["S01E01", "S01E02", "S02E01"])

    def test_specials_sit_outside_the_chain(self):
        # By default a special sees the state after the first season; its own facts never feed the chain.
        self.assertEqual(self.prior(0, 1), ["S01E01", "S01E02"])
        self.assertNotIn("S00E01", self.prior(2, 2))

    def test_a_configured_anchor_places_the_special(self):
        self.assertEqual(self.prior(0, 1, "S02E01"), ["S01E01", "S01E02", "S02E01"])
        self.assertEqual(self.prior(0, 1, "S01E01"), ["S01E01"])

    def test_anchor_parsing(self):
        self.assertEqual(parse_anchor(" s02e05 "), (2, 5))
        self.assertIsNone(parse_anchor("2x05"))


class CatalogTests(unittest.TestCase):
    def test_bazarr_episode_listing_gives_the_source_subtitle_of_each_episode(self):
        class Http:
            def get_json(self, url, params=None, headers=None):
                self.url, self.params, self.headers = url, params, headers
                return {"data": [
                    {"season": 1, "episode": 1, "title": "Pilot", "sonarrEpisodeId": 11, "path": "/m/S01E01.mkv",
                     "subtitles": [{"code2": "en", "path": "/m/S01E01.en.srt"}, {"code2": "vi", "path": "/m/S01E01.vi.srt"}]},
                    {"season": 1, "episode": 2, "title": "Two", "sonarrEpisodeId": 12, "path": "/m/S01E02.mkv", "subtitles": []},
                    {"season": None, "episode": 3},
                ]}

        http = Http()
        episodes = list_series_episodes(http, "http://bazarr", "key", "7", "en")
        self.assertEqual(http.params, {"seriesid[]": "7"})
        self.assertEqual([(e.season, e.episode, e.source_path) for e in episodes],
                         [(1, 1, "/m/S01E01.en.srt"), (1, 2, "")])
        self.assertEqual(episodes[0].bazarr_id, "11")

    def test_a_catalogue_failure_never_stops_a_translation(self):
        env = all_sources(self)

        class Down:
            def series_episodes(self, series_id, source_code):
                raise OSError("bazarr is down")

        env.memory.media = Down()
        self.assertEqual(env.translate(2), "translated")


class QueueDeferralTests(unittest.TestCase):
    def test_a_context_gap_defers_the_job_and_keeps_when_the_wait_began(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Show.S01E04.en.srt"
            source.write_text("x", encoding="utf-8")
            os.utime(source, (100, 100))  # long settled, so the queue may start it at the fake clock
            queue_dir = str(root / "queue")
            jobs = gst_queue.JobQueue(queue_dir)
            gst_queue.enqueue_translation_jobs(
                queue_dir, {"subtitle_path": str(source), "source_code": "en"},
                [{"code": "vi", "language": "Vietnamese", "enabled": True}],
            )

            def waiting(job, update_status):
                raise ContextGapError("Waiting for S01E03", retry_at=1300.0, since=1000.0, missing=["S01E03"])

            self.assertTrue(jobs.process_once(waiting, now=1000))
            snapshot = gst_queue.queue_snapshot(queue_dir)
            self.assertEqual(snapshot["counts"]["deferred"], 1)
            deferred = snapshot["deferred"][0]
            self.assertEqual((deferred["deferred_reason"], deferred["gap_since"], deferred["retry_at"]),
                             ("context-gap", 1000.0, 1300.0))
            self.assertIn("S01E03", deferred["error"])
            self.assertFalse(jobs.process_once(waiting, now=1299))  # not before its time

            seen = {}

            def finish(job, update_status):
                seen["gap_since"] = job.get("gap_since")  # the attempt reads it to know the wait is over
                return "translated"

            self.assertTrue(jobs.process_once(finish, now=1300))
            self.assertEqual(seen["gap_since"], 1000.0)
            done = gst_queue.queue_snapshot(queue_dir)["done"][0]
            self.assertNotIn("gap_since", done)  # cleared once the job finishes


class ExtractionVersionTests(unittest.TestCase):
    def test_facts_are_stored_per_source_hash_and_prompt_version(self):
        env = all_sources(self)
        env.translate(2)
        uid = env.unit_id(1)
        row = env.store.latest_facts([uid], EXTRACT_PROMPT_VERSION)[uid]
        self.assertEqual(row.prompt_version, EXTRACT_PROMPT_VERSION)
        self.assertEqual(len(row.source_sha256), 64)
        self.assertEqual(env.store.latest_facts([uid], "older-version"), {})


if __name__ == "__main__":
    unittest.main()
