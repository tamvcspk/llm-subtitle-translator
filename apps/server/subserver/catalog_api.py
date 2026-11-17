"""HTTP routes of the Library page: the catalog, "sync now", requests, Ignored and the per-series policy
(docs/API.md). Pure routing, like ``library_api``: ``handle`` maps a request to a ``CatalogActions`` call and
returns ``(status, payload)``, or ``None`` when the path is not one of these routes.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .catalog_actions import CatalogActions

Handler = Callable[[CatalogActions, re.Match[str], dict[str, Any]], Any]

ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", r"/api/catalog", lambda a, m, b: a.overview()),
    ("GET", r"/api/catalog/status", lambda a, m, b: a.status()),
    ("GET", r"/api/catalog/movies", lambda a, m, b: a.movies_page()),
    ("GET", r"/api/catalog/series/(\d+)", lambda a, m, b: a.series_page(int(m[1]))),
    ("POST", r"/api/catalog/sync", lambda a, m, b: a.sync_now()),
    ("PUT", r"/api/catalog/series/(\d+)/policy", lambda a, m, b: a.set_policy(int(m[1]), b)),
    ("PUT", r"/api/catalog/series/(\d+)/bootstrap", lambda a, m, b: a.set_bootstrap(int(m[1]), b)),
    ("POST", r"/api/catalog/ignore", lambda a, m, b: a.ignore(b, True)),
    ("POST", r"/api/catalog/unignore", lambda a, m, b: a.ignore(b, False)),
    ("POST", r"/api/jobs", lambda a, m, b: a.request(b)),
]

_COMPILED = [(method, re.compile(rf"^{pattern}/?$"), handler) for method, pattern, handler in ROUTES]


def handles(path: str) -> bool:
    return path.startswith("/api/catalog") or path == "/api/jobs"


def handle(actions: CatalogActions, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any] | None:
    for route_method, pattern, handler in _COMPILED:
        match = pattern.match(path)
        if match and route_method == method:
            try:
                return 200, handler(actions, match, body or {})
            except LookupError as exc:
                return 404, {"error": str(exc.args[0]) if exc.args else "not found"}
            except ValueError as exc:
                return 400, {"error": str(exc)}
    return None
