"""The library as Bazarr lists it, the labels read from it, requests, Ignored and the ``auto_request`` policy
(SPEC criterion 13 a-h, FR-14, FR-15.4-15.6; P10). A fake Bazarr and a temporary media folder; no network, no model."""

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from subserver import queue_policy as policy
from subserver.auto_request import AutoRequest, sync_and_request
from subserver.catalog import CatalogPaths, CatalogSync, with_keys
from subserver.catalog_actions import CatalogActions
from subserver.catalog_api import handle
from subserver.catalog_view import CatalogView
from subserver.glossaries import GlossaryFiles
from subserver.job_requests import JobRequests
from subserver.library import Library, identify_job
from subserver.ports import CatalogEntry, CatalogSubtitle, MediaLibraryError
from subserver.queue import JobQueue
from subserver.store import Store
from subserver.versions import TranslationVersions
from tests.queue_harness import Clock

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n"
EN = {"code": "en", "language": "English", "enabled": True}
VI = {"code": "vi", "language": "Vietnamese", "enabled": True}


class FakeMedia:
    """The MediaLibrary port: whatever ``entries`` holds, or the failure ``error`` says."""

    def __init__(self):
        self.entries: list[CatalogEntry] = []
        self.error: Exception | None = None

    def catalog(self):
        if self.error is not None:
            raise self.error
        yield from list(self.entries)


class Env:
    """Everything the Library page needs, on a real in-memory store and a temporary media folder."""

    def __init__(self, case, **settings):
        directory = tempfile.TemporaryDirectory()
        case.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.media_dir = self.root / "media"
        self.media_dir.mkdir()
        self.clock = Clock(now=1_800_000_000.0)
        self.store = Store(":memory:")
        case.addCleanup(self.store.close)
        self.settings = {"auto_request": False, "auto_request_daily_cap": 0, "catalog_shrink_guard": 20,
                         "catalog_sync_interval": 300, "bazarr_api_key": "key", "max_calls_per_day": 0, **settings}
        self.config = {"source_languages": [EN], "target_languages": [VI]}
        self.versions = TranslationVersions(self.store, self.root / "backups", clock=self.clock.time)
        self.library = Library(self.store, GlossaryFiles(str(self.root / "data")), self.versions)
        self.queue = JobQueue(self.store, clock=self.clock.time, monotonic=self.clock.monotonic)
        self.queue.configure({}, ("m",), "gemini")
        self.fake = FakeMedia()
        self.sync = CatalogSync(self.store, self.fake, lambda: self.settings, clock=self.clock.time)
        self.requests = JobRequests(
            self.store, self.library, self.queue, self.versions, config_source=lambda: self.config,
            settings_source=lambda: self.settings, clock=self.clock.time,
        )
        self.auto = AutoRequest(self.store, self.requests, lambda: self.settings, lambda: self.config, clock=self.clock.time)
        self.view = CatalogView(
            self.store, self.library, self.queue, self.versions, config_source=lambda: self.config,
            settings_source=lambda: self.settings,
        )
        self.actions = CatalogActions(self.store, self.library, self.sync, self.auto, self.requests, self.view)
        self.series: dict[str, CatalogEntry] = {}
        self.add_series("7", "Show", "70")

    # -- the fake library -----------------------------------------------------------------------------------------------
    def add_series(self, series_id, title, tvdb):
        entry = CatalogEntry(kind="series", bazarr_id=series_id, title=title, tvdb_id=tvdb)
        self.series[series_id] = entry
        self.fake.entries.append(entry)

    def episode(self, n, *, season=1, series="7", missing=True, source=True, vi=None, bazarr_id=None, folder=None):
        """One episode: its source subtitle exists on disk; ``vi`` is a file Bazarr lists as present (not written)."""
        show = self.series[series]
        directory = self.media_dir / (folder or show.title)
        directory.mkdir(exist_ok=True)
        stem = f"{show.title}.S{season:02d}E{n:02d}"
        subs = []
        if source:
            (directory / f"{stem}.en.srt").write_text(SRT, encoding="utf-8")
            subs.append(CatalogSubtitle("en", str(directory / f"{stem}.en.srt")))
        if vi:
            subs.append(CatalogSubtitle("vi", str(directory / f"{stem}.vi.srt")))
        entry = CatalogEntry(
            kind="episode", bazarr_id=bazarr_id or f"{series}{season}{n:02d}", parent_bazarr_id=series, tvdb_id=show.tvdb_id,
            season=season, episode=n, title=f"Episode {n}", video_path=str(directory / f"{stem}.mkv"),
            subtitles=tuple(subs), missing=("vi",) if missing and not vi else (),
        )
        self.fake.entries.append(entry)
        return entry

    def replace_entry(self, old, **fields):
        self.fake.entries[self.fake.entries.index(old)] = replace(old, **fields)

    def item(self, bazarr_id, kind="episode"):
        return self.store.catalog_lookup(kind, bazarr_id)

    def ids(self, *bazarr_ids):
        return [self.item(b).id for b in bazarr_ids]

    def label(self, bazarr_id, lang="vi"):
        entry = self.item(bazarr_id)
        series = self.store.catalog_entry(entry.parent_id) if entry.parent_id else None
        page = self.view.item(entry, series, [lang], self.store.decisions(), [EN])
        return page["targets"][0]

    def paths(self, bazarr_id):
        entry = self.item(bazarr_id)
        source = next(s.path for s in entry.subtitles if s.code == "en")
        return source, str(Path(source).with_name(Path(source).name.replace(".en.", ".vi.")))

    def jobs_open(self):
        return self.queue.jobs.by_state("queued", "running", "waiting")

    def translated(self, bazarr_id, *, edited=False):
        """A target file the system made (and, with ``edited``, one the admin then edited by hand)."""
        entry = self.item(bazarr_id)
        series = self.store.catalog_entry(entry.parent_id)
        source, target = self.paths(bazarr_id)
        unit_id = self.library.register_entry(entry, series, source_path=source, source_code="en")
        self.store.set_output(unit_id, "vi", target)
        rules = self.library.unit_rules(self.store.get_unit(unit_id), "vi").hash
        self.versions.publish(target, SRT, trigger="auto", source_path=source, target_lang="vi", unit_id=unit_id,
                              glossary_hash=rules, model="m")
        if edited:
            self.versions.publish(target, SRT.replace("Hello", "Xin chao"), trigger="cue_edit", source_path=source,
                                  target_lang="vi", unit_id=unit_id, glossary_hash=rules)
        return unit_id, target


