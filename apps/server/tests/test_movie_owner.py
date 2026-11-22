"""P12b backend: a movie has a glossary, a style and character cards of its own, reached by the movie's unit id
(``/api/movies/{unit_id}/...``). They reuse the stored rows of an owner (``owner_kind = movies``); the stale count is
worked out for the movie's one unit. Every provider is a fake; nothing touches the network."""

import unittest

from subserver.api import library_api
from subserver.api import style_api

from .test_corrections import SETTINGS, source_text
from .test_style_profile import Env as StyleEnv

KYBER = "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n"


class MovieEnv(StyleEnv):
    def __init__(self, test):
        super().__init__(test)
        self.movie_dir = self.root / "media" / "Heat"
        self.movie_dir.mkdir()

    def translate_movie(self):
        source = self.movie_dir / "Heat.en.srt"
        source.write_text(source_text(1), encoding="utf-8")
        job = {
            "job_id": "auto-movie", "provider": "gemini-console", "media_type": "movie", "media_id": "5",
            "video_path": str(self.movie_dir / "Heat.mkv"), "subtitle_path": str(source),
            "output_path": str(self.movie_dir / "Heat.vi.srt"), "source_code": "en", "target_code": "vi",
            "target_language": "Vietnamese",
        }
        self.attempt.run(job, "", SETTINGS)
        unit = self.store.list_units(movies=True)[0]
        self.movie = unit.id
        return unit.id

    def rules_hash(self, unit_id):
        return self.library.unit_rules(self.store.get_unit(unit_id), "vi").hash


