"""TMDB cast through the Metadata port: the main cast with episode counts and the episode's guests, names only (P19b, FR-5.9.4)."""

import unittest

from subserver.adapters.http import MemoryCache
from subserver.adapters.tmdb import TmdbMetadata
from subserver.domain.ports import Cast

JOB = {"media_type": "series", "series_id": "7", "media_id": "101", "subtitle_path": "x"}
CREDITS = {"cast": [
    {"name": "Jane K", "total_episode_count": 151, "roles": [{"character": "Lois", "episode_count": 151}]},
    {"name": "Frankie M", "total_episode_count": 151, "roles": [{"character": "Malcolm", "episode_count": 151}]},
    {"name": "Voice Actor", "total_episode_count": 3, "roles": [{"character": "Mom (voice)", "episode_count": 3}, {"character": "Self", "episode_count": 1}]},
    {"name": "Cameo", "total_episode_count": 1, "roles": [{"character": "Himself", "episode_count": 1}]},
    {"name": "Two Roles", "total_episode_count": 20, "roles": [{"character": "Ida / Sir", "episode_count": 20}]},
]}
EPISODE = {"guest_stars": [{"name": "A", "character": "Caroline Miller"}, {"name": "B", "character": "Mr. Herkabe (uncredited)"}, {"name": "C", "character": "Self"}]}


class FakeHttp:
    def __init__(self, fail=False, series=None):
        self.calls = []
        self.fail = fail
        self.series = {"tvdbId": "99"} if series is None else series

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        if self.fail:
            raise OSError("unreachable")
        if url.endswith("/api/series"):
            return {"data": [self.series]}
        if url.endswith("/api/episodes"):
            return {"data": [{"season": 2, "episode": 4}]}
        if "/find/" in url:
            return {"tv_results": [{"id": 55}]}
        if url.endswith("/tv/55/aggregate_credits"):
            return CREDITS
        if url.endswith("/tv/55/season/2/episode/4"):
            return EPISODE
        return {}


class TmdbCastTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"bazarr_url": "http://bazarr", "bazarr_api_key": "k", "tmdb_api_key": "tmdb-key"}
        self.http = FakeHttp()
        self.repo = TmdbMetadata(self.http, MemoryCache(), lambda: self.settings)

    def test_main_cast_is_ranked_by_episodes_and_names_are_plain(self):
        cast = self.repo.cast(JOB)
        names = [n for n, _ in cast.main]
        self.assertEqual(names[:2], ["Lois", "Malcolm"])  # the most episodes first, the listing order on a tie
        self.assertEqual(dict(cast.main)["Lois"], 151)
        self.assertIn("Ida", names)
        self.assertNotIn("Sir", names)  # a form of address is no character
        for gone in ("Mom", "Mom (voice)", "Self", "Himself"):
            self.assertNotIn(gone, names)

    def test_guests_of_the_episode_without_suffixes_or_self(self):
        self.assertEqual(self.repo.cast(JOB).guests, ("Caroline Miller", "Mr. Herkabe"))

    def test_without_a_key_nothing_is_asked(self):
        self.settings["tmdb_api_key"] = ""
        self.assertEqual(self.repo.cast(JOB), Cast())
        self.assertEqual(self.http.calls, [])

    def test_a_series_tmdb_does_not_know_has_no_cast(self):
        repo = TmdbMetadata(FakeHttp(series={}), MemoryCache(), lambda: self.settings)
        self.assertEqual(repo.cast(JOB), Cast())

    def test_a_failed_lookup_is_an_empty_cast_not_an_error(self):
        repo = TmdbMetadata(FakeHttp(fail=True), MemoryCache(), lambda: self.settings)
        with self.assertLogs(level="ERROR"):
            self.assertEqual(repo.cast(JOB), Cast())

    def test_one_job_asks_bazarr_once_for_the_series_and_the_episode(self):  # R19-9
        job = {**JOB, "job_id": "j1"}
        self.repo.description(job)
        self.repo.cast(job)
        self.repo.genres(job)
        self.assertEqual(self.http.calls.count("http://bazarr/api/series"), 1)
        self.assertEqual(self.http.calls.count("http://bazarr/api/episodes"), 1)
        self.repo.cast({**job, "job_id": "j2"})  # the next job reads Bazarr again: what it says can have changed
        self.assertEqual(self.http.calls.count("http://bazarr/api/series"), 2)

    def test_a_job_without_an_id_is_not_remembered(self):
        self.repo.cast(JOB)
        self.repo.cast(JOB)
        self.assertEqual(self.http.calls.count("http://bazarr/api/series"), 2)

    def test_a_movie_has_no_cast(self):
        self.assertEqual(self.repo.cast({"media_type": "movie", "video_path": "/m/{tmdb-1}.mkv"}), Cast())
        self.assertEqual(self.http.calls, [])


if __name__ == "__main__":
    unittest.main()