class CatalogCase(unittest.TestCase):
    def env(self, **settings):
        return Env(self, **settings)


class Criterion13a(CatalogCase):
    def test_13a_a_sync_builds_the_catalog_and_each_item_is_labelled_from_what_exists(self):
        env = self.env()
        env.episode(1)  # Bazarr lists vi as missing
        translated = env.episode(2, vi=True)  # Bazarr lists a vi subtitle, and the system made it
        env.episode(3, vi=True)  # a vi subtitle somebody else put there
        env.episode(4, vi=True)  # a vi subtitle the system made and the admin edited
        state = env.sync.run()
        self.assertEqual((state.status, state.items), ("ok", 5))  # a series and four episodes
        self.assertEqual(env.store.catalog_count(), 5)
        env.translated(translated.bazarr_id)
        env.translated("7104", edited=True)
        (Path(env.paths("7103")[1])).write_text(SRT, encoding="utf-8")  # the file of episode 3, made by nobody here

        self.assertEqual(env.label("7101")["text"], "Missing")
        self.assertEqual(env.label("7102")["text"], "Translated")
        self.assertEqual(env.label("7103")["text"], "External")
        self.assertEqual(env.label("7104")["text"], "Edited")
        self.assertEqual({env.label(b)["content"] for b in ("7101", "7102", "7103", "7104")},
                         {"missing", "translated", "external", "edited"})

    def test_13a_a_file_the_worker_just_wrote_is_Translated_before_bazarr_is_read_again(self):
        env = self.env()
        env.episode(1)  # the cache still says vi is missing
        env.sync.run()
        self.assertEqual(env.label("7101")["text"], "Missing")
        env.translated("7101")
        self.assertEqual(env.label("7101")["text"], "Translated")
        env.actions.request({"items": env.ids("7101")})  # and asking again is refused, not queued behind the stale cache
        self.assertEqual(env.jobs_open(), [])

    def test_13a_a_translation_the_glossary_has_outgrown_is_Translated_and_Stale(self):
        env = self.env()
        env.episode(1, vi=True)
        env.sync.run()
        unit_id, _ = env.translated("7101")
        self.assertEqual(env.label("7101")["text"], "Translated")
        env.store.add_correction(unit_id, "vi", "term", {"source": "Hi", "target": "Chao", "locked": True})
        self.assertEqual(env.label("7101")["text"], "Translated · Stale")

    def test_13a_the_catalog_keys_meet_the_keys_the_worker_gives_a_job(self):
        entry = with_keys([CatalogEntry(kind="series", bazarr_id="7", title="Show", tvdb_id="70")])[0]
        job = {"media_type": "series", "series_id": "7", "tvdb_id": "70", "season": 1, "episode": 1,
               "video_path": "/m/Show.S01E01.mkv", "subtitle_path": "/m/Show.S01E01.en.srt"}
        self.assertEqual(entry.series_key, identify_job(job).series_key)
        bare = with_keys([CatalogEntry(kind="series", bazarr_id="8", title="No Tvdb")])[0]
        self.assertEqual(bare.series_key, identify_job({**job, "tvdb_id": "", "series_id": "8"}).series_key)
        movie = with_keys([CatalogEntry(kind="movie", bazarr_id="5", title="Heat", video_path="/m/Heat {tmdb-949}/Heat.mkv")])[0]
        self.assertEqual(movie.series_key, "tmdb-949")


