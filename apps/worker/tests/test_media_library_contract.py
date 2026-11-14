"""Contract of the MediaLibrary port (SPEC criterion 10b): the same checks on the Bazarr adapter and on a fake."""

import unittest

from gst_worker.bazarr import BazarrMedia
from gst_worker.ports import EpisodeListing, MediaDetails, MediaLibrary


class InMemoryMediaLibrary:
    """Fake MediaLibrary: one series (id "7") with two episodes and one movie (id "5"). Bazarr lists "vi" as
    missing for episode 9 only; every rescan is recorded."""

    def __init__(self):
        self.refreshed = []

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

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/api/series"):
            return {"data": [{"title": "Show", "tvdbId": 70}]}
        if url.endswith("/api/movies"):
            return {"data": [{"title": "Heat"}]}
        if params and "episodeid[]" in params:
            return {"data": [{"season": 1, "episode": 2, "title": "Two",
                              "missing_subtitles": [{"code2": "vi"}]}]}
        return {"data": [
            {"season": 1, "episode": 1, "title": "One", "sonarrEpisodeId": 11, "path": "/m/S01E01.mkv",
             "subtitles": [{"code2": "en", "path": "/m/S01E01.en.srt"}]},
            {"season": 1, "episode": 2, "title": "Two", "sonarrEpisodeId": 12, "path": "/m/S01E02.mkv", "subtitles": []},
        ]}

    def request_json(self, method, url, params=None, headers=None):
        self.sent.append((method, url, dict(params or {})))
        return {}


class MediaLibraryContract:
    repo: MediaLibrary

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.repo, MediaLibrary)

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
