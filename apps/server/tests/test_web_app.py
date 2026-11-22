"""P15d (ADR-18): the console is a FastAPI app. What these tests hold on to: the routes are the app's own and match
docs/API.md both ways; every error keeps the shape ``apps/web`` reads; the loops start in the lifespan; the handlers run on
a thread pool over one ``Store``; and nothing secret reaches a log. The gate itself is tested in ``test_console_auth`` and
``test_console_login`` (SPEC criteria 9 and 24c).
"""
from __future__ import annotations

import base64
import http.client
import importlib.util
import json
import logging
import re
import tempfile
import threading
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from subserver.adapters.settings import SettingsService
from subserver.adapters.store import Store
from subserver.api.web import create_app
from subserver.jobs.queue import JobQueue
from subserver.runtime import BackgroundLoop, start_loops
from subserver.services.auth import ConsoleAuth
from subserver.services.console import ConsoleActions
from tests.web_harness import serve_console

REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location("doccheck", REPO / "scripts" / "doccheck.py")
doccheck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(doccheck)
OFF = ConsoleAuth.from_env({"CONSOLE_AUTH": "off"})


def normalize(path: str) -> str:
    """``/api/units/{p1:int}/cues`` and ``/api/units/{id}/cues?lang=`` are the same route."""
    return re.sub(r"\{[^{}]*\}", "{}", path.split("?", 1)[0]).rstrip("/")


def app_routes(app) -> set[tuple[str, str]]:
    return {
        (method, normalize(route.path)) for route in app.routes if route.path.startswith("/api/")
        for method in (route.methods or set()) - {"HEAD"}
    }


def documented_routes() -> set[tuple[str, str]]:
    text = (REPO / "docs" / "API.md").read_text(encoding="utf-8")
    found = set()
    for match in re.finditer(r"^#{2,4} (GET|POST|PUT|DELETE) (/api/\S+)", text, re.MULTILINE):
        found.add((match[1], normalize(match[2])))
    return found


class Kit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "static").mkdir()
        (self.root / "static" / "index.html").write_text("<html>console</html>", encoding="utf-8")
        (self.root / "secret.txt").write_text("outside the static folder", encoding="utf-8")
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.ctx = {
            "static_dir": str(self.root / "static"), "queue_dir": str(self.root / "queue"), "log_dir": str(self.root / "logs"),
            "console": ConsoleActions(JobQueue(self.store)),
        }


