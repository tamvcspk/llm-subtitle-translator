import json
import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import worker
from gst_worker import (
    backups as gst_backups,
    bazarr as gst_bazarr,
    config as gst_config,
    connection_tests as gst_connection_tests,
    gemini as gst_gemini,
    http as gst_http,
    logs as gst_logs,
    queue as gst_queue,
    subtitles as gst_subtitles,
    tmdb as gst_tmdb,
    translation as gst_translation,
)


class FakeHTTP:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params or {}, headers or {}))
        key = (url, tuple(sorted((params or {}).items())))
        return self.responses[key]

    def request_json(self, method, url, params=None, headers=None):
        self.calls.append((method, url, params or {}, headers or {}))
        return {"ok": True}




class TestWorkerIntegrationsTests(unittest.TestCase):
    def test_connection_tests_call_expected_api(self):
        http = FakeHTTP(
            {
                ("http://bazarr:6767/api/system/languages", ()): [],
                ("https://generativelanguage.googleapis.com/v1beta/models", (("key", "gemini-secret"),)): {"models": []},
                ("https://api.themoviedb.org/3/configuration", (("api_key", "tmdb-secret"),)): {"images": {}},
            }
        )
        settings = {
            "bazarr_url": "http://bazarr:6767",
            "bazarr_api_key": "bazarr-secret",
            "gemini_api_key": "gemini-secret",
            "gemini_api_key2": "gemini-secret",
            "tmdb_api_key": "tmdb-secret",
        }

        self.assertTrue(gst_connection_tests.test_connection("bazarr", settings, http)["ok"])
        self.assertTrue(gst_connection_tests.test_connection("gemini_api_key", settings, http)["ok"])
        self.assertTrue(gst_connection_tests.test_connection("tmdb_api_key", settings, http)["ok"])
        self.assertEqual(
            http.calls,
            [
                ("GET", "http://bazarr:6767/api/system/languages", {}, {"X-API-KEY": "bazarr-secret"}),
                ("GET", "https://generativelanguage.googleapis.com/v1beta/models", {"key": "gemini-secret"}, {}),
                ("GET", "https://api.themoviedb.org/3/configuration", {"api_key": "tmdb-secret"}, {}),
            ],
        )


    def test_gemini_models_returns_api_models_with_fallback(self):
        http = FakeHTTP(
            {
                (
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    (("key", "gemini-secret"),),
                ): {
                    "models": [
                        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
                    ]
                }
            }
        )

        models = gst_gemini.gemini_models(http, {"gemini_api_key": "gemini-secret"})

        self.assertEqual(models[0], {"id": "gemini-2.5-flash", "name": "gemini-2.5-flash"})
        self.assertNotIn("embedding-001", [item["id"] for item in models])
        self.assertIn(("GET", "https://generativelanguage.googleapis.com/v1beta/models", {"key": "gemini-secret"}, {}), http.calls)


    def test_clear_logs_truncates_worker_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "worker.log"
            log_path.write_text("line one\nline two\n", encoding="utf-8")

            result = gst_logs.clear_logs(tmp)

            self.assertTrue(result)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "")


    def test_console_handler_suppresses_routine_http_access_logs(self):
        handler = object.__new__(worker.ConsoleHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.requestline = "GET /api/status HTTP/1.1"

        with self.assertNoLogs(level="INFO"):
            handler.log_request(200)


    def test_console_handler_keeps_failed_http_access_logs(self):
        handler = object.__new__(worker.ConsoleHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.requestline = "POST /api/settings HTTP/1.1"

        with self.assertLogs(level="INFO") as captured:
            handler.log_request(400)

        self.assertEqual(captured.output, ['INFO:root:web 127.0.0.1 - "POST /api/settings HTTP/1.1" 400 -'])


    def test_console_handler_keeps_http_protocol_error_logs(self):
        handler = object.__new__(worker.ConsoleHandler)
        handler.client_address = ("127.0.0.1", 12345)

        with self.assertLogs(level="INFO") as captured:
            handler.log_error("code %d, message %s", 400, "Bad request syntax")

        self.assertEqual(captured.output, ["INFO:root:web 127.0.0.1 - code 400, message Bad request syntax"])


    def test_console_serves_module_scripts_as_javascript_without_caching(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "js").mkdir()
            (Path(tmp) / "js" / "main.js").write_text("export {};", encoding="utf-8")
            handler = object.__new__(worker.ConsoleHandler)
            handler.server = type("Server", (), {"ctx": {"static_dir": tmp}})()
            handler.wfile = io.BytesIO()
            headers: dict[str, str] = {}
            handler.send_response = lambda status: headers.setdefault("status", str(status))
            handler.send_header = lambda key, value: headers.__setitem__(key, value)
            handler.end_headers = lambda: None

            # Windows registries can map .js to text/plain; module scripts would then fail to load.
            with patch("worker.mimetypes.guess_type", return_value=("text/plain", None)):
                handler.serve_static("/js/main.js")

        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(handler.wfile.getvalue(), b"export {};")


    def test_scan_source_subtitles_finds_missing_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Show").mkdir()
            (root / "Show" / "Episode.ja.srt").write_text("hello", encoding="utf-8")
            (root / "Show" / "Episode.zh.srt").write_text("existing", encoding="utf-8")

            items = gst_subtitles.scan_source_subtitles(
                roots=[str(root)],
                source_languages=[
                    {"code": "en", "language": "English", "enabled": True},
                    {"code": "ja", "language": "Japanese", "enabled": True},
                ],
                target_languages=[
                    {"code": "zh", "language": "Simplified Chinese", "enabled": True},
                    {"code": "zt", "language": "Traditional Chinese", "enabled": True},
                ],
                limit=10,
            )

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["subtitle_path"], str(root / "Show" / "Episode.ja.srt"))
            self.assertEqual(items[0]["source_code"], "ja")
            self.assertEqual(items[0]["source_language"], "Japanese")
            self.assertEqual(items[0]["missing_targets"], [{"code": "zt", "language": "Traditional Chinese", "enabled": True}])


    def test_movie_description_prefers_tmdb_id_from_path(self):
        http = FakeHTTP({
            (
                "https://api.themoviedb.org/3/movie/350",
                (("api_key", "tmdb-key"), ("language", "en-US")),
            ): {
                "title": "The Devil Wears Prada",
                "release_date": "2006-06-30",
                "overview": "A smart graduate works for a fashion editor.",
                "genres": [{"name": "Comedy"}, {"name": "Drama"}],
            }
        })
        job = {
            "media_type": "movie",
            "video_path": "/media/Movie/The Devil Wears Prada (2006)/The Devil Wears Prada (2006) {tmdb-350}.mkv",
            "media_id": "781",
        }

        description = gst_tmdb.build_tmdb_description(
            job,
            bazarr=FakeHTTP(),
            tmdb=http,
            bazarr_url="http://bazarr:6767",
            bazarr_api_key="bazarr-key",
            tmdb_api_key="tmdb-key",
            cache=gst_http.MemoryCache(),
        )

        self.assertIn("Overview: A smart graduate works for a fashion editor.", description)
        self.assertIn("The Devil Wears Prada - 2006", description)
        self.assertIn("Genre(s): Comedy, Drama", description)


    def test_series_description_uses_bazarr_tvdb_mapping_and_episode(self):
        bazarr = FakeHTTP({
            (
                "http://bazarr:6767/api/series",
                (("seriesid[]", "257"),),
            ): {"data": [{"title": "NCIS: Sydney", "tvdbId": 416493, "imdbId": "tt18258908", "overview": "Bazarr overview"}]},
            (
                "http://bazarr:6767/api/episodes",
                (("episodeid[]", "14569"),),
            ): {"data": [{"season": 3, "episode": 16, "title": "Ticker"}]},
        })
        tmdb = FakeHTTP({
            (
                "https://api.themoviedb.org/3/find/416493",
                (("api_key", "tmdb-key"), ("external_source", "tvdb_id")),
            ): {"tv_results": [{"id": 222766}]},
            (
                "https://api.themoviedb.org/3/tv/222766",
                (("api_key", "tmdb-key"), ("language", "en-US")),
            ): {"name": "NCIS: Sydney", "overview": "Show overview"},
            (
                "https://api.themoviedb.org/3/tv/222766/season/3/episode/16",
                (("api_key", "tmdb-key"), ("language", "en-US")),
            ): {"name": "Ticker", "overview": "Episode overview"},
        })
        job = {"media_type": "series", "series_id": "257", "media_id": "14569"}

        description = gst_tmdb.build_tmdb_description(
            job,
            bazarr=bazarr,
            tmdb=tmdb,
            bazarr_url="http://bazarr:6767",
            bazarr_api_key="bazarr-key",
            tmdb_api_key="tmdb-key",
            cache=gst_http.MemoryCache(),
        )

        self.assertIn("Episode Overview: Episode overview", description)
        self.assertIn("NCIS: Sydney S03E16 - Ticker", description)
        self.assertIn("Show Overview: Show overview", description)


    def test_refresh_bazarr_uses_series_or_movie_scan_disk(self):
        http = FakeHTTP()
        gst_bazarr.refresh_bazarr(
            {"media_type": "series", "series_id": "257", "media_id": "14569"},
            http=http,
            bazarr_url="http://bazarr:6767",
            api_key="bazarr-key",
        )
        gst_bazarr.refresh_bazarr(
            {"media_type": "movie", "media_id": "781"},
            http=http,
            bazarr_url="http://bazarr:6767",
            api_key="bazarr-key",
        )

        self.assertEqual(
            http.calls,
            [
                ("PATCH", "http://bazarr:6767/api/series", {"seriesid": "257", "action": "scan-disk"}, {"X-API-KEY": "bazarr-key"}),
                ("PATCH", "http://bazarr:6767/api/movies", {"radarrid": "781", "action": "scan-disk"}, {"X-API-KEY": "bazarr-key"}),
            ],
        )


