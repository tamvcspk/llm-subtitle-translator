"""Run the real worker on seeded data for the contract test of the console's answers (P15e).

``worker.py`` is started as a process, as ``scripts/preview.ps1`` starts it (no queue worker, no model, no Bazarr; the
account check is off and the port is on loopback), over a state folder that ``tests.preview_seed`` filled with a small library
made by the real code. A test then calls the routes with ``http.client`` and checks each answer against the model the route
declares. Starting the process, not building the services in the test, is what makes the test cover the composition too.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from starlette.routing import Match

from subserver.api.web import create_app
from tests import preview_seed

SERVER = Path(__file__).resolve().parents[1]
APP = create_app({}, None)
# The (method, route path) of every route a case has called with a 200 and checked: the last test reads it.
COVERED: set[tuple[str, str]] = set()


def route_for(method: str, path: str):
    """The route of the app that serves ``method path`` (``None`` when there is none)."""
    scope = {"type": "http", "method": method, "path": path.split("?", 1)[0]}
    for route in APP.routes:
        if route.path.startswith("/api/") and route.matches(scope)[0] == Match.FULL:
            return route
    return None


def check_answer(model: Any, data: Any) -> None:
    """Raise ``ValidationError`` unless ``data`` is what ``model`` says. Strict: the text ``"1"`` is not the number 1."""
    TypeAdapter(model).validate_python(data, strict=True)


class Worker:
    """One ``worker.py`` process over a seeded state folder. Used from a ``setUpClass``."""

    def __init__(self, *, edit_demo: bool = False) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        preview_seed.seed(self.state, (1, 2), edit_demo=edit_demo, candidate=True)
        (self.state / "static").mkdir()
        (self.state / "static" / "index.html").write_text("<html></html>", encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("GEMINI_", "OPENAI_", "BAZARR_", "CONSOLE_", "TMDB_"))},
            "STATE_DIR": str(self.state), "DATA_DIR": str(self.state), "QUEUE_DIR": str(self.state / "queue"),
            "LOG_DIR": str(self.state / "logs"), "APP_CONFIG_PATH": str(self.state / "config.json"),
            "POSTPROCESS_TARGETS_PATH": str(self.state / "postprocess" / "targets.json"),
            "TMDB_CACHE_PATH": str(self.state / "cache" / "tmdb_cache.json"), "STATIC_DIR": str(self.state / "static"),
            "CONSOLE_AUTH": "off", "BAZARR_URL": "http://127.0.0.1:1", "HTTP_TIMEOUT_SECONDS": "2",
            "GST_ONLY_PATHS": str(self.state / "preview-media"), "PYTHONUNBUFFERED": "1",
        }
        (self.state / "queue").mkdir()
        self.log = (self.state / "worker.out").open("wb")
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER / "worker.py"), "--host", "127.0.0.1", "--port", str(self.port), "--no-worker"],
            cwd=SERVER, env=env, stdout=self.log, stderr=subprocess.STDOUT,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 60
        while True:
            try:
                self.call("GET", "/api/auth/me")
                return
            except (urllib.error.URLError, ConnectionError, OSError):
                if self.process.poll() is not None or time.monotonic() > deadline:
                    self.stop()
                    raise RuntimeError("the worker did not start:\n" + self.output())
                time.sleep(0.2)

    def output(self) -> str:
        self.log.flush()
        return (self.state / "worker.out").read_text(encoding="utf-8", errors="replace")[-1500:]

    def call(self, method: str, path: str, body: Any = None, *, raw: bytes | None = None) -> tuple[int, Any]:
        """``(status, JSON answer)``; an answer that is not JSON (a file) comes back as its text."""
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode("utf-8"))
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/zip" if raw is not None else "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        text = payload.decode("utf-8", "replace")
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    def download(self, path: str) -> tuple[int, bytes]:
        """``(status, bytes)`` of a file the console serves."""
        with urllib.request.urlopen(self.base + path, timeout=30) as response:
            return response.status, response.read()

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(10)
        self.log.close()
        try:
            self.tmp.cleanup()
        except OSError:  # Windows keeps a file of the database open a moment longer
            pass


class ContractCase(unittest.TestCase):
    """A test class that shares one worker and checks answers against the models the routes declare."""

    worker: Worker
    edit_demo = False

    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = Worker(edit_demo=cls.edit_demo)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.worker.stop()

    def movie_unit(self) -> int:
        """The unit of the seeded movie. The worker makes a unit only when the admin acts on an item, so this acts on it
        (it takes back a decision of "do not translate", which it never made)."""
        movie = self.answer("GET", "/api/catalog/movies")["items"][0]
        self.answer("POST", "/api/catalog/unignore", {"items": [movie["id"]], "languages": ["vi"]})
        return int(self.answer("GET", "/api/library/movies")["items"][0]["id"])

    def answer_raw(self, method: str, path: str, raw: bytes, *, status: int = 200) -> Any:
        """The same for a route whose body is a file (the backup to import)."""
        return self.answer(method, path, None, status=status, raw=raw)

    def answer(self, method: str, path: str, body: Any = None, *, status: int = 200, raw: bytes | None = None) -> Any:
        """Call a route; check the status and, for a JSON answer, that it fits the model the route declares."""
        code, data = self.worker.call(method, path, body, raw=raw)
        self.assertEqual(code, status, f"{method} {path} -> {code}: {data}\n{self.worker.output()}")
        route = route_for(method, path)
        self.assertIsNotNone(route, f"{method} {path} is not a route of the app")
        if status == 200:
            self.assertIsNotNone(route.response_model, f"{method} {route.path} declares no answer model")
            check_answer(route.response_model, data)
            COVERED.add((method, route.path))
        return data
