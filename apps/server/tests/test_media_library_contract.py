"""Contract of the MediaLibrary port (SPEC criterion 10b): the same checks on the Bazarr adapter and on a fake."""

import unittest
from unittest.mock import patch

from subserver.bazarr import BazarrMedia
from subserver.ports import CatalogEntry, CatalogSubtitle, EpisodeListing, MediaDetails, MediaLibrary, MediaLibraryError


class InMemoryMediaLibrary:
    """Fake MediaLibrary: one series (id "7") with two episodes and one movie (id "5"). Bazarr lists "vi" as
    missing for episode 9 only; every rescan is recorded."""

    def __init__(self):
        self.refreshed = []

    def catalog(self):
        yield CatalogEntry(kind="series", bazarr_id="7", title="Show", tvdb_id="70")
        yield CatalogEntry(
            kind="episode", bazarr_id="11", parent_bazarr_id="7", tvdb_id="70", season=1, episode=1, title="One",
            video_path="/m/S01E01.mkv", subtitles=(CatalogSubtitle("en", "/m/S01E01.en.srt"),),
        )
        yield CatalogEntry(
            kind="episode", bazarr_id="12", parent_bazarr_id="7", tvdb_id="70", season=1, episode=2, title="Two",
            video_path="/m/S01E02.mkv", missing=("vi",),
        )
        yield CatalogEntry(kind="movie", bazarr_id="5", title="Heat", video_path="/m/Heat.mkv", missing=("vi",))

    def describe(self, media_type, series_id, media_id):
        if media_type == "series" and series_id == "7" and media_id == "9":
            return MediaDetails(series_title="Show", tvdb_id="70", season=1, episode=2, episode_title="Two")
        if media_type == "movie" and media_id == "5":
            return MediaDetails(movie_title="Heat")
        return None

    def series_episodes(self, series_id, source_code):
        if series_id != "7":
            return []
        return [
            EpisodeListing(1, 1, "One", "11", "/m/S01E01.mkv", "/m/S01E01.en.srt" if source_code == "en" else ""),
            EpisodeListing(1, 2, "Two", "12", "/m/S01E02.mkv", ""),
        ]

    def target_still_missing(self, job):
        if job.get("media_id") != "9" or job.get("media_type") != "series":
            return None
        return job.get("target_code") == "vi"

    def refresh(self, job):
        self.refreshed.append(job)


class FakeBazarrHttp:
    """Answers the Bazarr endpoints the adapter reads, and records the requests it is sent."""

    def __init__(self):
        self.sent = []
        self.timeouts = []
        self.requests = []

    def get_json(self, url, params=None, headers=None):
        self.requests.append((url, dict(params or {})))
        if url.endswith("/api/series"):
            return {"data": [{"title": "Show", "tvdbId": 70, "sonarrSeriesId": 7}], "total": 1}
        if url.endswith("/api/movies"):
            return {"data": [{"title": "Heat", "radarrId": 5, "path": "/m/Heat.mkv",
                              "missing_subtitles": [{"code2": "vi"}, {"code2": "fr", "forced": True}]}], "total": 1}
        if params and "episodeid[]" in params:
            return {"data": [{"season": 1, "episode": 2, "title": "Two",
                              "missing_subtitles": [{"code2": "vi"}]}]}
        return {"data": [
            {"season": 1, "episode": 1, "title": "One", "sonarrEpisodeId": 11, "path": "/m/S01E01.mkv",
             "subtitles": [{"code2": "en", "path": "/m/S01E01.en.srt"}, {"code2": "fr", "path": None}]},
            {"season": 1, "episode": 2, "title": "Two", "sonarrEpisodeId": 12, "path": "/m/S01E02.mkv", "subtitles": [],
             "missing_subtitles": [{"code2": "vi"}]},
            {"season": None, "episode": None, "title": "Not an episode", "sonarrEpisodeId": 13},
        ]}

    def request_json(self, method, url, params=None, headers=None, timeout=None):
        self.sent.append((method, url, dict(params or {})))
        self.timeouts.append(timeout)
        return {}


