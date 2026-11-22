"""The account routes (docs/API.md, "Authentication"): who am I, sign in, sign out, change the password.

The gate in ``security.py`` has already decided who may call what; these routes only use the session store, the throttle
and the account that ``worker.py`` put on the app.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Request
from starlette.responses import Response

from ..services import auth as console_auth
from .common import blocking, error_response, json_response, payload
from .schemas import LoginBody, PasswordBody, RequestObject
from .security import client_address, session_cookie


def _auth(request: Request) -> console_auth.ConsoleAuth:
    return request.app.state.auth


def _sessions(request: Request) -> console_auth.SessionStore | None:
    return getattr(request.app.state, "sessions", None)


def _throttle(request: Request) -> console_auth.LoginThrottle | None:
    return getattr(request.app.state, "throttle", None)


def register(router: APIRouter) -> None:
    @router.get("/api/auth/me")
    async def me(request: Request) -> Response:
        auth = _auth(request)
        via = request.state.auth_via
        user = await blocking(lambda: auth.name) if via in {"session", "basic"} else None
        return json_response({"user": user, "via": via, "can_change_password": auth.can_change_password})

    @router.post("/api/auth/login")
    async def login(request: Request, body: LoginBody | None = Body(default=None)) -> Response:
        """The one call that runs without credentials (the form login)."""
        return await blocking(_login, request, payload(body))

    @router.post("/api/auth/logout")
    async def logout(request: Request, body: RequestObject | None = Body(default=None)) -> Response:
        sessions = _sessions(request)
        if sessions is not None:
            sessions.revoke(console_auth.session_token(request.headers.get("Cookie")))
        request.state.refreshed_token = None
        return json_response({"ok": True}, headers={"Set-Cookie": session_cookie(request, "", max_age=0)})

    @router.post("/api/auth/password")
    async def password(request: Request, body: PasswordBody | None = Body(default=None)) -> Response:
        return await blocking(_change_password, request, payload(body))


def _login(request: Request, body: dict[str, Any]) -> Response:
    auth, sessions, throttle = _auth(request), _sessions(request), _throttle(request)
    if not auth.enabled:
        return json_response({"ok": True, "via": "off"})
    if sessions is None or throttle is None:
        return error_response("form login is not available", status=404)
    address = client_address(request)
    wait = throttle.locked_for(address)
    if wait:
        return json_response({"error": f"too many failed logins; try again in {wait} s"}, status=429,
                             headers={"Retry-After": str(wait)})
    user, password = str(body.get("user", "")), str(body.get("password", ""))
    if not auth.verify(user, password):
        if throttle.fail(address):
            logging.warning("web %s - locked for %s s after repeated wrong logins", address, console_auth.LOCK_SECONDS)
        logging.info("web %s - form login refused (wrong user name or password)", address)
        return error_response("Wrong user name or password", status=401)
    throttle.success(address)
    token = sessions.create(auth.name)
    return json_response({"ok": True, "user": auth.name}, headers={"Set-Cookie": session_cookie(request, token)})


def _change_password(request: Request, body: dict[str, Any]) -> Response:
    """The caller proves the current password; every session then ends."""
    auth, sessions, throttle = _auth(request), _sessions(request), _throttle(request)
    if not auth.can_change_password:
        return error_response("The password is not managed by the console (authentication is off)", status=409)
    address = client_address(request)
    wait = throttle.locked_for(address) if throttle is not None else 0
    if wait:
        return json_response({"error": f"too many failed attempts; try again in {wait} s"}, status=429,
                             headers={"Retry-After": str(wait)})
    current, new = str(body.get("current", "")), str(body.get("new", ""))
    # 403, not 401: the browser client treats 401 as "session ended" and leaves the page.
    if not auth.password_matches(current):
        if throttle is not None and throttle.fail(address):
            logging.warning("web %s - locked for %s s after repeated wrong logins", address, console_auth.LOCK_SECONDS)
        logging.info("web %s - password change refused (wrong current password)", address)
        return error_response("The current password is wrong", status=403)
    if not console_auth.MIN_PASSWORD_LENGTH <= len(new) <= console_auth.MAX_PASSWORD_LENGTH:
        return error_response(
            f"The new password must be {console_auth.MIN_PASSWORD_LENGTH}-{console_auth.MAX_PASSWORD_LENGTH} characters",
            status=400,
        )
    if throttle is not None:
        throttle.success(address)
    auth.change_password(new)  # also rotates the session secret: every session, this one too, is dead
    request.state.refreshed_token = None
    headers: dict[str, str] = {}
    if sessions is not None:
        sessions.revoke_all()
        if request.state.auth_via == "session":  # the person who changed it stays signed in
            headers["Set-Cookie"] = session_cookie(request, sessions.create(auth.name))
    logging.info("web %s - console password changed; all sessions ended", address)
    return json_response({"ok": True}, headers=headers)
