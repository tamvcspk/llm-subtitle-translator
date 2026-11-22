"""HTTP routes for the API keys (FR-20, docs/API.md): ``/api/keys``.

Pure routing, like ``library_api``: ``handle`` maps a request to a ``SettingsService`` call and returns
``(status, payload)``, or ``None`` when the path is not one of these routes. No route returns a key's value,
only its last four characters.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter

from . import schemas
from .common import add_table_routes

_ONE = re.compile(r"^/api/keys/(\d+)(/test|/remove)?/?$")


def handles(path: str) -> bool:
    return path == "/api/keys" or path.startswith("/api/keys/")


ROUTES = [
    ("GET", r"/api/keys", None),
    ("POST", r"/api/keys", None),
    ("POST", r"/api/keys/(\d+)", None),
    ("PUT", r"/api/keys/(\d+)", None),
    ("POST", r"/api/keys/(\d+)/test", None),
    ("POST", r"/api/keys/(\d+)/remove", None),
]


BODIES = {
    ("POST", r"/api/keys"): schemas.KeyBody,
    ("POST", r"/api/keys/(\d+)"): schemas.KeyBody,
    ("PUT", r"/api/keys/(\d+)"): schemas.KeyBody,
}


def register(router: APIRouter) -> None:
    add_table_routes(
        router, ROUTES, "settings_service",
        lambda ctx, service, method, path, query, body: handle(service, ctx["http"], method, path, body), BODIES,
    )


def handle(
    service: Any, http: Any, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, Any] | None:
    body = body or {}
    path = path.rstrip("/") or "/"
    try:
        if path == "/api/keys":
            if method == "GET":
                return 200, service.list_keys()
            if method == "POST":
                return 200, service.add_key(body)
            return None
        match = _ONE.match(path)
        if match is None:
            return None
        key_id, action = int(match[1]), match[2]
        if action == "/test" and method == "POST":
            result = service.test_key(key_id, http)
            return (200, result) if result is not None else (404, {"error": f"no key {key_id}"})
        if action == "/remove" and method == "POST":
            return (200, {"ok": True}) if service.remove_key(key_id) else (404, {"error": f"no key {key_id}"})
        if action is None and method in {"POST", "PUT"}:
            updated = service.update_key(key_id, body)
            return (200, updated) if updated is not None else (404, {"error": f"no key {key_id}"})
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return None
