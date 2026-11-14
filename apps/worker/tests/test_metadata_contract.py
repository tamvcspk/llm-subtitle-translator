"""Contract of the Metadata port (SPEC criterion 10b): the same checks on the TMDB adapter and on a fake."""

import unittest

from gst_worker.http import MemoryCache
from gst_worker.ports import Metadata
from gst_worker.tmdb import TmdbMetadata

MOVIE_JOB = {"media_type": "movie", "video_path": "/media/The.Matrix.(1999).{tmdb-603}.mkv", "subtitle_path": "x"}
MOVIE = {"title": "The Matrix", "release_date": "1999-03-31", "genres": [{"name": "Sci-Fi"}], "overview": "A hacker."}
EXPECTED = "Overview: A hacker.\n\nThe Matrix - 1999\nGenre(s): Sci-Fi"


class InMemoryMetadata:
    """Fake Metadata: knows one movie by its TMDB id and nothing else."""

    def description(self, job):
        if job.get("media_type") == "movie" and "{tmdb-603}" in job.get("video_path", ""):
            return EXPECTED
        return ""


class FakeTmdbHttp:
    """Answers the TMDB movie endpoint and counts the calls it gets."""

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def get_json(self, url, params=None, headers=None):
        self.calls += 1
        if self.fail:
            raise OSError("tmdb unreachable")
        if url.endswith("/movie/603"):
            return MOVIE
        return {}


class MetadataContract:
    repo: Metadata

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.repo, Metadata)

    def test_a_known_movie_gets_its_title_year_genres_and_overview(self):
        self.assertEqual(self.repo.description(MOVIE_JOB), EXPECTED)

    def test_an_item_it_cannot_match_gets_an_empty_text(self):
        self.assertEqual(self.repo.description({"media_type": "movie", "video_path": "/media/Unknown.mkv"}), "")


class InMemoryMetadataTests(MetadataContract, unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryMetadata()


class TmdbMetadataTests(MetadataContract, unittest.TestCase):
    def setUp(self):
        self.http = FakeTmdbHttp()
        self.settings = {"bazarr_url": "", "bazarr_api_key": "", "tmdb_api_key": "tmdb-key"}
        self.repo = TmdbMetadata(self.http, MemoryCache(), lambda: self.settings)

    def test_without_a_tmdb_key_it_does_not_ask_and_returns_empty(self):
        self.settings["tmdb_api_key"] = ""
        self.assertEqual(self.repo.description(MOVIE_JOB), "")
        self.assertEqual(self.http.calls, 0)

    def test_a_failed_lookup_is_empty_text_not_an_error(self):
        repo = TmdbMetadata(FakeTmdbHttp(fail=True), MemoryCache(), lambda: self.settings)
        self.assertEqual(repo.description(MOVIE_JOB), "")

    def test_a_key_saved_later_takes_effect_without_a_new_adapter(self):
        self.settings["tmdb_api_key"] = ""
        self.assertEqual(self.repo.description(MOVIE_JOB), "")
        self.settings["tmdb_api_key"] = "tmdb-key"
        self.assertEqual(self.repo.description(MOVIE_JOB), EXPECTED)

    def test_a_repeated_lookup_is_served_from_the_cache(self):
        self.repo.description(MOVIE_JOB)
        calls = self.http.calls
        self.assertEqual(self.repo.description(MOVIE_JOB), EXPECTED)
        self.assertEqual(self.http.calls, calls)


if __name__ == "__main__":
    unittest.main()