class MovieGlossaryTests(unittest.TestCase):
    def test_a_movie_glossary_is_saved_read_back_listed_and_restored_under_the_movie_not_a_series(self):
        env = MovieEnv(self)
        env.translate(1)
        series = env.store.list_series()[0].id
        movie = env.translate_movie()
        self.assertEqual(env.service.movie_glossary(movie, "vi")["exists"], False)

        preview = env.service.save_movie_glossary(movie, "vi", KYBER, dry_run=True)
        self.assertEqual((preview["saved"], preview["would_stale"]), (False, 1))  # the movie's one unit, not the episode
        self.assertIsNone(env.store.glossary("movies", "bazarr-movie-5", "vi"))

        saved = env.service.save_movie_glossary(movie, "vi", KYBER)
        self.assertEqual((saved["saved"], saved["version"]), (True, 1))
        page = env.service.movie_glossary(movie, "vi")
        self.assertEqual((page["exists"], page["version"], page["stale_units"]), (True, 1, 1))
        self.assertEqual(page["doc"]["terms"][0]["target"], "tinh thể Kyber")
        self.assertIsNone(env.store.glossary("series", "bazarr-7", "vi"))  # the series was not touched
        self.assertEqual(env.service.glossary(series, "vi")["exists"], False)

        env.service.save_movie_glossary(movie, "vi", None, doc={"version": 1, "terms": [{"source": "A", "target": "B"}]})
        history = env.service.movie_glossary_history(movie, "vi")["items"]
        self.assertEqual([(h["version"], h["terms"]) for h in history], [(1, 1)])
        restored = env.service.restore_movie_glossary(movie, "vi", 1)
        self.assertEqual((restored["ok"], restored["restored"]), (True, 1))
        self.assertEqual(env.service.movie_glossary(movie, "vi")["doc"]["terms"][0]["source"], "Kyber crystal")

    def test_a_bad_movie_glossary_is_refused_and_leaves_the_stored_one_alone(self):
        env = MovieEnv(self)
        movie = env.translate_movie()
        env.service.save_movie_glossary(movie, "vi", KYBER)
        for bad in ("terms: nope\n", "terms:\n  - {source: A}\n"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                env.service.save_movie_glossary(movie, "vi", bad)
        self.assertEqual(env.service.movie_glossary(movie, "vi")["version"], 1)

    def test_the_glossary_of_a_movie_changes_the_rules_hash_of_that_movie_only(self):
        env = MovieEnv(self)
        env.translate(1)
        episode = env.unit(1)
        movie = env.translate_movie()
        before = (env.rules_hash(movie), env.rules_hash(episode))
        env.service.save_movie_glossary(movie, "vi", KYBER)
        self.assertNotEqual(env.rules_hash(movie), before[0])
        self.assertEqual(env.rules_hash(episode), before[1])


class MovieStyleTests(unittest.TestCase):
    def test_a_movie_style_is_saved_with_its_impact_and_changes_the_hash_of_the_movie(self):
        env = MovieEnv(self)
        env.translate(1)
        movie = env.translate_movie()
        self.assertEqual(env.library.unit_summary(env.store.get_unit(movie))["status"], "current")
        hash_before = env.rules_hash(movie)

        preview = env.style.save_movie_style(movie, {"lang": "vi", "preset": "drama", "style": {"tone": "grim"}, "dry_run": True})
        self.assertEqual((preview["saved"], preview["would_stale"]), (False, 1))
        self.assertEqual(env.style.movie_overview(movie)["style"], {})  # a dry run saves nothing
        self.assertEqual(env.rules_hash(movie), hash_before)

        saved = env.style.save_movie_style(movie, {"lang": "vi", "preset": "drama", "style": {"tone": "grim"}})
        self.assertEqual((saved["saved"], saved["would_stale"]), (True, 1))
        self.assertNotEqual(env.rules_hash(movie), hash_before)  # the translation made without it is now stale
        self.assertEqual(env.library.unit_summary(env.store.get_unit(movie))["status"], "stale")
        page = env.style.movie_overview(movie)
        self.assertEqual((page["movie"], page["preset"], page["style"]), ({"id": movie, "title": "Heat"}, "drama", {"tone": "grim"}))
        self.assertNotIn("series", page)
        self.assertEqual(env.styles.owner_style("movies", "bazarr-movie-5")[0], "drama")
        self.assertEqual(env.styles.owner_style("series", "bazarr-7")[0], "")  # no series got it

    def test_a_movie_style_does_not_flag_the_episodes_of_a_series(self):
        env = MovieEnv(self)
        env.translate(1)
        series = env.store.list_series()[0].id
        movie = env.translate_movie()
        env.style.save_movie_style(movie, {"lang": "vi", "style": {"tone": "grim"}})
        self.assertEqual(env.library.series_units(series)["units"][0]["status"], "current")

    def test_cards_of_a_movie_are_counted_saved_and_deleted(self):
        env = MovieEnv(self)
        movie = env.translate_movie()
        card = {"name": "Neil", "speech": "clipped"}
        counted = env.style.save_movie_card(movie, {"lang": "vi", "card": card, "dry_run": True})
        self.assertEqual((counted["saved"], counted["would_stale"]), (False, 1))  # the rules hash covers every card of the owner
        self.assertEqual(env.style.movie_overview(movie)["cards"], [])
        env.style.save_movie_card(movie, {"lang": "vi", "card": card})
        self.assertEqual([c["name"] for c in env.style.movie_overview(movie)["cards"]], ["Neil"])
        self.assertEqual([c.name for c in env.styles.cards("movies", "bazarr-movie-5")], ["Neil"])
        self.assertTrue(env.style.delete_movie_card(movie, "Neil")["ok"])
        with self.assertRaises(LookupError):
            env.style.delete_movie_card(movie, "Neil")

    def test_the_trial_of_a_movie_style_translates_its_own_subtitle_and_writes_nothing(self):
        env = MovieEnv(self)
        env.translate(1)
        movie = env.translate_movie()
        target = env.movie_dir / "Heat.vi.srt"
        written = target.read_bytes()
        files = sorted(str(p) for p in env.root.rglob("*") if p.is_file() and "app.db" not in p.name)
        env.fake.calls.clear()
        result = env.style.preview_movie(movie, {"lang": "vi", "count": 3, "style": {"tone": "frantic"}})
        self.assertEqual((result["unit_id"], len(result["rows"]), len(env.fake.calls)), (movie, 3, 1))
        self.assertIn("Tone: frantic", env.fake.calls[0].system)
        self.assertEqual(target.read_bytes(), written)
        self.assertEqual(sorted(str(p) for p in env.root.rglob("*") if p.is_file() and "app.db" not in p.name), files)
        with self.assertRaises(ValueError):  # an episode of a series is not this movie's unit
            env.style.preview_movie(movie, {"unit_id": env.unit(1)})


class MovieRoutingTests(unittest.TestCase):
    def test_the_movie_routes_answer_by_unit_id_and_say_404_for_what_is_not_a_movie(self):
        env = MovieEnv(self)
        env.translate(1)
        episode = env.unit(1)
        movie = env.translate_movie()

        def style(method, path, query=None, body=None):
            return style_api.handle(env.style, method, path, query or {}, body)

        def library(method, path, query=None, body=None):
            return library_api.handle(env.service, method, path, query or {}, body)

        status, page = style("GET", f"/api/movies/{movie}/style", {"lang": ["vi"]})
        self.assertEqual((status, page["movie"]["title"], len(page["presets"])), (200, "Heat", 5))
        self.assertEqual(style("PUT", f"/api/movies/{movie}/style", {}, {"preset": "drama", "style": {"tone": "x"}})[1]["saved"], True)
        self.assertEqual(style("PUT", f"/api/movies/{movie}/style", {}, {"style": {"formality": "rude"}})[0], 400)
        self.assertEqual(style("PUT", f"/api/movies/{movie}/cards", {}, {"card": {"name": "Neil", "speech": "x"}})[0], 200)
        self.assertEqual(style("POST", f"/api/movies/{movie}/cards/delete", {}, {"name": "Neil"})[0], 200)
        self.assertEqual(style("POST", f"/api/movies/{movie}/style/preview", {}, {"cues": ["a"]})[0], 400)
        self.assertEqual(style("POST", f"/api/movies/{movie}/style/preview", {}, {"cues": [2]})[0], 200)

        self.assertEqual(library("GET", f"/api/movies/{movie}/glossary", {"lang": ["vi"]})[1]["exists"], False)
        self.assertEqual(library("GET", f"/api/movies/{movie}/glossary")[0], 400)  # lang is required
        status, saved = library("PUT", f"/api/movies/{movie}/glossary", {}, {"lang": "vi", "yaml": KYBER})
        self.assertEqual((status, saved["version"]), (200, 1))
        self.assertEqual(library("PUT", f"/api/movies/{movie}/glossary", {}, {"lang": "vi", "yaml": KYBER, "dry_run": True})[1]["saved"], False)
        library("PUT", f"/api/movies/{movie}/glossary", {}, {"lang": "vi", "yaml": KYBER + "style: casual\n"})
        self.assertEqual(len(library("GET", f"/api/movies/{movie}/glossary/history", {"lang": ["vi"]})[1]["items"]), 1)
        self.assertEqual(library("POST", f"/api/movies/{movie}/glossary/restore", {}, {"lang": "vi", "version": 1})[0], 200)
        self.assertEqual(library("POST", f"/api/movies/{movie}/glossary/restore", {}, {"lang": "vi"})[0], 400)

        for unit in (episode, 999):  # an episode of a series, and nothing at all
            self.assertEqual(library("GET", f"/api/movies/{unit}/glossary", {"lang": ["vi"]})[0], 404, unit)
        self.assertEqual(style("GET", f"/api/movies/{episode}/style")[0], 404)
        self.assertEqual(style("GET", "/api/movies/999/style")[0], 404)

    def test_a_glossary_downloads_as_a_yaml_file_named_after_its_owner(self):
        env = MovieEnv(self)
        env.translate(1)
        movie = env.translate_movie()
        series = env.service.library.series_list()[0]["id"]
        env.service.save_movie_glossary(movie, "vi", KYBER)

        status, name, text = library_api.download(env.service, f"/api/movies/{movie}/glossary.yaml", {"lang": ["vi"]})
        self.assertEqual((status, name), (200, "glossary-heat-vi.yaml"))
        self.assertIn("Kyber crystal", text)
        # The series has none yet: an empty file, as the page's own `yaml` is empty.
        status, name, text = library_api.download(env.service, f"/api/series/{series}/glossary.yaml", {"lang": ["vi"]})
        self.assertEqual((status, text), (200, ""))
        self.assertTrue(name.startswith("glossary-") and name.endswith("-vi.yaml"), name)

        self.assertEqual(library_api.download(env.service, f"/api/movies/{movie}/glossary.yaml", {})[0], 400)  # lang
        self.assertEqual(library_api.download(env.service, "/api/movies/999/glossary.yaml", {"lang": ["vi"]})[0], 404)
        self.assertEqual(library_api.download(env.service, f"/api/movies/{env.unit(1)}/glossary.yaml", {"lang": ["vi"]})[0], 404)
        self.assertIsNone(library_api.download(env.service, f"/api/movies/{movie}/glossary", {"lang": ["vi"]}))

    def test_the_movie_paths_are_claimed_by_the_routers_that_serve_them(self):
        for path in ("/api/movies/3/glossary", "/api/movies/3/glossary/history", "/api/movies/3/glossary/restore"):
            self.assertTrue(library_api.handles(path), path)
        for path in ("/api/movies/3/style", "/api/movies/3/style/preview", "/api/movies/3/cards", "/api/movies/3/cards/delete"):
            self.assertTrue(style_api.handles(path), path)
        self.assertFalse(style_api.handles("/api/movies/3/glossary"))


if __name__ == "__main__":
    unittest.main()