class MediaLibraryContract:
    repo: MediaLibrary

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.repo, MediaLibrary)

    def test_the_catalog_lists_series_episodes_and_movies_with_their_subtitles_and_missing_languages(self):
        entries = list(self.repo.catalog())
        self.assertTrue(all(isinstance(entry, CatalogEntry) for entry in entries))
        self.assertEqual([(e.kind, e.bazarr_id) for e in entries],
                         [("series", "7"), ("episode", "11"), ("episode", "12"), ("movie", "5")])
        series, one, two, movie = entries
        self.assertEqual((series.title, series.tvdb_id), ("Show", "70"))
        self.assertEqual((one.parent_bazarr_id, one.season, one.episode, one.video_path), ("7", 1, 1, "/m/S01E01.mkv"))
        self.assertEqual((one.subtitles[0].code, one.subtitles[0].path), ("en", "/m/S01E01.en.srt"))
        self.assertEqual(two.missing, ("vi",))
        self.assertEqual((movie.title, movie.missing), ("Heat", ("vi",)))  # a forced-only language is not "missing"

    def test_an_episode_is_described_by_its_series_and_numbers(self):
        got = self.repo.describe("series", "7", "9")
        self.assertIsInstance(got, MediaDetails)
        self.assertEqual((got.series_title, got.tvdb_id, got.season, got.episode, got.episode_title),
                         ("Show", "70", 1, 2, "Two"))

    def test_a_movie_is_described_by_its_title(self):
        self.assertEqual(self.repo.describe("movie", "", "5").movie_title, "Heat")

    def test_what_it_cannot_describe_is_none_not_an_error(self):
        self.assertIsNone(self.repo.describe("series", "", "9"))  # no series id
        self.assertIsNone(self.repo.describe("series", "7", ""))  # no item id
        self.assertIsNone(self.repo.describe("", "7", "9"))  # unknown kind

    def test_episodes_come_with_their_source_subtitle_in_the_asked_language(self):
        got = self.repo.series_episodes("7", "en")
        self.assertTrue(all(isinstance(e, EpisodeListing) for e in got))
        self.assertEqual([(e.season, e.episode, e.bazarr_id, e.source_path) for e in got],
                         [(1, 1, "11", "/m/S01E01.en.srt"), (1, 2, "12", "")])
        self.assertEqual(self.repo.series_episodes("7", "fr")[0].source_path, "")

    def test_no_series_id_means_no_episodes(self):
        self.assertEqual(self.repo.series_episodes("", "en"), [])

    def test_the_library_says_whether_the_target_language_is_still_missing(self):
        self.assertIs(self.repo.target_still_missing({"media_type": "series", "media_id": "9", "target_code": "vi"}), True)
        self.assertIs(self.repo.target_still_missing({"media_type": "series", "media_id": "9", "target_code": "fr"}), False)

    def test_a_job_it_cannot_identify_gets_no_answer(self):
        self.assertIsNone(self.repo.target_still_missing({"media_id": "", "target_code": "vi"}))

    def test_refresh_asks_for_a_rescan_of_the_item(self):
        # Must not raise for a job the library knows; the file is already on disk either way.
        self.repo.refresh({"media_type": "series", "series_id": "7", "media_id": "9"})


