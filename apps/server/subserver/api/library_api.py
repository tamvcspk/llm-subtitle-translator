"""HTTP routes for the library, episodes, corrections, versions and glossaries (docs/API.md).

Pure routing: ``handle`` maps a request to a ``UnitService`` call and returns ``(status, payload)``,
or ``None`` when the path is not one of these routes. The stdlib handler stays a thin adapter.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..services.units import UnitService

Query = dict[str, list[str]]
Handler = Callable[[UnitService, re.Match[str], Query, dict[str, Any]], Any]


def _one(query: Query, name: str, default: str | None = None) -> str | None:
    values = query.get(name)
    return values[0] if values else default


def _int(query: Query, name: str, default: int) -> int:
    value = _one(query, name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


def _lang(query: Query, body: dict[str, Any] | None = None) -> str | None:
    return _one(query, "lang") or ((body or {}).get("lang") or None)


def _body_int(body: dict[str, Any], name: str) -> int:
    try:
        return int(body[name])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None


def _glossary_lang(query: Query, body: dict[str, Any] | None = None) -> str:
    lang = _lang(query, body)
    if not lang:
        raise ValueError("lang is required")
    return str(lang)


ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", r"/api/library/series", lambda s, m, q, b: {"items": s.library.series_list()}),
    ("GET", r"/api/library/series/(\d+)/units", lambda s, m, q, b: s.library.series_units(int(m[1]), _one(q, "status"))),
    ("GET", r"/api/library/movies", lambda s, m, q, b: {"items": s.library.movies(_one(q, "status"))}),
    ("GET", r"/api/units/(\d+)", lambda s, m, q, b: s.detail(int(m[1]))),
    (
        "GET",
        r"/api/units/(\d+)/cues",
        lambda s, m, q, b: s.cues(
            int(m[1]), _lang(q), at=_one(q, "at"), q=_one(q, "q"),
            offset=_int(q, "offset", 0), limit=_int(q, "limit", 5000),
        ),
    ),
    ("GET", r"/api/units/(\d+)/corrections", lambda s, m, q, b: {"items": s.list_corrections(int(m[1]), _lang(q))}),
    ("POST", r"/api/units/(\d+)/corrections", lambda s, m, q, b: s.add_correction(int(m[1]), b)),
    (
        "POST",
        r"/api/units/(\d+)/corrections/(\d+)/remove",
        lambda s, m, q, b: {"ok": s.remove_correction(int(m[1]), int(m[2]))},
    ),
    ("POST", r"/api/units/(\d+)/retranslate", lambda s, m, q, b: s.retranslate(int(m[1]), b)),
    ("POST", r"/api/units/(\d+)/variant", lambda s, m, q, b: s.variant(int(m[1]), b)),
    ("GET", r"/api/units/(\d+)/versions/(\d+)/compare", lambda s, m, q, b: s.compare(int(m[1]), int(m[2]), _lang(q))),
    ("POST", r"/api/units/(\d+)/versions/(\d+)/choose", lambda s, m, q, b: s.choose(int(m[1]), int(m[2]), b)),
    ("POST", r"/api/units/(\d+)/versions/(\d+)/judge", lambda s, m, q, b: s.ask_judge(int(m[1]), int(m[2]), b)),
    ("GET", r"/api/units/(\d+)/versions", lambda s, m, q, b: s.list_versions(int(m[1]), _lang(q))),
    (
        "GET",
        r"/api/units/(\d+)/versions/(\d+)/diff",
        lambda s, m, q, b: s.diff(
            int(m[1]), int(m[2]), _int(q, "against", 0) or None, _lang(q)
        ),
    ),
    ("POST", r"/api/units/(\d+)/versions/(\d+)/revert", lambda s, m, q, b: s.revert(int(m[1]), int(m[2]), _lang(q, b))),
    (
        "POST",
        r"/api/units/(\d+)/versions/(\d+)/adopt",
        lambda s, m, q, b: s.adopt(
            int(m[1]), int(m[2]), _lang(q, b), apply_style=b.get("apply_style") is True, dry_run=b.get("dry_run") is True,
        ),
    ),
    ("POST", r"/api/units/(\d+)/output/remove", lambda s, m, q, b: s.remove_output(int(m[1]), _lang(q, b))),
    ("GET", r"/api/units/(\d+)/context", lambda s, m, q, b: s.context(int(m[1]), _lang(q))),
    ("GET", r"/api/units/(\d+)/facts", lambda s, m, q, b: s.unit_facts(int(m[1]))),
    ("POST", r"/api/units/(\d+)/context/extract", lambda s, m, q, b: s.refresh_context(int(m[1]), b)),
    ("GET", r"/api/series/(\d+)/memory", lambda s, m, q, b: s.series_memory(int(m[1]))),
    ("PUT", r"/api/series/(\d+)/memory", lambda s, m, q, b: s.set_series_memory(int(m[1]), b)),
    ("POST", r"/api/series/(\d+)/memory/adopt", lambda s, m, q, b: s.adopt_episodes(int(m[1]))),
    ("GET", r"/api/series/(\d+)/glossary", lambda s, m, q, b: s.glossary(int(m[1]), _glossary_lang(q))),
    (
        "PUT",
        r"/api/series/(\d+)/glossary",
        lambda s, m, q, b: s.save_glossary(
            int(m[1]), _glossary_lang(q, b), None if b.get("doc") is not None else str(b.get("yaml") or ""),
            doc=b.get("doc"), dry_run=bool(b.get("dry_run")),
        ),
    ),
    ("GET", r"/api/series/(\d+)/glossary/history", lambda s, m, q, b: s.glossary_history(int(m[1]), _glossary_lang(q))),
    (
        "POST",
        r"/api/series/(\d+)/glossary/restore",
        lambda s, m, q, b: s.restore_glossary(
            int(m[1]), _glossary_lang(q, b), _body_int(b, "version"), dry_run=bool(b.get("dry_run")),
        ),
    ),
    (
        "POST",
        r"/api/series/(\d+)/glossary/copy",
        lambda s, m, q, b: s.copy_glossary(
            int(m[1]), _glossary_lang(q, b), _body_int(b, "from_series"), merge=bool(b.get("merge")),
            dry_run=bool(b.get("dry_run")),
        ),
    ),
    # A movie's own glossary, by the movie's unit id (P12b). No copy: a glossary is copied between series.
    ("GET", r"/api/movies/(\d+)/glossary", lambda s, m, q, b: s.movie_glossary(int(m[1]), _glossary_lang(q))),
    (
        "PUT",
        r"/api/movies/(\d+)/glossary",
        lambda s, m, q, b: s.save_movie_glossary(
            int(m[1]), _glossary_lang(q, b), None if b.get("doc") is not None else str(b.get("yaml") or ""),
            doc=b.get("doc"), dry_run=bool(b.get("dry_run")),
        ),
    ),
    ("GET", r"/api/movies/(\d+)/glossary/history", lambda s, m, q, b: s.movie_glossary_history(int(m[1]), _glossary_lang(q))),
    (
        "POST",
        r"/api/movies/(\d+)/glossary/restore",
        lambda s, m, q, b: s.restore_movie_glossary(
            int(m[1]), _glossary_lang(q, b), _body_int(b, "version"), dry_run=bool(b.get("dry_run")),
        ),
    ),
]

_COMPILED =[(method, re.compile(rf"^{pattern}/?$"), handler) for method, pattern, handler in ROUTES]


_EXPORT = re.compile(r"^/api/(series|movies)/(\d+)/glossary\.yaml/?$")


def download(service: UnitService, path: str, query: Query) -> tuple[int, str, str] | None:
    """``GET /api/{series|movies}/{id}/glossary.yaml?lang=``: the glossary as a file a link can fetch (no ``blob:``).
    Returns ``(status, file name, YAML text)``, or ``(status, "", message)`` for an error; ``None`` when the path is
    not this route."""
    match = _EXPORT.match(path)
    if match is None:
        return None
    try:
        name, text = service.glossary_export(match[1], int(match[2]), _glossary_lang(query))
    except LookupError as exc:
        return 404, "", str(exc.args[0]) if exc.args else "not found"
    except ValueError as exc:
        return 400, "", str(exc)
    return 200, name, text


def handles(path: str) -> bool:
    return path.startswith(("/api/library/", "/api/units/", "/api/series/", "/api/movies/"))


def handle(
    service: UnitService, method: str, path: str, query: Query, body: dict[str, Any] | None = None
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
