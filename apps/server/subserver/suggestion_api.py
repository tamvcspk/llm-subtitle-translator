"""HTTP routes of the Review inbox (docs/API.md, P13). Pure routing, like ``style_api``: ``handle`` maps a request to a
``SuggestionService`` call and returns ``(status, payload)``, or ``None`` when the path is not one of these routes.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .suggestions import SuggestionService

Query = dict[str, list[str]]
Handler = Callable[[SuggestionService, re.Match[str], Query, dict[str, Any]], Any]


def _one(query: Query, name: str) -> str | None:
    value = (query.get(name) or [""])[0]
    return value or None


def _number(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


def _series_id(body: dict[str, Any], what: str = "accepting in bulk") -> int:
    value = _number(None if body.get("series_id") is None else str(body["series_id"]), "series_id")
    if value is None:
        raise ValueError(f"series_id is required: {what} is always one series")
    return value


def _list(s: SuggestionService, m: re.Match[str], q: Query, b: dict[str, Any]) -> Any:
    return s.listing(
        series_id=_number(_one(q, "series"), "series"), status=_one(q, "status"), source=_one(q, "source"),
        lang=_one(q, "lang"), limit=_number(_one(q, "limit"), "limit") or 500,
    )


ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", r"/api/suggestions", _list),
    ("GET", r"/api/suggestions/summary", lambda s, m, q, b: s.summary()),
    # One series at a time, on purpose: the answer to "accept all" is never "accept every series".
    ("POST", r"/api/suggestions/bulk", lambda s, m, q, b: s.bulk(
        _series_id(b), lang=str(b.get("lang") or "") or None, expect=b.get("expect"), dry_run=bool(b.get("dry_run")),
    )),
    # Ask the facts generator for proposals (spends model calls unless `estimate_only`; the page puts it behind Estimate).
    ("POST", r"/api/suggestions/generate", lambda s, m, q, b: s.generate(
        _series_id(b, "generating"), str(b.get("lang") or ""), estimate_only=bool(b.get("estimate_only")),
    )),
    # What the admin keeps correcting, as suggestions (P15): no model call, so it answers with what it found.
    ("POST", r"/api/suggestions/from-edits", lambda s, m, q, b: s.from_edits(
        _series_id(b, "looking at the edits"), str(b.get("lang") or ""),
    )),
    ("POST", r"/api/suggestions/(\d+)/accept", lambda s, m, q, b: s.accept(
        int(m[1]), payload=b.get("payload"), dry_run=bool(b.get("dry_run")),
    )),
    ("POST", r"/api/suggestions/(\d+)/reject", lambda s, m, q, b: s.reject(int(m[1]))),
    ("POST", r"/api/suggestions/(\d+)/undo", lambda s, m, q, b: s.undo(int(m[1]), dry_run=bool(b.get("dry_run")))),
]

_COMPILED = [(method, re.compile(rf"^{pattern}/?$"), handler) for method, pattern, handler in ROUTES]


def handles(path: str) -> bool:
    return path == "/api/suggestions" or path.startswith("/api/suggestions/")


def handle(
    service: SuggestionService, method: str, path: str, query: Query, body: dict[str, Any] | None = None
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