class BazarrMediaTests(MediaLibraryContract, unittest.TestCase):
    def setUp(self):
        self.http = FakeBazarrHttp()
        self.repo = BazarrMedia(self.http, lambda: {"bazarr_url": "http://bazarr/", "bazarr_api_key": "key"})

    def test_without_an_api_key_it_cannot_say_anything(self):
        media = BazarrMedia(FakeBazarrHttp(), lambda: {"bazarr_url": "http://bazarr", "bazarr_api_key": ""})
        self.assertIsNone(media.describe("series", "7", "9"))
        self.assertEqual(media.series_episodes("7", "en"), [])
        self.assertIsNone(media.target_still_missing({"media_type": "series", "media_id": "9", "target_code": "vi"}))

    def test_a_key_saved_later_takes_effect_without_a_new_adapter(self):
        settings = {"bazarr_url": "http://bazarr", "bazarr_api_key": ""}
        media = BazarrMedia(FakeBazarrHttp(), lambda: settings)
        self.assertEqual(media.series_episodes("7", "en"), [])
        settings["bazarr_api_key"] = "key"
        self.assertEqual(len(media.series_episodes("7", "en")), 2)

    def test_an_unreachable_bazarr_raises_so_the_caller_can_decide(self):
        class Down:
            def get_json(self, *args, **kwargs):
                raise OSError("connection refused")

            def request_json(self, *args, **kwargs):
                raise OSError("connection refused")

        media = BazarrMedia(Down(), lambda: {"bazarr_url": "http://bazarr", "bazarr_api_key": "key"})
        with self.assertRaises(OSError):
            media.describe("series", "7", "9")
        with self.assertRaises(OSError):
            media.series_episodes("7", "en")
        with self.assertRaises(OSError):
            media.refresh({"media_type": "series", "series_id": "7", "media_id": "9"})

    def test_a_refresh_of_a_series_episode_scans_that_series_on_disk(self):
        self.repo.refresh({"media_type": "series", "series_id": "7", "media_id": "9"})
        self.assertEqual(self.http.sent, [("PATCH", "http://bazarr/api/series", {"seriesid": "7", "action": "scan-disk"})])

    def test_the_catalog_is_read_in_pages_of_series_then_each_series_episodes_then_movies(self):
        list(self.repo.catalog())
        asked = [(url.rsplit("/api/", 1)[1], params) for url, params in self.http.requests]
        self.assertEqual(asked, [
            ("series", {"start": 0, "length": 100}), ("episodes", {"seriesid[]": "7"}), ("movies", {"start": 0, "length": 100}),
        ])

    def test_a_page_that_is_full_asks_for_the_next_one_and_stops_at_the_total(self):
        class Paged(FakeBazarrHttp):
            def get_json(self, url, params=None, headers=None):
                if url.endswith("/api/series") and params and "start" in params:
                    start = params["start"]
                    items = [{"title": f"S{n}", "sonarrSeriesId": n} for n in range(start + 1, min(start + 2, 3) + 1)]
                    return {"data": items, "total": 3}
                if url.endswith("/api/episodes"):
                    return {"data": []}
                return {"data": [], "total": 0}

        with patch("subserver.bazarr.CATALOG_PAGE_SIZE", 2):
            media = BazarrMedia(Paged(), lambda: {"bazarr_url": "http://bazarr", "bazarr_api_key": "key"})
            titles = [e.title for e in media.catalog() if e.kind == "series"]
        self.assertEqual(titles, ["S1", "S2", "S3"])

    def test_a_catalog_that_cannot_be_read_raises_a_sentence_that_has_no_address_and_no_key(self):
        class Down:
            def get_json(self, *args, **kwargs):
                raise OSError("connection refused to http://bazarr:6767/api/series?apikey=SECRET")

        media = BazarrMedia(Down(), lambda: {"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "SECRET"})
        with self.assertRaises(MediaLibraryError) as caught:
            list(media.catalog())
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn("6767", str(caught.exception))

    def test_an_answer_that_is_not_a_list_is_a_failure_not_an_empty_library(self):
        class Odd(FakeBazarrHttp):
            def get_json(self, url, params=None, headers=None):
                return {"data": "nothing"}

        media = BazarrMedia(Odd(), lambda: {"bazarr_url": "http://bazarr", "bazarr_api_key": "key"})
        with self.assertRaises(MediaLibraryError):
            list(media.catalog())

    def test_without_an_address_or_a_key_the_catalog_says_so(self):
        media = BazarrMedia(FakeBazarrHttp(), lambda: {"bazarr_url": "http://bazarr", "bazarr_api_key": ""})
        with self.assertRaises(MediaLibraryError):
            list(media.catalog())

    def test_a_rescan_waits_longer_than_the_default_timeout(self):
        # Bazarr answers scan-disk only after scanning the whole series (31 s for 150 episodes on the first trial).
        self.repo.refresh({"media_type": "series", "series_id": "7", "media_id": "9"})
        self.assertEqual(self.http.timeouts, [180])

    def test_an_unreachable_target_check_raises_nothing_and_says_nothing(self):
        class Down:
            def get_json(self, *args, **kwargs):
                raise OSError("connection refused")

        media = BazarrMedia(Down(), lambda: {"bazarr_url": "http://bazarr", "bazarr_api_key": "key"})
        self.assertIsNone(media.target_still_missing({"media_type": "series", "media_id": "9", "target_code": "vi"}))


class InMemoryMediaLibraryTests(MediaLibraryContract, unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryMediaLibrary()


if __name__ == "__main__":
    unittest.main()
