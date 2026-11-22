"""The gate in front of every request (NFR-5, SPEC criteria 9 and 24c): an ASGI middleware, so it runs before any route,
the static files of the console included.

Nothing passes without a session cookie or Basic Auth, except ``GET /login`` and ``POST /api/auth/login``. A refusal never
says which part of a credential was wrong in the answer (the log does, without the secret); a cookie session that is about to
age comes back re-issued (sliding expiry); a write by a cookie must come from this very host (the CSRF guard).
"""
from __future__ import annotations

import logging
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..services import auth as console_auth
from .common import error_response, json_response


def is_https(request: Request) -> bool:
    # TLS ends at a reverse proxy; trust its header only to add the Secure flag.
    return (request.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https"


def session_cookie(request: Request, token: str, max_age: int | None = None) -> str:
    """The session cookie as this deployment wants it (``CONSOLE_COOKIE_SAMESITE``, ``Secure`` behind HTTPS)."""
    auth = getattr(request.app.state, "auth", None)
    extra = {} if max_age is None else {"max_age": max_age}
    return console_auth.session_cookie(
        token, secure=is_https(request), same_site=getattr(auth, "cookie_same_site", "strict"), **extra,
    )


def origin_ok(request: Request) -> bool:
    """Does a write come from this console's own page? With ``CONSOLE_COOKIE_SAMESITE=none`` the cookie crosses sites, so
    this is the only CSRF guard and a request that names no origin must at least say it is same-origin."""
    auth = getattr(request.app.state, "auth", None)
    return console_auth.same_origin(
        request.headers.get("Origin"), request.headers.get("Host"), request.headers.get("X-Forwarded-Host"),
        fetch_site=request.headers.get("Sec-Fetch-Site"),
        require_origin=getattr(auth, "cookie_same_site", "strict") == "none",
    )


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def decide(request: Request) -> Response | None:
    """``None`` to let the request through (and note on ``request.state`` how it got in), or the refusal to send.
    Synchronous on purpose: it checks a scrypt hash and reads the account from SQLite, so the middleware runs it on the
    thread pool."""
    state = request.state
    state.auth_via = "none"
    state.refreshed_token = None
    app_state = request.app.state
    auth: console_auth.ConsoleAuth | None = getattr(app_state, "auth", None)
    sessions: console_auth.SessionStore | None = getattr(app_state, "sessions", None)
    throttle: console_auth.LoginThrottle | None = getattr(app_state, "throttle", None)
    authorization = request.headers.get("Authorization")
    path = request.url.path
    method = request.method
    # No auth handle on the app means a wiring mistake: fail closed.
    if auth is None:
        unconfigured = True
    elif not auth.enabled:
        state.auth_via = "off"
        return None
    else:
        unconfigured = not auth.configured
    if unconfigured:
        return error_response(console_auth.UNCONFIGURED_MESSAGE, status=503)

    # The only requests that need no credentials: the login page and the login call itself.
    is_login_page = method in {"GET", "HEAD"} and path == "/login"
    is_login_call = method == "POST" and path == "/api/auth/login"
    if (is_login_page or is_login_call) and sessions is not None:
        if is_login_call and not origin_ok(request):
            return error_response("cross-origin login refused", status=403)
        return None

    # Cookie session (browser). Writes must come from this very host: this is the CSRF guard, and the only one
    # when CONSOLE_COOKIE_SAMESITE=none lets the cookie travel with a request from another site.
    cookie_token = console_auth.session_token(request.headers.get("Cookie")) if sessions is not None else None
    resolved = sessions.resolve(cookie_token) if sessions is not None else None
    if resolved:
        state.refreshed_token = resolved[1]  # sliding expiry: sent back with whatever this request answers
        if method not in {"GET", "HEAD", "OPTIONS"} and not origin_ok(request):
            state.refreshed_token = None
            return error_response("cross-origin request refused", status=403)
        state.auth_via = "session"
        return None

    # Basic Auth (scripts), shared lockout with the login form.
    address = client_address(request)
    if authorization and throttle is not None:
        wait = throttle.locked_for(address)
        if wait:
            return json_response({"error": f"too many failed logins; try again in {wait} s"}, status=429,
                                 headers={"Retry-After": str(wait)})
    if authorization and auth.check(authorization) == console_auth.ALLOW:
        if throttle is not None:
            throttle.success(address)
        state.auth_via = "basic"
        return None

    if authorization:
        if throttle is not None and throttle.fail(address):
            logging.warning("web %s - locked for %s s after repeated wrong logins", address, console_auth.LOCK_SECONDS)
        logging.info(
            "web %s - login refused for %s: %s (client: %s)",
            address, path[:80], console_auth.explain_failure(auth, authorization),
            (request.headers.get("User-Agent") or "unknown")[:90],
        )
    wants_page = method in {"GET", "HEAD"} and not path.startswith("/api/") \
        and "text/html" in (request.headers.get("Accept") or "")
    # A browser (it sends Sec-Fetch-*, or asks for a page) must never see the Basic prompt.
    browser = wants_page or "Sec-Fetch-Mode" in request.headers or bool(cookie_token)
    if sessions is not None and wants_page and not authorization:
        return Response(status_code=302, headers={"Location": "/login", "Cache-Control": "no-store"})
    if sessions is not None and browser and not authorization:
        return error_response("authentication required", status=401)
    return error_response(
        "authentication required", status=401,
        headers={"WWW-Authenticate": f'Basic realm="{console_auth.REALM}", charset="UTF-8"'},
    )


class AuthGate:
    """Pure ASGI middleware (not ``BaseHTTPMiddleware``): it sits in front of the router, the fallback and the static files,
    adds the re-issued session cookie to whatever the request answers, and logs a failed request (400 and up)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # the console has no WebSocket; refuse rather than route one unauthenticated
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        request = Request(scope, receive)
        scope_state: dict[str, Any] = scope.setdefault("state", {})

        async def respond(message: Message) -> None:
            if message["type"] == "http.response.start":
                status = int(message["status"])
                token = scope_state.get("refreshed_token")
                if token:
                    MutableHeaders(scope=message).append("Set-Cookie", session_cookie(request, token))
                if status >= 400:
                    logging.info('web %s - "%s %s" %s', client_address(request), request.method, request.url.path, status)
            await send(message)

        refusal = await run_in_threadpool(decide, request)
        if refusal is not None:
            await refusal(scope, receive, respond)
            return
        await self.app(scope, receive, respond)