class Criterion13b(CatalogCase):
    def synced(self, count=9):
        env = self.env()
        for n in range(1, count + 1):
            env.episode(n)
        env.sync.run()
        self.assertEqual(env.store.catalog_count(), count + 1)
        return env

    def assertCacheKept(self, env, before):
        state = env.store.catalog_state()
        self.assertEqual((state.status, state.synced_at, state.items), ("kept", before.synced_at, before.items))
        self.assertTrue(state.warning)
        self.assertEqual(env.store.catalog_count(), before.items)
        self.assertEqual(env.label("7101")["text"], "Missing")  # nothing became "not missing" because Bazarr failed
        self.assertEqual(env.view.status()["synced_at"], before.synced_at)  # "updated at ..." with the warning beside it
        self.assertEqual(env.view.status()["warning"], state.warning)

    def test_13b_a_bazarr_that_fails_or_times_out_leaves_the_catalog_as_it_was_and_says_so(self):
        for error in (OSError("connection refused"), TimeoutError("timed out"), MediaLibraryError("Bazarr rejected the API key (HTTP 401).")):
            with self.subTest(error=error):
                env = self.synced()
                before = env.store.catalog_state()
                env.clock.advance(60)
                env.fake.error = error
                env.sync.run()
                self.assertCacheKept(env, before)

    def test_13b_an_empty_answer_is_not_an_empty_library(self):
        env = self.synced()
        before = env.store.catalog_state()
        env.clock.advance(60)
        env.fake.entries = []
        env.sync.run()
        self.assertCacheKept(env, before)
        self.assertIn("empty", env.store.catalog_state().warning)

    def test_13b_an_answer_that_shrank_more_than_the_guard_allows_is_set_aside(self):
        env = self.synced(9)  # 10 items
        before = env.store.catalog_state()
        env.clock.advance(60)
        env.fake.entries = env.fake.entries[:7]  # 7 of 10: 30% fewer, over the 20% guard
        env.sync.run()
        self.assertCacheKept(env, before)

    def test_13b_a_smaller_answer_within_the_guard_is_applied_and_the_missing_item_is_gone_not_deleted(self):
        env = self.synced(9)
        env.clock.advance(60)
        gone = env.fake.entries.pop()  # 9 of 10: within the guard
        state = env.sync.run()
        self.assertEqual((state.status, state.items, state.warning), ("ok", 9, ""))
        self.assertEqual(env.store.catalog_count(), 9)
        self.assertIsNotNone(env.item(gone.bazarr_id).gone_at)  # the row stays: a unit and its history may point at it
        env.clock.advance(60)
        env.fake.entries.append(gone)
        env.sync.run()
        self.assertIsNone(env.item(gone.bazarr_id).gone_at)

    def test_13b_the_guard_can_be_turned_off_and_a_first_read_has_nothing_to_guard(self):
        env = self.synced(9)
        env.settings["catalog_shrink_guard"] = 0
        env.clock.advance(60)
        env.fake.entries = env.fake.entries[:3]
        self.assertEqual(env.sync.run().status, "ok")
        empty = self.env()
        self.assertEqual(empty.sync.run().status, "ok")  # nothing to compare with, and an empty library is a library

    def test_13b_the_next_good_read_clears_the_warning(self):
        env = self.synced()
        env.clock.advance(60)
        env.fake.error = OSError("down")
        env.sync.run()
        env.clock.advance(60)
        env.fake.error = None
        state = env.sync.run()
        self.assertEqual((state.status, state.warning), ("ok", ""))

    def test_13b_the_periodic_read_is_due_by_the_interval_and_off_without_a_key(self):
        env = self.env()
        self.assertTrue(env.sync.due())
        env.sync.run()
        self.assertFalse(env.sync.due())
        env.clock.advance(301)
        self.assertTrue(env.sync.due())
        env.settings["catalog_sync_interval"] = 0
        self.assertFalse(env.sync.due())
        env.settings.update(catalog_sync_interval=300, bazarr_api_key="")
        self.assertFalse(env.sync.due())


