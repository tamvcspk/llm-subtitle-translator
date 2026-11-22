"""Serve the console's FastAPI app on a loopback port for a test (P15d).

The tests talk to it with ``http.client``/``urllib`` as a browser or a script would, so cookies, headers, redirects and the
answer of the real server (uvicorn) are what they see; ``TestClient`` is used where a test only needs the app's routes.
"""
from __future__ import annotations

import threading
import time
import unittest
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI

from subserver.api.web import create_app
from subserver.services.auth import ConsoleAuth, LoginThrottle, SessionStore


@dataclass
class Served:
    app: FastAPI
    port: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def serve_app(case: unittest.TestCase, app: FastAPI) -> Served:
    """Run ``app`` until the test ends. The port is chosen by the system."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_config=None, access_log=False, server_header=False, proxy_headers=False, ws="none",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.01)

    def stop() -> None:
        server.should_exit = True
        thread.join(10)

    case.addCleanup(stop)
    return Served(app, server.servers[0].sockets[0].getsockname()[1])


def serve_console(
    case: unittest.TestCase, ctx: dict[str, Any], auth: ConsoleAuth | None, *, sessions: bool = False,
) -> Served:
    """The console as ``worker.py`` serves it: ``ctx`` is the runtime context, ``auth`` the account (``None`` fails closed).
    With ``sessions`` the form login and its throttle are there too, as in the running process."""
    app = create_app(
        ctx, auth,
        sessions=SessionStore.for_auth(auth) if sessions and auth is not None else None,
        throttle=LoginThrottle() if sessions else None,
    )
    return serve_app(case, app)
