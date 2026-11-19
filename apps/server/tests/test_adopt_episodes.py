"""The Context tab lists the episodes of a series the catalog knows, also when no job ever ran for them (subtitles that
were translated before, or by someone else): their units are made from the catalog, with the source subtitle it picks."""

import unittest

from subserver.glossaries import Glossaries
from subserver.memory import StoryMemory
from subserver.ports import CatalogSubtitle
from subserver.units import UnitService

from .test_catalog import CatalogCase, Env


class AdoptEpisodesTests(CatalogCase):
    def setup(self, *, wired=True):
        env = Env(self)
        env.episode(1, vi=True)  # already translated by another system: Bazarr lists a vi subtitle
        env.episode(2)
        env.third = env.episode(3, source=False)  # no source subtitle at all
        env.sync.run()
        memory = StoryMemory(env.store, env.store, settings_source=lambda: env.settings)
        service = UnitService(
            env.library, Glossaries(env.store), env.versions, env.queue, memory=memory, settings_source=lambda: env.settings,
            source_languages=(lambda: env.config["source_languages"]) if wired else None,
        )
        series_id = env.store.upsert_series(env.item("7", "series").series_key, title="Show", bazarr_id="7")
        return env, service, series_id

    def test_episodes_with_no_job_yet_are_listed_with_their_source_once_the_tab_adopts_them(self):
        env, service, series_id = self.setup()
        self.assertEqual(env.store.list_units(series_id), [])
        self.assertEqual(service.adopt_episodes(series_id), {"ok": True, "adopted": 3})
        rows = service.series_memory(series_id)["units"]
        self.assertEqual([(r["label"], r["has_source"], r["has_facts"]) for r in rows], [
            ("S01E01", True, False), ("S01E02", True, False), ("S01E03", False, False),
        ])

    def test_reading_the_memory_of_a_series_makes_no_unit(self):
        """R14-3: ``GET /api/series/{id}/memory`` is polled by the page; a read never writes."""
        env, service, series_id = self.setup()
        before = env.store.query("SELECT COUNT(*) AS n FROM units")[0]["n"]
        for _ in range(3):
            self.assertEqual(service.series_memory(series_id)["units"], [])
        self.assertEqual(env.store.query("SELECT COUNT(*) AS n FROM units")[0]["n"], before)

    def test_asking_again_makes_nothing_new_and_keeps_the_units(self):
        env, service, series_id = self.setup()
        service.adopt_episodes(series_id)
        first = [u.id for u in env.store.list_units(series_id)]
        self.assertEqual(service.adopt_catalog_episodes(series_id), 0)
        self.assertEqual(service.adopt_episodes(series_id)["adopted"], 0)
        self.assertEqual([u.id for u in env.store.list_units(series_id)], first)

    def test_an_episode_that_gets_its_source_later_is_given_it(self):
        env, service, series_id = self.setup()
        service.adopt_episodes(series_id)
        unit = env.store.find_unit(series_id, 1, 3)
        self.assertEqual(unit.source_path, "")
        env.replace_entry(env.third, subtitles=(CatalogSubtitle("en", env.paths("7102")[0]),))  # Bazarr finds one
        env.sync.run()
        self.assertEqual(service.adopt_catalog_episodes(series_id), 1)
        self.assertTrue(env.store.find_unit(series_id, 1, 3).source_path)

    def test_without_the_source_languages_wired_nothing_is_made(self):
        env, service, series_id = self.setup(wired=False)
        self.assertEqual(service.adopt_episodes(series_id)["adopted"], 0)
        self.assertEqual(service.series_memory(series_id)["units"], [])


if __name__ == "__main__":
    unittest.main()