class Criterion13c(CatalogCase):
    def test_13c_a_job_that_waits_finds_its_files_again_after_they_were_renamed(self):
        env = self.env()
        old = env.episode(1)
        env.sync.run()
        result = env.actions.request({"items": env.ids("7101")})
        (job_id,) = result["created"]
        before = env.queue.jobs.get(job_id)
        unit_id = before["unit_id"]

        # Sonarr renames the folder and the files; Bazarr lists the same ids with the new paths.
        new_dir = env.media_dir / "Show (2020)"
        new_dir.mkdir()
        os.replace(env.media_dir / "Show" / "Show.S01E01.en.srt", new_dir / "Show.2020.S01E01.en.srt")
        env.replace_entry(
            old, video_path=str(new_dir / "Show.2020.S01E01.mkv"),
            subtitles=(CatalogSubtitle("en", str(new_dir / "Show.2020.S01E01.en.srt")),),
        )
        env.clock.advance(60)
        env.sync.run()

        seen = {}

        def run(job, update_status):
            CatalogPaths(env.store).refresh(job)
            seen.update(subtitle=job["subtitle_path"], output=job["output_path"], video=job["video_path"])
            self.assertTrue(Path(job["subtitle_path"]).is_file())  # the file it would translate is really there
            return "translated"

        self.assertTrue(env.queue.process_once(run))
        self.assertEqual(seen["subtitle"], str(new_dir / "Show.2020.S01E01.en.srt"))
        self.assertEqual(seen["output"], str(new_dir / "Show.2020.S01E01.vi.srt"))
        self.assertEqual(seen["video"], str(new_dir / "Show.2020.S01E01.mkv"))
        unit = env.store.get_unit(unit_id)
        self.assertEqual((unit.source_path, unit.video_path), (seen["subtitle"], seen["video"]))
        self.assertEqual([o.output_path for o in env.store.outputs(unit_id)], [seen["output"]])

    def test_13c_asking_again_after_a_rename_is_still_the_same_request(self):
        env = self.env()
        old = env.episode(1)
        env.sync.run()
        env.actions.request({"items": env.ids("7101")})
        new_dir = env.media_dir / "Renamed"
        new_dir.mkdir()
        os.replace(env.media_dir / "Show" / "Show.S01E01.en.srt", new_dir / "New.S01E01.en.srt")
        env.replace_entry(old, subtitles=(CatalogSubtitle("en", str(new_dir / "New.S01E01.en.srt")),))
        env.clock.advance(60)
        env.sync.run()
        again = env.actions.request({"items": env.ids("7101")})
        self.assertEqual(again["count"], 0)
        self.assertEqual(len(env.jobs_open()), 1)  # found by the unit, though the paths, so the key, changed

    def test_13c_a_job_the_cache_does_not_know_keeps_its_own_paths(self):
        env = self.env()
        job = {"media_id": "999", "media_type": "series", "subtitle_path": "/a/x.en.srt", "output_path": "/a/x.vi.srt",
               "source_code": "en", "target_code": "vi"}
        self.assertFalse(CatalogPaths(env.store).refresh(job))
        self.assertEqual(job["subtitle_path"], "/a/x.en.srt")


