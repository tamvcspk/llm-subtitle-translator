"""P12b: a character is two rows (its Name in the glossary, its Voice in a card). They are saved, or the card is deleted,
in one transaction, so a failure leaves neither half behind. Every provider is a fake; nothing touches the network."""

import unittest

from subserver.api import style_api

from .test_style_profile import Env


def names(env, series):
    doc = env.service.glossary(series, "vi")["doc"]
    return [c["name"] for c in doc.get("characters", [])]


def cards(env, series):
    return [c["name"] for c in env.style.overview(series, "vi")["cards"]]


class CharacterWriteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        self.env.translate(1)
        self.series = self.env.service.library.series_list()[0]["id"]

    def save(self, body):
        return self.env.style.save_character(self.series, {"lang": "vi", **body})

    def test_name_and_voice_are_written_together_and_a_rename_moves_both(self):
        self.save({"glossary": {"version": 1, "characters": [{"name": "Vi", "target": "Vi"}]},
                   "card": {"card": {"name": "Vi", "speech": "blunt"}}})
        self.assertEqual((names(self.env, self.series), cards(self.env, self.series)), (["Vi"], ["Vi"]))
        self.save({"glossary": {"version": 1, "characters": [{"name": "Violet", "target": "Vi"}]},
                   "card": {"card": {"name": "Violet", "speech": "blunt"}, "replaces": "Vi"}})
        self.assertEqual((names(self.env, self.series), cards(self.env, self.series)), (["Violet"], ["Violet"]))

    def test_dry_run_counts_one_set_over_both_halves_and_writes_nothing(self):
        # The Name and the Voice both reach episode 1; it is flagged once, not once per half (B1 of the P19 review).
        body = {"glossary": {"version": 1, "characters": [{"name": "Vi", "target": "Vi", "aliases": ["Violet"]}]},
                "card": {"card": {"name": "Vi", "speech": "blunt", "aliases": ["Violet"]}}}
        counted = self.save({**body, "dry_run": True})
        self.assertEqual((counted["ok"], counted["saved"], counted["would_stale"]), (True, False, 1))
        self.assertEqual((names(self.env, self.series), cards(self.env, self.series)), ([], []))
        saved = self.save(body)
        self.assertEqual((saved["saved"], saved["would_stale"]), (True, 1))
        self.assertEqual((names(self.env, self.series), cards(self.env, self.series)), (["Vi"], ["Vi"]))
        again = self.save({**body, "dry_run": True})
        self.assertEqual(again["would_stale"], 0)  # nothing new to flag: the episode is already counted as stale

    def test_dry_run_of_a_delete_counts_what_losing_the_voice_would_flag(self):
        self.save({"glossary": {"version": 1, "characters": [{"name": "Vi"}]}, "card": {"card": {"name": "Vi", "speech": "blunt"}}})
        counted = self.save({"glossary": {"version": 1, "characters": [{"name": "Vi"}]}, "delete_card": "Vi", "dry_run": True})
        self.assertEqual((counted["saved"], cards(self.env, self.series)), (False, ["Vi"]))  # still there

    def test_a_bad_card_stops_the_save_before_the_name_is_written(self):
        with self.assertRaises(ValueError):
            self.save({"glossary": {"version": 1, "characters": [{"name": "Vi"}]}, "card": {"card": {"name": "Vi", "nonsense": 1}}})
        self.assertEqual((names(self.env, self.series), cards(self.env, self.series)), ([], []))

    def test_a_bad_glossary_stops_the_save_before_the_card_is_written(self):
        with self.assertRaises(ValueError):
            self.save({"glossary": {"version": 1, "terms": [{"source": "x"}]}, "card": {"card": {"name": "Vi", "speech": "x"}}})
        self.assertEqual(cards(self.env, self.series), [])

    def test_deleting_a_card_that_is_not_there_rolls_the_name_back_too(self):
        self.save({"glossary": {"version": 1, "characters": [{"name": "Vi"}, {"name": "Jinx"}]}})
        with self.assertRaises(LookupError):
            self.save({"glossary": {"version": 1, "characters": [{"name": "Vi"}]}, "delete_card": "Jinx"})
        self.assertEqual(names(self.env, self.series), ["Vi", "Jinx"])  # the Name is still there: nothing was half done

    def test_delete_removes_the_voice_and_rewrites_the_name_list_in_one_step(self):
        self.save({"glossary": {"version": 1, "characters": [{"name": "Vi"}, {"name": "Jinx"}]}, "card": {"card": {"name": "Jinx", "speech": "wild"}}})
        self.save({"glossary": {"version": 1, "characters": [{"name": "Vi"}]}, "delete_card": "Jinx"})
        self.assertEqual((names(self.env, self.series), cards(self.env, self.series)), (["Vi"], []))

    def test_what_is_asked_must_make_sense(self):
        for body in ({}, {"glossary": "x"}, {"card": {"nope": 1}}, {"card": {"card": {"name": "A"}}, "delete_card": "A"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.save(body)

    def test_an_episode_style_counts_every_language_so_it_never_guesses_one(self):
        # It used to count one language, taken from the first translation (P12b), and refused when there was none. The
        # episode's style is laid under every language, so the count is over all of them (R12-7): an episode with no
        # translation truly flags nothing, and no language has to be named.
        unit = self.env.store.upsert_unit(series_id=self.series, season=1, episode=9, title="Untranslated")
        counted = self.env.style.save_unit_style(unit, {"style": {"tone": "x"}, "dry_run": True})
        self.assertEqual((counted["ok"], counted["saved"], counted["would_stale"]), (True, False, 0))
        narrowed = self.env.style.save_unit_style(unit, {"style": {"tone": "x"}, "dry_run": True, "lang": "fr"})
        self.assertEqual(narrowed["would_stale"], 0)  # a lang is accepted and changes nothing

    def test_the_routes_serve_a_series_and_a_movie_and_answer_400_and_404(self):
        def put(path, body, query=None):
            return style_api.handle(self.env.style, "PUT", path, query or {}, body)

        self.assertTrue(style_api.handles(f"/api/series/{self.series}/characters"))
        status, answer = put(f"/api/series/{self.series}/characters", {"glossary": {"version": 1, "characters": [{"name": "Vi"}]}}, {"lang": ["vi"]})
        self.assertEqual((status, answer["saved"]), (200, True))
        self.assertEqual(put(f"/api/series/{self.series}/characters", {}, {"lang": ["vi"]})[0], 400)
        self.assertEqual(put("/api/series/999/characters", {"delete_card": "x"}, {"lang": ["vi"]})[0], 404)
        self.assertEqual(put("/api/movies/999/characters", {"delete_card": "x"}, {"lang": ["vi"]})[0], 404)


if __name__ == "__main__":
    unittest.main()
