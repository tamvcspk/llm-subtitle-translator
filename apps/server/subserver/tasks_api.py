"""HTTP routes of the background tasks Activity lists (docs/API.md). Pure routing, like ``suggestion_api``: ``handle`` maps
a request to a ``BackgroundTasks`` call and returns ``(status, payload)``, or ``None`` when the path is not one of these."""
from __future__ import annotations

import re
from typing import Any, Callable

from .background import BackgroundTasks

Query = dict[str, list[str]]
Handler = Callable[[BackgroundTasks, re.Match[str], Query, dict[str, Any]], Any]

ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", r"/api/tasks", lambda s, m, q, b: {"items": s.tasks()}),
    ("GET", r"/api/tasks/([0-9a-f]{12})", lambda s, m, q, b: s.get(m[1])),
    ("POST", r"/api/tasks/([0-9a-f]{12})/cancel", lambda s, m, q, b: s.cancel(m[1])),
]

_COMPILED = [(method, re.compile(rf"^{pattern}/?$"), handler) for method, pattern, handler in ROUTES]


def handles(path: str) -> bool:
    return path == "/api/tasks" or path.startswith("/api/tasks/")


def handle(
    service: BackgroundTasks, method: str, path: str, query: Query, body: dict[str, Any] | None = None
) -> tuple[int, Any] | None:
    for route_method, pattern, handler in _COMPILED:
        match = pattern.match(path)
        if match and route_method == method:
            try:
                return 200, handler(service, match, query, body or {})
            except LookupError as exc:
                return 404, {"error": str(exc.args[0]) if exc.args else "not found"}
            except ValueError as exc:
                return 400, {"error": str(exc)}
    return None