class Criterion13d(CatalogCase):
    def test_13d_a_sync_makes_no_job_while_auto_request_is_off(self):
        env = self.env()
        for n in (1, 2, 3):
            env.episode(n)
        state, auto = sync_and_request(env.sync, env.auto)
        self.assertEqual((state.status, auto.created), ("ok", 0))
        self.assertEqual(env.queue.jobs.counts(), {"pending": 0, "processing": 0, "deferred": 0, "done": 0, "failed": 0})

    def test_13d_a_read_that_was_set_aside_does_not_wake_the_policy(self):
        env = self.env(auto_request=True)
        env.episode(1)
        env.fake.error = OSError("down")
        state, auto = sync_and_request(env.sync, env.auto)
        self.assertEqual((state.status, auto), ("kept", None))
        self.assertEqual(env.jobs_open(), [])


class Criterion13e(CatalogCase):
    def test_13e_a_request_makes_the_item_Missing_and_Requested_and_a_second_one_makes_no_second_job(self):
        env = self.env()
        env.episode(1)
        env.sync.run()
        self.assertEqual(env.label("7101")["text"], "Missing")
        first = env.actions.request({"items": env.ids("7101")})
        self.assertEqual(first["count"], 1)
        self.assertEqual(env.label("7101")["text"], "Missing · Requested")
        job = env.queue.jobs.get(first["created"][0])
        self.assertEqual((job["origin"], job["prio"], job["mode"]), ("manual", policy.INTERACTIVE, "fill"))
        second = env.actions.request({"items": env.ids("7101")})
        self.assertEqual((second["count"], second["skipped_by_reason"]), (0, {"already requested": 1}))
        self.assertEqual(len(env.jobs_open()), 1)

    def test_13e_the_label_follows_the_job_while_it_runs_waits_and_fails(self):
        env = self.env()
        env.episode(1)
        env.sync.run()
        (job_id,) = env.actions.request({"items": env.ids("7101")})["created"]
        env.queue.jobs.transition(job_id, "running")
        self.assertEqual(env.label("7101")["text"], "Missing · Running")
        env.queue.jobs.transition(job_id, "waiting", wait_reason="quota", not_before=9e12)
        self.assertEqual(env.label("7101")["text"], "Missing · Waiting · daily quota")
        env.queue.jobs.transition(job_id, "failed", error="boom")
        self.assertEqual(env.label("7101")["text"], "Missing · Failed")

    def test_13e_a_whole_series_or_season_is_one_request_and_an_estimate_comes_first(self):
        env = self.env()
        for n in (1, 2, 3):
            env.episode(n)
        env.episode(1, season=2)
        env.sync.run()
        (series_id,) = [e.id for e in env.store.catalog_entries("series")]
        estimate = env.actions.request({"series": series_id, "season": 1, "estimate_only": True})
        self.assertEqual((estimate["estimate_only"], estimate["planned_total"], estimate["estimate"]["jobs"]), (True, 3, 3))
        self.assertEqual((estimate["estimate"]["cues"], estimate["estimate"]["calls"]), (6, 3))
        self.assertEqual(env.jobs_open(), [])  # an estimate makes nothing
        made = env.actions.request({"series": series_id, "season": 1})
        self.assertEqual(made["count"], 3)
        everything = env.actions.request({"all": True})
        self.assertEqual(everything["count"], 1)  # season 2 was left; the others are already requested
        self.assertEqual(len(env.jobs_open()), 4)

    def test_13e_the_estimate_says_what_is_left_of_the_days_calls_and_when_a_quota_pause_ends(self):
        env = self.env(max_calls_per_day=10)
        env.episode(1)
        env.sync.run()
        env.store.record_llm_call("j", provider="gemini", model="m", task="translate", tokens_in=1, tokens_out=1,
                                  latency_ms=2_000, outcome="ok", at=env.clock.time())
        estimate = env.actions.request({"items": env.ids("7101"), "estimate_only": True})["estimate"]
        self.assertEqual(estimate["calls_left_today"], 9)
        self.assertEqual(estimate["minutes"], round(1 * 2.0 / 60, 1))
        self.assertGreater(estimate["finish_at"], env.clock.time())

    def test_13e_a_replace_request_turns_an_open_fill_job_into_the_replacement(self):
        env = self.env()
        env.episode(1)
        env.sync.run()
        (fill,) = env.actions.request({"items": env.ids("7101")})["created"]
        made = env.actions.request({"items": env.ids("7101"), "mode": "replace"})
        self.assertEqual((made["count"], made["merged"]), (0, 1))
        job = env.queue.jobs.get(fill)
        self.assertEqual((job["mode"], job["trigger"]), ("replace", "retranslate"))
        self.assertEqual(len(env.jobs_open()), 1)

    def test_13e_what_cannot_be_requested_says_why(self):
        env = self.env()
        env.episode(1, source=False)  # no source subtitle at all
        env.episode(2, vi=True)  # Bazarr lists a Vietnamese subtitle already
        env.episode(3)
        env.sync.run()
        os.remove(env.paths("7103")[0])  # listed by Bazarr but gone from the disk
        result = env.actions.request({"all": True})
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["skipped_by_reason"], {
            "no source subtitle in a configured source language": 1,
            "the item already has a subtitle in this language": 1,
            "the source subtitle is not on disk": 1,
        })

    def test_13e_a_request_must_name_what_it_is_about_in_exactly_one_way(self):
        env = self.env()
        env.sync.run()
        for body in ({}, {"items": [1], "all": True}, {"season": 1}, {"items": "x"}):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    env.actions.request(body)
        with self.assertRaises(LookupError):
            env.actions.request({"items": [9999]})


