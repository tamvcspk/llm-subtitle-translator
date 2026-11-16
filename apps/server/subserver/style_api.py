"""HTTP routes of the Style page: a series' style, genre presets, character cards, the trial of a style and an
episode's own style (docs/API.md). Pure routing, like ``library_api``: ``handle`` maps a request to a ``StyleService``
call and returns ``(status, payload)``, or ``None`` when the path is not one of these routes.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .style_service import StyleService

Query = dict[str, list[str]]
Handler = Callable[[StyleService, re.Match[str], Query, dict[str, Any]], Any]


def _lang(query: Query, body: dict[str, Any]) -> str:
    return (query.get("lang") or [""])[0] or str(body.get("lang") or "") or "vi"


ROUTES: list[tuple[str, str, Handler]] = [
    ("GET", r"/api/series/(\d+)/style", lambda s, m, q, b: s.overview(int(m[1]), _lang(q, b))),
    ("PUT", r"/api/series/(\d+)/style", lambda s, m, q, b: s.save_style(int(m[1]), {**b, "lang": _lang(q, b)})),
    ("GET", r"/api/series/(\d+)/style/suggestions", lambda s, m, q, b: s.suggest(int(m[1]))),
    ("POST", r"/api/series/(\d+)/style/preview", lambda s, m, q, b: s.preview(int(m[1]), {**b, "lang": _lang(q, b)})),
    ("PUT", r"/api/series/(\d+)/cards", lambda s, m, q, b: s.save_card(int(m[1]), {**b, "lang": _lang(q, b)})),
    (
        "POST",
        r"/api/series/(\d+)/cards/delete",
        lambda s, m, q, b: s.delete_card(int(m[1]), str(b.get("name") or ""), _lang(q, b)),
    ),
    # A movie has the same pages as a series, by the movie's unit id (P12b). No suggestions: they ask TMDB for a series.
    ("GET", r"/api/movies/(\d+)/style", lambda s, m, q, b: s.movie_overview(int(m[1]), _lang(q, b))),
    ("PUT", r"/api/movies/(\d+)/style", lambda s, m, q, b: s.save_movie_style(int(m[1]), {**b, "lang": _lang(q, b)})),
    ("POST", r"/api/movies/(\d+)/style/preview", lambda s, m, q, b: s.preview_movie(int(m[1]), {**b, "lang": _lang(q, b)})),
    ("PUT", r"/api/movies/(\d+)/cards", lambda s, m, q, b: s.save_movie_card(int(m[1]), {**b, "lang": _lang(q, b)})),
    (
        "POST",
        r"/api/movies/(\d+)/cards/delete",
        lambda s, m, q, b: s.delete_movie_card(int(m[1]), str(b.get("name") or ""), _lang(q, b)),
    ),
    ("GET", r"/api/style/presets",lambda s, m, q, b: s.presets()),
    ("PUT", r"/api/style/presets/([a-z0-9-]+)", lambda s, m, q, b: s.save_preset(m[1], b)),
    ("POST", r"/api/style/presets/([a-z0-9-]+)/clone", lambda s, m, q, b: s.clone_preset(m[1], b)),
    ("POST", r"/api/style/presets/([a-z0-9-]+)/delete", lambda s, m, q, b: s.delete_preset(m[1])),
    ("GET", r"/api/units/(\d+)/style", lambda s, m, q, b: s.unit_style(int(m[1]))),
    ("PUT", r"/api/units/(\d+)/style", lambda s, m, q, b: s.save_unit_style(int(m[1]), b)),
]

_COMPILED = [(method, re.compile(rf"^{pattern}/?$"), handler) for method, pattern, handler in ROUTES]


def handles(path: str) -> bool:
    return path.startswith("/api/style/") or path.endswith(("/style", "/style/suggestions", "/style/preview", "/cards", "/cards/delete"))


def handle(
    service: StyleService, method: str, path: str, query: Query, body: dict[str, Any] | None = None
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
