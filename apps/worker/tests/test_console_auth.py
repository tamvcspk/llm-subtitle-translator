import base64
import http.client
import logging
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import worker
from gst_worker.auth import ALLOW, UNAUTHORIZED, UNCONFIGURED, ConsoleAuth

USER = "admin"
PASSWORD = "s3cret-pässword:with:colons"


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


class ConsoleAuthPolicyTests(unittest.TestCase):
    def test_from_env_requires_both_credentials(self):
        self.assertEqual(ConsoleAuth.from_env({}).check(None), UNCONFIGURED)
        self.assertEqual(ConsoleAuth.from_env({"CONSOLE_USER": "a"}).check(None), UNCONFIGURED)
        self.assertEqual(ConsoleAuth.from_env({"CONSOLE_PASSWORD": "b"}).check(None), UNCONFIGURED)
        self.assertEqual(ConsoleAuth.from_env({"CONSOLE_USER": "", "CONSOLE_PASSWORD": ""}).check(None), UNCONFIGURED)

    def test_auth_off_allows_everything_even_without_credentials(self):
        auth = ConsoleAuth.from_env({"CONSOLE_AUTH": "off"})
        self.assertEqual(auth.check(None), ALLOW)
        self.assertEqual(auth.check("garbage"), ALLOW)
        self.assertEqual(ConsoleAuth.from_env({"CONSOLE_AUTH": " OFF "}).check(None), ALLOW)

    def test_other_auth_values_do_not_disable_auth(self):
        auth = ConsoleAuth.from_env({"CONSOLE_AUTH": "false", "CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD})
        self.assertEqual(auth.check(None), UNAUTHORIZED)

    def test_check_accepts_only_exact_credentials(self):
        auth = ConsoleAuth.from_env({"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD})
        self.assertEqual(auth.check(basic(USER, PASSWORD)), ALLOW)
        self.assertEqual(auth.check(basic(USER, "wrong")), UNAUTHORIZED)
        self.assertEqual(auth.check(basic("other", PASSWORD)), UNAUTHORIZED)
        self.assertEqual(auth.check(basic(USER, PASSWORD + "x")), UNAUTHORIZED)

    def test_check_rejects_malformed_headers(self):
        auth = ConsoleAuth.from_env({"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD})
        for header in [None, "", "Basic", "Basic ", "Bearer abc", "Basic !!!not-base64!!!", "Basic " + base64.b64encode(b"nocolon").decode()]:
            with self.subTest(header=header):
                self.assertEqual(auth.check(header), UNAUTHORIZED)

    def test_a_refusal_is_explained_without_revealing_the_credentials(self):
        from gst_worker.auth import explain_failure

        auth = ConsoleAuth.from_env({"CONSOLE_USER": "trial", "CONSOLE_PASSWORD": "abcDEF123"})
        self.assertIn("no credentials were sent", explain_failure(auth, None))
        self.assertIn("wrong password", explain_failure(auth, basic("trial", "other")))
        # Phone keyboards capitalise the first letter of a field; say so, because it is the usual cause.
        self.assertIn("capitals", explain_failure(auth, basic("Trial", "abcDEF123")))
        self.assertIn("capitals", explain_failure(auth, basic("trial", "AbcDEF123")))
        self.assertIn("wrong user name", explain_failure(auth, basic("someone", "abcDEF123")))
        self.assertIn("could not be read", explain_failure(auth, "Basic !!!"))
        self.assertIn("unsupported", explain_failure(auth, "Bearer abc"))
        for header in (None, basic("trial", "other"), basic("Trial", "abcDEF123"), "Bearer abc"):
            self.assertNotIn("abcDEF123", explain_failure(auth, header))

    def test_repr_hides_secrets(self):
        text = repr(ConsoleAuth.from_env({"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD}))
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(USER, text)


class ConsoleAuthServerTests(unittest.TestCase):
    """Drive the real ConsoleHandler over a socket so every route is covered."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        static = Path(self.tmp.name)
        (static / "index.html").write_text("<html>console</html>", encoding="utf-8")
        self.ctx = {
            "static_dir": str(static),
            "queue_dir": str(Path(self.tmp.name) / "queue"),
            "log_dir": str(Path(self.tmp.name) / "logs"),
        }

    def serve(self, auth):
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.ConsoleHandler)
        server.ctx = self.ctx
        if auth is not None:
            server.auth = auth
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def request(self, port, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            conn.close()

    def configured(self):
        return ConsoleAuth.from_env({"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD})

    def test_missing_header_is_401_with_challenge_on_static_and_api(self):
        port = self.serve(self.configured())
        for method, path in [("GET", "/"), ("GET", "/index.html"), ("GET", "/js/main.js"), ("GET", "/api/status"),
                             ("GET", "/api/backups/download?name=x"), ("POST", "/api/settings"), ("POST", "/api/backups/import"),
                             ("PUT", "/api/settings"), ("DELETE", "/api/queue"), ("OPTIONS", "/"), ("HEAD", "/")]:
            with self.subTest(method=method, path=path):
                status, headers, body = self.request(port, method, path, body=b"{}" if method in {"POST", "PUT"} else None)
                self.assertEqual(status, 401)
                self.assertTrue(headers["WWW-Authenticate"].startswith("Basic realm="))
                self.assertNotIn("console", body)

    def test_wrong_credentials_are_401(self):
        port = self.serve(self.configured())
        status, headers, _ = self.request(port, "GET", "/api/queue", {"Authorization": basic(USER, "nope")})
        self.assertEqual(status, 401)
        self.assertIn("WWW-Authenticate", headers)

    def test_correct_credentials_reach_static_content(self):
        port = self.serve(self.configured())
        status, _, body = self.request(port, "GET", "/", {"Authorization": basic(USER, PASSWORD)})
        self.assertEqual(status, 200)
        self.assertIn("console", body)

    def test_correct_credentials_reach_api_routes(self):
        port = self.serve(self.configured())
        status, _, body = self.request(port, "POST", "/api/nope", {"Authorization": basic(USER, PASSWORD)}, body=b"{}")
        self.assertEqual(status, 404)
        self.assertIn("not found", body)

    def test_unconfigured_credentials_answer_503_with_instructions(self):
        port = self.serve(ConsoleAuth.from_env({}))
        for headers in [{}, {"Authorization": basic(USER, PASSWORD)}]:
            with self.subTest(headers=headers):
                status, response_headers, body = self.request(port, "GET", "/api/status", headers)
                self.assertEqual(status, 503)
                self.assertNotIn("WWW-Authenticate", response_headers)
                self.assertIn("CONSOLE_USER", body)
                self.assertIn("CONSOLE_AUTH=off", body)

    def test_auth_off_serves_without_credentials(self):
        port = self.serve(ConsoleAuth.from_env({"CONSOLE_AUTH": "off"}))
        status, _, body = self.request(port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("console", body)

    def test_server_without_auth_handle_fails_closed(self):
        port = self.serve(None)
        status, _, _ = self.request(port, "GET", "/")
        self.assertEqual(status, 503)

    def test_logs_never_contain_password_or_authorization_header(self):
        port = self.serve(self.configured())
        with self.assertLogs(level="INFO") as captured:
            self.request(port, "GET", "/api/status", {"Authorization": basic(USER, "wrong-password-xyz")})
            self.request(port, "GET", "/api/status")
            logging.info("sentinel")  # assertLogs fails on an empty capture
        text = "\n".join(captured.output)
        self.assertNotIn("wrong-password-xyz", text)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(base64.b64encode(f"{USER}:wrong-password-xyz".encode()).decode(), text)
        self.assertNotIn("Authorization", text)

    def test_startup_logs_warn_without_leaking_credentials(self):
        with self.assertLogs(level="WARNING") as captured:
            ConsoleAuth.from_env({"CONSOLE_AUTH": "off", "CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD}).log_startup()
        self.assertIn("CONSOLE_AUTH=off", captured.output[0])
        with self.assertLogs(level="ERROR") as captured:
            ConsoleAuth.from_env({"CONSOLE_USER": USER}).log_startup()
        self.assertNotIn(USER, "\n".join(captured.output))
        with self.assertNoLogs(level="WARNING"):
            self.configured().log_startup()


class ConsoleAuthStartupTests(unittest.TestCase):
    def run_main(self, extra_env, probe=None):
        """Run main() with the web server stubbed; ``probe(ctx, auth)`` runs while the store is still open."""
        results = []

        def fake_server(ctx, host, port, auth):
            results.append(probe(ctx, auth) if probe else None)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "STATE_DIR": str(root / "state"),
                "QUEUE_DIR": str(root / "queue"),
                "APP_CONFIG_PATH": str(root / "state" / "config.json"),
                "POSTPROCESS_TARGETS_PATH": str(root / "targets.json"),
                "TMDB_CACHE_PATH": str(root / "cache.json"),
                **extra_env,
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["worker.py", "--no-worker"]),
                patch.object(worker, "configure_logging"),
                patch.object(worker.threading, "Thread"),
                patch.object(worker, "start_web_server", side_effect=fake_server),
            ):
                self.assertEqual(worker.main(), 0)
        return results[0]

    def test_main_passes_env_auth_to_server_and_keeps_it_out_of_context(self):
        result = self.run_main(
            {"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD},
            lambda ctx, auth: (auth.check(basic(USER, PASSWORD)), PASSWORD in repr(ctx)),
        )
        self.assertEqual(result, (ALLOW, False))

    def test_main_without_credentials_still_starts_in_unconfigured_mode(self):
        self.assertEqual(self.run_main({}, lambda ctx, auth: auth.check(None)), UNCONFIGURED)

    def test_main_auth_off(self):
        self.assertEqual(self.run_main({"CONSOLE_AUTH": "off"}, lambda ctx, auth: auth.check(None)), ALLOW)


if __name__ == "__main__":
    unittest.main()