class Criterion13f(CatalogCase):
    def test_13f_an_ignored_item_and_a_removed_one_are_not_requested_by_the_policy_but_the_others_are(self):
        env = self.env(auto_request=True)
        env.episode(1)  # the admin ignores it
        env.episode(2, vi=True)  # the admin removes what the system made
        env.episode(3)
        env.sync.run()
        env.actions.ignore({"items": env.ids("7101")}, True)
        _, target = env.translated("7102")
        env.versions.remove(target)
        # Bazarr no longer sees the file, and wants it again.
        env.replace_entry(env.fake.entries[2], subtitles=env.fake.entries[2].subtitles[:1], missing=("vi",))
        env.clock.advance(60)
        env.sync.run()

        self.assertEqual(env.label("7101")["text"], "Missing · Ignored")
        self.assertEqual(env.label("7102")["text"], "Missing · Removed")
        result = env.auto.run()
        self.assertEqual(result.created, 1)
        (job,) = env.jobs_open()
        self.assertEqual((job["origin"], job["media_id"]), ("policy", "7103"))

    def test_13f_the_admin_can_still_ask_for_a_removed_item_by_hand_and_take_an_ignore_back(self):
        env = self.env()
        env.episode(1, vi=True)
        env.sync.run()
        _, target = env.translated("7101")
        env.versions.remove(target)
        env.replace_entry(env.fake.entries[1], subtitles=env.fake.entries[1].subtitles[:1], missing=("vi",))
        env.clock.advance(60)
        env.sync.run()
        env.actions.ignore({"items": env.ids("7101")}, True)
        self.assertEqual(env.actions.request({"items": env.ids("7101")})["skipped_by_reason"], {"ignored by the admin": 1})
        env.actions.ignore({"items": env.ids("7101")}, False)
        self.assertEqual(env.label("7101")["text"], "Missing · Removed")
        self.assertEqual(env.actions.request({"items": env.ids("7101")})["count"], 1)

    def test_13f_a_job_of_the_policy_does_not_bring_back_what_the_admin_removed_but_a_manual_one_does(self):
        # The attempt reads `origin` of the job (translation_attempt): the policy is not the admin.
        from tests.test_translation_attempt import Env as AttemptEnv

        env = AttemptEnv(self)
        env.run()
        env.versions.remove(str(env.output))
        self.assertEqual(env.run(origin="policy"), "skipped-existing-output")
        self.assertEqual(env.run(origin="manual"), "translated")


