"""The console's HTTP adapter: a FastAPI app served by uvicorn inside the worker's own process (ADR-18, P15d).

``create_app`` builds the app from the runtime context: the gate (``security.AuthGate``) in front of everything, the routers
of every page, the sign-in page and the static files of the console as the fallback. The worker loop, the catalog sync and
the daily maintenance start and stop in the app's ``lifespan`` (D7: there is no second background mechanism).
Queue and unit logic live in ``ConsoleActions`` and ``UnitService``; a route only parses, calls and formats.
"""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response

from ..runtime import BackgroundLoop, start_loops
from ..services import auth as console_auth
from ..services.auth import ConsoleAuth
from . import auth_api, catalog_api, console_api, insight_api, keys_api, library_api, style_api, suggestion_api
from .common import ConsoleRoute, blocking, ctx_of, error_response, validation_message
from .security import AuthGate

STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",  # the React console ships its fonts; the registry on Windows does not know this one
}


def static_response(static_dir: str, request_path: str, no_store: bool = False) -> Response:
    """A file of the console's build. A path that is not a file is the app's entry page (the console routes in the browser)."""
    root = Path(static_dir)
    rel = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
    try:
        path = (root / rel).resolve()
        # Inside the build folder by path parts: a prefix test would also let a sibling folder through (``static-x``).
        inside = path.is_relative_to(root.resolve()) and path.is_file()
    except (OSError, ValueError):  # a name the file system refuses (a NUL byte, a path too long)
        inside = False
    if not inside:
        path = root / "index.html"
    # Module scripts require a JavaScript MIME type; the Windows registry can map .js to text/plain, so the console's
    # own asset types are fixed here.
    content_type = STATIC_CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return Response(
        path.read_bytes(), media_type=content_type, headers={"Cache-Control": "no-store" if no_store else "no-cache"},
    )


def register_pages(router: Any) -> None:
    """The sign-in page and the static files: the fallback of every GET that is not a route."""

    @router.get("/login", include_in_schema=False)
    async def login_page(request: Request) -> Response:
        return await blocking(static_response, ctx_of(request)["static_dir"], "/login.html", True)

    @router.api_route("/{path:path}", methods=["GET", "POST", "PUT"], include_in_schema=False)
    async def fallback(request: Request, path: str) -> Response:
        # An API path that no route has is not the console's page: say so, the way every other refusal is said.
        if request.method != "GET" or request.url.path.startswith("/api/"):
            return error_response("not found", status=404)
        return await blocking(static_response, ctx_of(request)["static_dir"], request.url.path)


def register_routes(router: Any) -> None:
    """Every route of the console, on the app's own router, so ``app.routes`` is the list of them."""
    router.route_class = ConsoleRoute
    auth_api.register(router)
    console_api.register(router)
    for module in (library_api, style_api, suggestion_api, catalog_api, keys_api, insight_api):
        module.register(router)
    register_pages(router)  # last: the catch-all


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    start_loops(app.state.loops)
    yield


def create_app(
    ctx: dict[str, Any],
    auth: ConsoleAuth | None,
    *,
    sessions: console_auth.SessionStore | None = None,
    throttle: console_auth.LoginThrottle | None = None,
    loops: Sequence[BackgroundLoop] = (),
) -> FastAPI:
    """The console as an ASGI app. ``auth`` is the account (``None`` fails closed: every request is a 503); without
    ``sessions`` there is no form login, only Basic Auth."""
    # No generated docs or schema routes: the console's API is described in docs/API.md, and a route that is not
    # the console's has no business behind the gate.
    app = FastAPI(
        title="LLM Subtitle Translator", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
        redirect_slashes=False,
    )
    app.state.ctx = ctx
    app.state.auth = auth
    app.state.sessions = sessions
    app.state.throttle = throttle
    app.state.loops = tuple(loops)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        return error_response(str(exc.detail), status=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> Response:
        return error_response(validation_message(exc), status=400)

    register_routes(app.router)
    app.add_middleware(AuthGate)
    return app


def start_web_server(
    ctx: dict[str, Any], host: str, port: int, auth: ConsoleAuth, loops: Sequence[BackgroundLoop] = (),
) -> None:
    """Serve the console until the process is told to stop. Blocks."""
    app = create_app(
        ctx, auth, sessions=console_auth.SessionStore.for_auth(auth), throttle=console_auth.LoginThrottle(), loops=loops,
    )
    auth.log_startup()
    logging.info("LLM Subtitle Translator listening on http://%s:%s", host, port)
    # uvicorn must not configure logging (the worker's own formatter redacts secrets) nor log each request (the gate
    # logs the failed ones); it answers as itself, not as a proxy's client.
    uvicorn.run(
        app, host=host, port=port, log_config=None, access_log=False, server_header=False, date_header=True,
        proxy_headers=False, ws="none",
    )
