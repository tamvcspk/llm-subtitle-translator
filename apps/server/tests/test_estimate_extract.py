"""The Estimate of a Translate request counts the fact reads its jobs would run first (SPEC FR-15.5.1, criterion 24k, P15g).
Same fake library as ``test_catalog``; nothing is read by a model and nothing is queued."""

import unittest

from pathlib import Path

from subcore.memory import EXTRACT_PROMPT_VERSION, Facts

from subserver.domain.ports import CatalogEntry, CatalogSubtitle
from subserver.services.job_requests import Selection
from subserver.services.memory import StoryMemory
from subserver.services.versions import sha256_file
from tests.test_catalog import SRT, CatalogCase, Env, VI

FR = {"code": "fr", "language": "French", "enabled": True}


class Fixture(Env):
    """A series of ``episodes`` S01 episodes (episode 1 first) with memory switched on, as the worker wires it."""

    def __init__(self, case, episodes=3, **settings):
        super().__init__(case, memory_enabled=True, memory_model="flash-lite", **settings)
        for n in range(1, episodes + 1):
            self.episode(n)
        self.sync.run()
        self.requests.memory = StoryMemory(self.store, self.store, settings_source=lambda: self.settings)

    def estimate(self, *bazarr_ids, **body):
        return self.actions.request({"items": self.ids(*bazarr_ids), "estimate_only": True, **body})["estimate"]

    def facts_for(self, bazarr_id, *, sha=None):
        """The worker has read this episode already (``sha``: of another version of its source subtitle)."""
        entry = self.item(bazarr_id)
        series = self.store.catalog_entry(entry.parent_id)
        source, _ = self.paths(bazarr_id)
        unit_id = self.library.register_entry(entry, series, source_path=source, source_code="en")
        self.store.save_facts(unit_id, sha or sha256_file(Path(source)), EXTRACT_PROMPT_VERSION,
                              model="m", facts=Facts())
        return unit_id


class Criterion24k(CatalogCase):
    def test_24k_episode_3_with_no_facts_reads_episodes_1_to_3(self):
        env = Fixture(self)
        estimate = env.estimate("7103")
        self.assertEqual((estimate["jobs"], estimate["calls"], estimate["extract_calls"]), (1, 1, 3))
        self.assertEqual(estimate["extract_model"], "flash-lite")
        self.assertEqual(env.jobs_open(), [])  # an estimate makes nothing, not even the units it looked at
        self.assertIsNone(env.store.series_by_key(env.item("7103").series_key))  # nor did it register a series

    def test_24k_each_episode_is_counted_once_for_the_whole_request(self):
        env = Fixture(self)
        self.assertEqual(env.estimate("7102", "7103")["extract_calls"], 3)
        env.config["target_languages"] = [VI, FR]
        estimate = env.estimate("7102", "7103")
        self.assertEqual((estimate["jobs"], estimate["extract_calls"]), (4, 3))

    def test_24k_a_series_request_counts_the_series_once(self):
        env = Fixture(self, episodes=4)
        (series_id,) = [e.id for e in env.store.catalog_entries("series")]
        estimate = env.actions.request({"series": series_id, "estimate_only": True})["estimate"]
        self.assertEqual((estimate["jobs"], estimate["extract_calls"]), (4, 4))

    def test_24k_memory_off_counts_none_and_names_no_model(self):
        env = Fixture(self)
        env.settings["memory_enabled"] = False
        estimate = env.estimate("7103")
        self.assertEqual((estimate["extract_calls"], estimate["extract_model"]), (0, ""))

    def test_24k_a_series_can_override_the_global_switch_either_way(self):
        env = Fixture(self)
        env.library.register_entry(env.item("7103"), env.store.catalog_entry(env.item("7103").parent_id))
        series = env.store.series_by_key(env.item("7103").series_key)
        env.store.set_series_memory(series.id, memory="off")
        self.assertEqual(env.estimate("7103")["extract_calls"], 0)
        env.settings["memory_enabled"] = False
        env.store.set_series_memory(series.id, memory="on")
        self.assertEqual(env.estimate("7103")["extract_calls"], 3)

    def test_24k_facts_that_are_already_there_cost_nothing(self):
        env = Fixture(self)
        for bazarr_id in ("7101", "7102", "7103"):
            env.facts_for(bazarr_id)
        self.assertEqual(env.estimate("7103")["extract_calls"], 0)

    def test_24k_only_the_episodes_without_facts_are_counted(self):
        env = Fixture(self)
        env.facts_for("7101")
        self.assertEqual(env.estimate("7103")["extract_calls"], 2)

    def test_24k_facts_of_an_older_version_of_the_source_do_not_count(self):
        env = Fixture(self)
        for bazarr_id in ("7101", "7102"):
            env.facts_for(bazarr_id)
        env.facts_for("7103", sha="a-source-that-has-since-changed")
        self.assertEqual(env.estimate("7103")["extract_calls"], 1)

    def test_24k_an_earlier_episode_without_a_source_subtitle_is_not_a_call(self):
        env = Fixture(self, episodes=0)
        env.episode(1, source=False)
        env.episode(2)
        env.episode(3)
        env.sync.run()
        self.assertEqual(env.estimate("7103")["extract_calls"], 2)

    def test_24k_a_later_episode_is_never_read_for_an_earlier_one(self):
        env = Fixture(self)
        self.assertEqual(env.estimate("7101")["extract_calls"], 1)
        self.assertEqual(env.estimate("7102")["extract_calls"], 2)

    def test_24k_another_series_is_counted_on_its_own(self):
        env = Fixture(self, episodes=0)
        env.add_series("8", "Other", "80")
        for n in (1, 2):
            env.episode(n)
            env.episode(n, series="8")
        env.sync.run()
        self.assertEqual(env.estimate("7102", "8102")["extract_calls"], 4)

    def test_24k_a_movie_has_no_story_to_read(self):
        env = Fixture(self, episodes=0)
        source = env.media_dir / "Film.en.srt"
        source.write_text(SRT, encoding="utf-8")
        env.fake.entries.append(CatalogEntry(
            kind="movie", bazarr_id="m1", title="Film", missing=("vi",), video_path=str(env.media_dir / "Film.mkv"),
            subtitles=(CatalogSubtitle("en", str(source)),),
        ))
        env.sync.run()
        movie = env.store.catalog_lookup("movie", "m1")
        estimate = env.actions.request({"items": [movie.id], "estimate_only": True})["estimate"]
        self.assertEqual((estimate["jobs"], estimate["extract_calls"]), (1, 0))

    def test_24k_the_time_includes_the_reads(self):
        env = Fixture(self)
        env.store.record_llm_call("j", provider="gemini", model="m", task="translate", tokens_in=1, tokens_out=1,
                                  latency_ms=60_000, outcome="ok", at=env.clock.time())
        estimate = env.estimate("7103")
        self.assertEqual((estimate["calls"], estimate["extract_calls"], estimate["minutes"]), (1, 3, 4.0))

    def test_24k_the_automatic_policy_does_not_pay_for_the_count(self):
        env = Fixture(self)
        plan = env.requests.plan(Selection(everything=True), measure=False)
        self.assertEqual(plan.estimate.extract_calls, 0)

    def test_24k_without_story_memory_wired_the_count_is_zero(self):
        env = Fixture(self)
        env.requests.memory = None
        self.assertEqual(env.estimate("7103")["extract_calls"], 0)


if __name__ == "__main__":
    unittest.main()