class RouteListTests(Kit):
    def test_docs_and_app_name_the_same_routes(self):
        """What docs/API.md documents under a ``METHOD /path`` heading is served, and every path the app serves is in the
        document (the list comes from the app, not from a hand-kept table)."""
        served = app_routes(create_app(self.ctx, OFF))
        documented = documented_routes()
        self.assertGreater(len(documented), 40)
        self.assertEqual(sorted(documented - served), [], "documented in docs/API.md but not served")
        mentioned = doccheck.doc_routes((REPO / "docs" / "API.md").read_text(encoding="utf-8"))
        unmentioned = sorted({path for _, path in served} - mentioned)
        self.assertEqual(unmentioned, [], "served but never mentioned in docs/API.md")

    def test_there_are_no_generated_documentation_routes(self):
        paths = {route.path for route in create_app(self.ctx, OFF).routes}
        self.assertFalse(paths & {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

    def test_one_catch_all_comes_last_and_nothing_serves_a_method_it_does_not_name(self):
        routes = create_app(self.ctx, OFF).routes
        self.assertEqual(routes[-1].path, "/{path:path}")
        self.assertEqual(routes[-1].methods, {"GET", "POST", "PUT"})


class ErrorShapeTests(Kit):
    """The console reads ``{"error": ...}`` and the status; the framework's own error shapes never reach it."""

    def client(self):
        return TestClient(create_app(self.ctx, OFF), raise_server_exceptions=False)

    def test_a_body_that_is_not_json_or_not_an_object_is_a_400_with_the_error_shape(self):
        client = self.client()
        for body in (b"{not json", b"[1, 2]", b'"text"'):
            with self.subTest(body=body):
                response = client.post("/api/queue/stop", content=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(response.json()), {"error"})
        self.assertEqual(client.post("/api/queue/stop", content=b"{not json").json(), {"error": "the request body is not valid JSON"})
        self.assertEqual(client.post("/api/queue/stop", content=b"").status_code, 200)  # no body is {}

    def test_an_unexpected_exception_is_a_logged_500_with_the_error_shape(self):
        class Broken:
            def snapshot(self):
                raise RuntimeError("the queue is on fire")

        self.ctx["console"] = Broken()
        with self.assertLogs(level="ERROR") as captured:
            response = self.client().get("/api/queue")
        self.assertEqual((response.status_code, response.json()), (500, {"error": "the queue is on fire"}))
        self.assertIn("GET /api/queue failed", "\n".join(captured.output))

    def test_a_missing_service_and_an_unknown_path_are_404_with_the_error_shape(self):
        client = self.client()
        for method, path in (("GET", "/api/library/series"), ("POST", "/api/nothing"), ("PUT", "/api/nothing"),
                             ("GET", "/api/nothing"), ("GET", "/api/units/abc")):
            with self.subTest(method=method, path=path):
                response = client.request(method, path)
                self.assertEqual((response.status_code, response.json()), (404, {"error": "not found"}))

    def test_a_route_with_another_method_is_refused_in_the_same_shape(self):
        response = self.client().request("DELETE", "/api/queue")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(set(response.json()), {"error"})

    def test_a_path_that_is_not_a_file_is_the_page_and_a_path_outside_the_folder_is_never_served(self):
        client = self.client()
        self.assertIn("console", client.get("/library/series/3").text)
        for path in ("/../secret.txt", "/..%2fsecret.txt", "/%2e%2e/secret.txt", "/static/../../secret.txt", "//secret.txt"):
            with self.subTest(path=path):
                self.assertNotIn("outside", client.get(path).text)
        conn = http.client.HTTPConnection("127.0.0.1", serve_console(self, self.ctx, OFF).port, timeout=5)
        try:  # not normalised by the client either: the raw request line
            for target in ("/../secret.txt", "/..%2fsecret.txt", "/%2e%2e/%2e%2e/secret.txt"):
                conn.request("GET", target)
                response = conn.getresponse()
                self.assertNotIn("outside", response.read().decode("utf-8", "replace"), target)
        finally:
            conn.close()


class BodySchemaTests(Kit):
    """P15d step 2: the request bodies have models; a refusal keeps the console's error shape."""

    def client(self):
        return TestClient(create_app(self.ctx, OFF), raise_server_exceptions=False)

    def test_a_flag_that_would_spend_must_be_a_real_boolean(self):
        """``"true"`` used to fall through to the call that spends; now it is refused and says which field."""
        for body in ({"estimate_only": "true"}, {"estimate_only": 1}, {"all": "yes"}):
            with self.subTest(body=body):
                response = self.client().post("/api/queue/retry", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertRegex(response.json()["error"], r"^(estimate_only|all): ")
        self.assertEqual(self.client().post("/api/queue/retry", json={"job_id": "nothing"}).json(), {"ok": False})

    def test_the_two_flags_whose_safe_side_is_the_default_keep_their_old_rule(self):
        client = self.client()

        class Cleanup:
            def run(self, dry_run):
                class Report:
                    def as_dict(self):
                        return {"dry_run": dry_run}
                return Report()

        self.ctx["cleanup"] = Cleanup()
        for body, expected in (({}, True), ({"dry_run": "false"}, True), ({"dry_run": 0}, True), ({"dry_run": False}, False)):
            with self.subTest(body=body):
                self.assertEqual(client.post("/api/cleanup", json=body).json(), {"dry_run": expected})

    def test_a_body_is_read_as_json_whatever_its_content_type_and_unknown_fields_pass(self):
        client = self.client()
        for content_type in ("application/x-www-form-urlencoded", "text/plain", "application/json"):
            with self.subTest(content_type=content_type):
                response = client.post(
                    "/api/queue/stop", content=b'{"job_id": "nothing", "something_new": 1}', headers={"Content-Type": content_type},
                )
                self.assertEqual((response.status_code, response.json()["ok"]), (200, False))

    def test_the_backup_upload_is_a_file_and_is_not_read_as_json(self):
        self.ctx.update(state_dir=str(self.root / "state"), config_path=str(self.root / "c.json"),
                        postprocess_targets_path=str(self.root / "t.json"), settings_service=None)
        response = self.client().post("/api/backups/import", content=b"PK not a real archive", headers={"Content-Type": "application/zip"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("JSON", response.json()["error"])
        self.assertEqual(self.client().post("/api/backups/import", content=b"").json(), {"error": "request body is empty"})

    def test_the_openapi_document_describes_every_route_and_every_body(self):
        app = create_app(self.ctx, OFF)
        schema = app.openapi()
        paths = {(method.upper(), normalize(path)) for path, item in schema["paths"].items() for method in item}
        self.assertEqual(sorted(app_routes(app) - paths), [], "a route missing from the OpenAPI document")
        defined = schema["components"]["schemas"]
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                with self.subTest(route=f"{method.upper()} {path}"):
                    declared = {p["name"] for p in operation.get("parameters", []) if p["in"] == "path"}
                    self.assertEqual(declared, set(re.findall(r"\{([^}:]+)", path)))
                    if method in {"post", "put"} and path != "/api/backups/import":
                        ref = operation["requestBody"]["content"]["application/json"]["schema"]
                        names = [part["$ref"].rsplit("/", 1)[-1] for part in ref.get("anyOf", [ref]) if "$ref" in part]
                        self.assertTrue(names and all(name in defined for name in names), "a body with no model")
        self.assertEqual(defined["CleanupBody"]["properties"]["dry_run"]["type"], "boolean")
        self.assertEqual(defined["RetryBody"]["properties"]["estimate_only"]["anyOf"][0]["type"], "boolean")


class LifespanTests(unittest.TestCase):
    def test_the_loops_start_with_the_app_and_not_before(self):
        started = threading.Event()
        loop = BackgroundLoop("probe", started.set)
        app = create_app({"static_dir": "."}, OFF, loops=[loop])
        self.assertFalse(started.wait(0.2))  # building the app starts nothing
        with TestClient(app):
            self.assertTrue(started.wait(5))

    def test_a_loop_runs_on_a_daemon_thread_named_for_it(self):
        seen = {}
        done = threading.Event()

        def target(*args):
            thread = threading.current_thread()
            seen.update(name=thread.name, daemon=thread.daemon, args=args)
            done.set()

        start_loops([BackgroundLoop("catalog-sync", target, (1, "two"))])
        self.assertTrue(done.wait(5))
        self.assertEqual(seen, {"name": "catalog-sync", "daemon": True, "args": (1, "two")})


class ThreadPoolStoreTests(Kit):
    """Handlers are synchronous and run on the thread pool; they share the one ``Store`` with the worker's thread."""

    def test_parallel_requests_over_one_store_all_answer_and_never_tear_a_write(self):
        store = Store(self.root / "app.db")
        self.addCleanup(store.close)
        service = SettingsService(
            store, config_path=str(self.root / "config.json"), postprocess_targets_path=str(self.root / "targets.json"),
            env={"BAZARR_CONFIG_PATH": str(self.root / "none.yaml")},
        )
        service.start()
        self.ctx.update(console=ConsoleActions(JobQueue(store)), settings_service=service, settings=service.settings())
        port = serve_console(self, self.ctx, OFF).port
        failures: list[str] = []
        stop = threading.Event()

        def worker_thread():  # the queue worker writes to the same store while the console answers
            n = 0
            while not stop.is_set():
                n += 1
                try:
                    store.query("SELECT COUNT(*) FROM settings")
                    with store.transaction() as db:
                        db.execute("INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES ('probe', ?, 0)", (str(n),))
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"store: {exc!r}")

        def call(method, path, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            try:
                payload = json.dumps(body).encode() if body is not None else None
                conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                return response.status, response.read()
            finally:
                conn.close()

        def client(index):
            for turn in range(6):
                for method, path, body in (
                    ("GET", "/api/queue", None), ("GET", "/api/settings", None),
                    ("POST", "/api/settings", {"gst_model": f"model-{index}-{turn}"}),
                    ("POST", "/api/queue/stop", {"job_id": "nothing"}),
                ):
                    status, raw = call(method, path, body)
                    if status != 200:
                        failures.append(f"{method} {path} -> {status} {raw[:200]!r}")

        background = threading.Thread(target=worker_thread, daemon=True)
        background.start()
        clients = [threading.Thread(target=client, args=(i,)) for i in range(8)]
        for thread in clients:
            thread.start()
        for thread in clients:
            thread.join(60)
        stop.set()
        background.join(10)
        self.assertEqual(failures, [])
        self.assertRegex(service.settings()["gst_model"], r"^model-\d-\d$")  # one whole value, never a mix


class NothingSecretIsLoggedTests(Kit):
    """FR-20.3 / NFR-5 on the real server: a password, a cookie, a Basic header and a key are in no log line, uvicorn's included."""

    PASSWORD = "correct-horse-battery-staple-4711"

    def test_login_cookie_and_basic_traffic_leave_no_secret_in_any_log(self):
        auth = ConsoleAuth.from_env({"CONSOLE_USER": "admin", "CONSOLE_PASSWORD": self.PASSWORD})
        port = serve_console(self, self.ctx, auth, sessions=True).port
        basic = base64.b64encode(f"admin:{self.PASSWORD}-wrong".encode()).decode()

        def call(method, path, headers=None, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                response = conn.getresponse()
                response.read()
                return response
            finally:
                conn.close()

        previous = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        self.addCleanup(logging.getLogger().setLevel, previous)
        with self.assertLogs(level="DEBUG") as captured:
            logging.getLogger().debug("sentinel")
            login = call("POST", "/api/auth/login", {"Content-Type": "application/json"},
                         json.dumps({"user": "admin", "password": self.PASSWORD}))
            cookie = login.getheader("Set-Cookie").split(";", 1)[0]
            call("GET", "/api/queue", {"Cookie": cookie})
            call("POST", "/api/queue/stop", {"Cookie": cookie, "Origin": "http://evil.example"}, "{}")
            call("GET", "/api/queue", {"Authorization": f"Basic {basic}"})
            call("POST", "/api/auth/password", {"Cookie": cookie, "Content-Type": "application/json"},
                 json.dumps({"current": "not-the-password-0000", "new": "another-secret-98765"}))
        text = "\n".join(captured.output)
        token = cookie.split("=", 1)[1]
        for secret in (self.PASSWORD, basic, cookie, token, "not-the-password-0000", "another-secret-98765"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
