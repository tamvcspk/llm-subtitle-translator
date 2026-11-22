import http.client
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
from tests.web_harness import serve_console
from subserver.services.auth import ConsoleAuth
from subserver.adapters import backups as gst_backups
from subserver.adapters import bazarr as gst_bazarr
from subserver.domain import config as gst_config
from subserver.adapters import connection_tests as gst_connection_tests
from subserver.adapters import gemini as gst_gemini
from subserver.adapters import http as gst_http
from subserver.adapters import logs as gst_logs
from subserver.jobs import queue as gst_queue
from subserver.domain import subtitles as gst_subtitles
from subserver.adapters import tmdb as gst_tmdb
from subserver.domain import translation as gst_translation


class FakeHTTP:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params or {}, headers or {}))
        key = (url, tuple(sorted((params or {}).items())))
        return self.responses[key]

    def request_json(self, method, url, params=None, headers=None, timeout=None):
        self.calls.append((method, url, params or {}, headers or {}))
        return {"ok": True}




class TestWorkerIntegrationsTests(unittest.TestCase):
    def test_connection_errors_are_sentences_not_python_reprs(self):
        import socket
        import urllib.error

        lookup = urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))
        message = gst_http.describe_http_error(lookup, "Bazarr")
        self.assertIn("Cannot find Bazarr", message)
        self.assertNotIn("urlopen", message)
        self.assertNotIn("Errno", message)
        self.assertIn("refused", gst_http.describe_http_error(urllib.error.URLError(ConnectionRefusedError()), "Bazarr"))
        self.assertIn("in time", gst_http.describe_http_error(urllib.error.URLError(socket.timeout()), "Bazarr"))
        denied = urllib.error.HTTPError("http://bazarr/api?key=SECRET", 401, "Unauthorized", {}, None)
        text = gst_http.describe_http_error(denied, "Bazarr")
        self.assertIn("rejected the API key", text)
        self.assertNotIn("SECRET", text)  # a URL that carries a key never reaches the console
        self.assertIn("not valid", gst_http.describe_http_error(ValueError("unknown url type"), "Bazarr"))

    def test_a_failing_connection_test_reports_the_friendly_message(self):
        import socket
        import urllib.error

        class Down:
            def get_json(self, *args, **kwargs):
                raise urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))

        result = gst_connection_tests.test_connection(
            "bazarr", {"bazarr_url": "http://bazarr:6767", "bazarr_api_key": "k"}, Down()
        )
        self.assertFalse(result["ok"])
        self.assertIn("Cannot find Bazarr", result["message"])

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


    def serve_static_folder(self, tmp):
        (Path(tmp) / "index.html").write_text("<html>console</html>", encoding="utf-8")
        (Path(tmp) / "js").mkdir()
        (Path(tmp) / "js" / "main.js").write_text("export {};", encoding="utf-8")
        auth = ConsoleAuth.from_env({"CONSOLE_AUTH": "off"})
        return serve_console(self, {"static_dir": tmp}, auth).port

    def get(self, port, path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()


    def test_the_console_logs_a_failed_request_and_not_a_routine_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = self.serve_static_folder(tmp)
            with self.assertNoLogs(level="INFO"):
                self.assertEqual(self.get(port, "/")[0], 200)
            with self.assertLogs(level="INFO") as captured:
                self.assertEqual(self.get(port, "/api/nope")[0], 404)
        self.assertEqual(captured.output, ['INFO:root:web 127.0.0.1 - "GET /api/nope" 404'])


    def test_console_serves_module_scripts_as_javascript_without_caching(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = self.serve_static_folder(tmp)
            # Windows registries can map .js to text/plain; module scripts would then fail to load.
            with patch("subserver.api.web.mimetypes.guess_type", return_value=("text/plain", None)):
                status, headers, body = self.get(port, "/js/main.js")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(body, b"export {};")


    def test_a_path_that_is_not_a_file_is_the_consoles_entry_page_but_never_an_api_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = self.serve_static_folder(tmp)
            status, _, body = self.get(port, "/library/series/3")  # a client-side route
            self.assertEqual((status, b"console" in body), (200, True))
            status, _, body = self.get(port, "/api/nothing-here")
            self.assertEqual((status, json.loads(body)), (404, {"error": "not found"}))


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