class Criterion13g(CatalogCase):
    def two_series(self, **settings):
        env = self.env(**settings)
        env.add_series("8", "Other", "80")
        for n in (1, 2, 3):
            env.episode(n)
        for n in (1, 2):
            env.episode(n, series="8")
        env.sync.run()
        return env

    def policy(self, env, bazarr_id, value):
        env.actions.set_policy(env.item(bazarr_id, "series").id, {"auto_request": value})

    def test_13g_auto_request_on_for_one_series_requests_only_that_series_as_policy_jobs(self):
        env = self.two_series()
        self.policy(env, "7", "on")
        result = env.auto.run()
        self.assertEqual((result.created, result.capped), (3, 0))
        jobs = env.jobs_open()
        self.assertEqual({job["media_id"][0] for job in jobs}, {"7"})
        self.assertEqual({(job["origin"], job["prio"]) for job in jobs}, {("policy", policy.AUTO)})
        self.assertEqual(env.auto.run().created, 0)  # the next sync asks nothing twice

    def test_13g_off_for_a_series_beats_on_globally_and_the_global_switch_covers_the_rest(self):
        env = self.two_series(auto_request=True)
        self.policy(env, "8", "off")
        env.auto.run()
        self.assertEqual({job["media_id"][0] for job in env.jobs_open()}, {"7"})
        self.policy(env, "8", "")  # follow the global setting again
        self.assertEqual(env.auto.run().created, 2)

    def test_13g_only_paths_keeps_the_policy_inside_the_named_folder(self):
        env = self.two_series(auto_request=True)
        with patch.dict(os.environ, {"GST_ONLY_PATHS": str(env.media_dir / "Other")}):
            result = env.auto.run()
        self.assertEqual(result.created, 2)
        self.assertEqual({job["media_id"][0] for job in env.jobs_open()}, {"8"})

    def test_13g_the_daily_cap_stops_the_policy_and_the_rest_stays_Missing(self):
        env = self.two_series(auto_request=True, auto_request_daily_cap=2)
        result = env.auto.run()
        self.assertEqual((result.created, result.capped), (2, 3))
        self.assertEqual(len(env.jobs_open()), 2)
        again = env.auto.run()  # the same day: the cap is spent
        self.assertEqual((again.created, again.capped), (0, 3))
        left = [b for b in ("7101", "7102", "7103", "8101", "8102") if env.label(b)["text"] == "Missing"]
        self.assertEqual(len(left), 3)
        env.clock.advance(86_400)  # tomorrow: a new day's cap
        self.assertEqual(env.auto.run().created, 2)

    def test_13g_the_cap_is_said_once_not_at_every_sync(self):
        events = []

        class Sink:
            def event(self, kind, **data):
                events.append(kind)

        env = self.two_series(auto_request=True, auto_request_daily_cap=2)
        env.auto.telemetry = Sink()
        env.auto.run()
        env.auto.run()
        env.auto.run()
        self.assertEqual(events.count("policy.capped"), 1)
        self.assertEqual(events.count("policy.requested"), 1)

    def test_13g_the_policy_is_off_by_default_and_a_movie_needs_the_global_switch(self):
        env = self.env()
        movie_dir = env.media_dir / "Heat"
        movie_dir.mkdir()
        (movie_dir / "Heat.en.srt").write_text(SRT, encoding="utf-8")
        env.fake.entries.append(CatalogEntry(
            kind="movie", bazarr_id="5", title="Heat", video_path=str(movie_dir / "Heat.mkv"),
            subtitles=(CatalogSubtitle("en", str(movie_dir / "Heat.en.srt")),), missing=("vi",),
        ))
        env.sync.run()
        self.assertEqual(env.auto.run().created, 0)
        env.settings["auto_request"] = True
        self.assertEqual(env.auto.run().created, 1)
        self.assertEqual(env.jobs_open()[0]["media_type"], "movie")


