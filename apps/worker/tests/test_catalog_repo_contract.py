"""Contract of the catalog (series, units, outputs, corrections) on the real SQLite store (ADR-12)."""

import dataclasses
import tempfile
import time
import unittest
from pathlib import Path

from gst_worker.ports import Correction, Output, Series, Unit
from gst_worker.store import Store


class CatalogRepoContract:
    """Mixed into a TestCase that provides ``self.repo``."""

    repo: Store

    def episode(self, series_id, season=1, episode=1, **fields):
        return self.repo.upsert_unit(series_id=series_id, season=season, episode=episode, **fields)

    def test_empty_catalog(self):
        self.assertIsNone(self.repo.get_series(1))
        self.assertIsNone(self.repo.get_unit(1))
        self.assertIsNone(self.repo.find_unit(1, 1, 1))
        self.assertEqual((self.repo.list_series(), self.repo.list_units(1), self.repo.list_units(movies=True)), ([], [], []))
        self.assertEqual(self.repo.outputs(1), [])
        self.assertEqual(self.repo.corrections(1, "vi"), [])
        self.assertIsNone(self.repo.get_correction(1))
        self.assertFalse(self.repo.deactivate_correction(1))

    def test_a_series_is_a_named_immutable_value_with_defaults(self):
        series_id = self.repo.upsert_series("tvdb-1", title="Show", bazarr_id="7")
        got = self.repo.get_series(series_id)
        self.assertIsInstance(got, Series)
        self.assertEqual((got.id, got.key, got.title, got.bazarr_id), (series_id, "tvdb-1", "Show", "7"))
        self.assertEqual((got.stale_policy, got.memory, got.specials_after), ("flag", "", ""))
        with self.assertRaises(AttributeError):
            got.title = "other"

    def test_upserting_a_series_keeps_its_id_and_only_non_blank_fields_refresh_it(self):
        first = self.repo.upsert_series("k", title="Old", bazarr_id="1")
        self.assertEqual(self.repo.upsert_series("k"), first)
        self.assertEqual(self.repo.upsert_series("k", title="New"), first)
        got = self.repo.get_series(first)
        self.assertEqual((got.title, got.bazarr_id), ("New", "1"))
        self.assertNotEqual(self.repo.upsert_series("other"), first)

    def test_series_are_listed_by_title_ignoring_case(self):
        self.repo.upsert_series("b", title="banana")
        self.repo.upsert_series("a", title="Apple")
        self.repo.upsert_series("c", title="Cherry")
        self.assertEqual([s.title for s in self.repo.list_series()], ["Apple", "banana", "Cherry"])

    def test_series_memory_policy_changes_only_the_fields_given(self):
        series_id = self.repo.upsert_series("k", title="Show")
        self.repo.set_series_memory(series_id, memory="on")
        self.repo.set_series_memory(series_id, specials_after="S02E05")
        got = self.repo.get_series(series_id)
        self.assertEqual((got.memory, got.specials_after), ("on", "S02E05"))
        self.repo.set_series_memory(series_id, memory="")
        self.assertEqual(self.repo.get_series(series_id).memory, "")
        self.assertEqual(self.repo.get_series(series_id).specials_after, "S02E05")

    def test_an_episode_round_trips_as_a_unit(self):
        series_id = self.repo.upsert_series("k")
        unit_id = self.episode(series_id, 2, 5, title="Pilot", media_type="series", bazarr_id="105",
                               video_path="/m/a.mkv", source_path="/m/a.en.srt", source_code="en", source_language="English")
        got = self.repo.get_unit(unit_id)
        self.assertIsInstance(got, Unit)
        self.assertEqual((got.series_id, got.movie_key, got.season, got.episode), (series_id, None, 2, 5))
        self.assertEqual((got.title, got.media_type, got.bazarr_id), ("Pilot", "series", "105"))
        self.assertEqual((got.video_path, got.source_path, got.source_code, got.source_language),
                         ("/m/a.mkv", "/m/a.en.srt", "en", "English"))
        self.assertEqual(self.repo.find_unit(series_id, 2, 5), got)
        self.assertIsNone(self.repo.find_unit(series_id, 2, 6))
        with self.assertRaises(AttributeError):
            got.title = "other"

    def test_upserting_a_unit_keeps_its_id_and_a_blank_never_erases_a_known_value(self):
        series_id = self.repo.upsert_series("k")
        unit_id = self.episode(series_id, title="Pilot", source_path="/m/a.en.srt", source_code="en")
        self.assertEqual(self.episode(series_id, title="", source_path="", bazarr_id="9"), unit_id)
        got = self.repo.get_unit(unit_id)
        self.assertEqual((got.title, got.source_path, got.source_code, got.bazarr_id), ("Pilot", "/m/a.en.srt", "en", "9"))
        self.assertEqual(self.episode(series_id, title="Renamed"), unit_id)
        self.assertEqual(self.repo.get_unit(unit_id).title, "Renamed")

    def test_a_unit_is_an_episode_or_a_movie_never_both_or_neither(self):
        series_id = self.repo.upsert_series("k")
        with self.assertRaises(ValueError):
            self.repo.upsert_unit(series_id=series_id, movie_key="m", season=1, episode=1)
        with self.assertRaises(ValueError):
            self.repo.upsert_unit(title="nobody")

    def test_episodes_list_in_story_order_and_movies_by_title(self):
        series_id = self.repo.upsert_series("k")
        other = self.repo.upsert_series("o")
        for season, episode in [(2, 1), (1, 2), (1, 1)]:
            self.episode(series_id, season, episode)
        self.episode(other, 1, 1)
        self.repo.upsert_unit(movie_key="m-z", title="Zulu")
        self.repo.upsert_unit(movie_key="m-a", title="alpha")
        self.assertEqual([(u.season, u.episode) for u in self.repo.list_units(series_id)], [(1, 1), (1, 2), (2, 1)])
        self.assertEqual([u.title for u in self.repo.list_units(movies=True)], ["alpha", "Zulu"])
        self.assertTrue(all(u.series_id is None and u.movie_key for u in self.repo.list_units(movies=True)))

    def test_outputs_are_one_per_language_and_a_blank_language_name_keeps_the_known_one(self):
        unit_id = self.episode(self.repo.upsert_series("k"))
        self.repo.set_output(unit_id, "vi", "/m/a.vi.srt", "Vietnamese")
        self.repo.set_output(unit_id, "de", "/m/a.de.srt")
        self.repo.set_output(unit_id, "vi", "/m/b.vi.srt")
        got = self.repo.outputs(unit_id)
        self.assertEqual(got, [Output(unit_id, "de", "/m/a.de.srt", ""), Output(unit_id, "vi", "/m/b.vi.srt", "Vietnamese")])
        self.assertEqual(self.repo.outputs(unit_id + 100), [])

    def test_corrections_are_named_values_oldest_first_and_scoped_by_unit_and_language(self):
        unit_id = self.episode(self.repo.upsert_series("k"))
        other = self.episode(self.repo.upsert_series("k"), 1, 2)
        first = self.repo.add_correction(unit_id, "vi", "note", {"text": "one"})
        second = self.repo.add_correction(unit_id, "vi", "term", {"source": "a", "target": "b"})
        self.repo.add_correction(unit_id, "de", "note", {"text": "de"})
        self.repo.add_correction(other, "vi", "note", {"text": "other"})
        got = self.repo.corrections(unit_id, "vi")
        self.assertEqual([c.id for c in got], [first, second])
        self.assertIsInstance(got[0], Correction)
        self.assertEqual((got[0].unit_id, got[0].target_lang, got[0].kind, got[0].payload, got[0].active),
                         (unit_id, "vi", "note", {"text": "one"}, True))
        self.assertEqual(self.repo.get_correction(second), got[1])
        with self.assertRaises(AttributeError):
            got[0].kind = "term"

    def test_deactivating_hides_a_correction_unless_asked_and_only_works_once(self):
        unit_id = self.episode(self.repo.upsert_series("k"))
        keep = self.repo.add_correction(unit_id, "vi", "note", {"text": "keep"})
        drop = self.repo.add_correction(unit_id, "vi", "note", {"text": "drop"})
        self.assertTrue(self.repo.deactivate_correction(drop))
        self.assertFalse(self.repo.deactivate_correction(drop))
        self.assertEqual([c.id for c in self.repo.corrections(unit_id, "vi")], [keep])
        everything = self.repo.corrections(unit_id, "vi", active_only=False)
        self.assertEqual([(c.id, c.active) for c in everything], [(keep, True), (drop, False)])
        self.assertFalse(self.repo.get_correction(drop).active)

    def test_a_payload_keeps_its_structure_and_text(self):
        unit_id = self.episode(self.repo.upsert_series("k"))
        payload = {"cue": 3, "text": "Xin chào – tạm biệt", "source_text": "Hello", "aliases": ["a", "b"]}
        correction_id = self.repo.add_correction(unit_id, "vi", "cue_edit", payload)
        self.assertEqual(self.repo.get_correction(correction_id).payload, payload)


class SqliteCatalogRepoTests(CatalogRepoContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Store(Path(directory.name) / "app.db")
        self.addCleanup(self.repo.close)


if __name__ == "__main__":
    unittest.main()