class Criterion13h(CatalogCase):
    def test_13h_an_item_whose_language_is_outside_the_bazarr_profile_can_still_be_requested(self):
        env = self.env()
        env.episode(1, missing=False)  # Bazarr wants no vi for it, and has none
        env.sync.run()
        self.assertEqual(env.label("7101")["text"], "Outside profile")
        made = env.actions.request({"items": env.ids("7101")})
        self.assertEqual(made["count"], 1)
        self.assertEqual(env.label("7101")["text"], "Outside profile · Requested")

    def test_13h_the_policy_leaves_an_outside_profile_item_alone(self):
        env = self.env(auto_request=True)
        env.episode(1, missing=False)
        env.sync.run()
        self.assertEqual(env.auto.run().created, 0)


class OverviewAndApi(CatalogCase):
    def test_the_overview_counts_what_the_catalog_and_the_open_jobs_say(self):
        env = self.env()
        for n in (1, 2, 3):
            env.episode(n)
        env.sync.run()
        env.actions.request({"items": env.ids("7101")})
        page = env.actions.overview()
        (series,) = page["series"]
        self.assertEqual((series["title"], series["episodes"], series["missing"], series["open_jobs"]),
                         ("Show", 3, {"vi": 3}, {"requested": 1}))
        self.assertEqual(page["languages"], ["vi"])
        self.assertEqual(env.actions.series_page(series["id"])["items"][0]["targets"][0]["text"], "Missing · Requested")

    def test_the_routes_map_to_the_use_cases_and_say_404_and_400(self):
        env = self.env()
        env.episode(1)
        self.assertEqual(handle(env.actions, "POST", "/api/catalog/sync")[0], 200)
        self.assertEqual(handle(env.actions, "GET", "/api/catalog/status")[1]["items"], 2)
        self.assertEqual(handle(env.actions, "GET", "/api/catalog")[1]["series"][0]["episodes"], 1)
        self.assertEqual(handle(env.actions, "GET", "/api/catalog/series/99999")[0], 404)
        self.assertEqual(handle(env.actions, "POST", "/api/jobs", {})[0], 400)
        status, body = handle(env.actions, "POST", "/api/jobs", {"all": True})
        self.assertEqual((status, body["count"]), (200, 1))
        self.assertEqual(handle(env.actions, "PUT", "/api/catalog/series/1/policy", {"auto_request": "maybe"})[0], 400)
        self.assertIsNone(handle(env.actions, "GET", "/api/units/1"))

    def test_a_policy_cannot_be_set_on_an_episode(self):
        env = self.env()
        env.episode(1)
        env.sync.run()
        with self.assertRaises(LookupError):
            env.actions.set_policy(env.ids("7101")[0], {"auto_request": "on"})


class StoreCatalog(CatalogCase):
    def test_a_read_applies_in_one_step_and_an_item_seen_again_is_no_longer_gone(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        series = CatalogEntry(kind="series", bazarr_id="1", title="S", series_key="k")
        episode = CatalogEntry(kind="episode", bazarr_id="2", parent_bazarr_id="1", series_key="k", season=1, episode=1,
                               subtitles=(CatalogSubtitle("en", "/a.en.srt"),), missing=("vi",))
        applied = store.catalog_apply([episode, series], 100.0)  # an episode listed before its series still finds it
        self.assertEqual((applied.added, applied.updated, applied.gone), (2, 0, 0))
        row = store.catalog_lookup("episode", "2")
        self.assertEqual((row.parent_id, row.subtitles[0].path, row.missing), (store.catalog_lookup("series", "1").id, "/a.en.srt", ("vi",)))
        applied = store.catalog_apply([series], 200.0)
        self.assertEqual((applied.added, applied.updated, applied.gone), (0, 1, 1))
        self.assertEqual(store.catalog_count(), 1)
        self.assertEqual(store.catalog_lookup("episode", "2").gone_at, 200.0)
        store.catalog_apply([series, episode], 300.0)
        self.assertIsNone(store.catalog_lookup("episode", "2").gone_at)

    def test_decisions_are_kept_per_unit_and_language_and_can_be_taken_back(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        unit = store.upsert_unit(movie_key="m", title="M")
        store.set_decision(unit, "vi", "ignored")
        store.set_decision(unit, "vi", "ignored")
        store.set_decision(unit, "de", "ignored")
        self.assertEqual(store.decisions(), {(unit, "vi"), (unit, "de")})
        store.set_decision(unit, "vi", None)
        self.assertEqual(store.decisions(), {(unit, "de")})


if __name__ == "__main__":
    unittest.main()
