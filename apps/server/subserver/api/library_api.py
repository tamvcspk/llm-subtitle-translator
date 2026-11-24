"""HTTP routes for the library, episodes, corrections, versions and glossaries (docs/API.md).

Pure routing: ``handle`` maps a request to a ``UnitService`` call and returns ``(status, payload)``,
or ``None`` when the path is not one of these routes. ``register`` puts the table on the FastAPI app (one route per row).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Union

from fastapi import APIRouter, Request
from starlette.responses import Response

from ..services.units import UnitService
from . import schemas
from .answers import common as shared
from .answers import library as ans
from .common import (
    add_table_routes, blocking, ctx_of, download_response, error_response, file_answer, parameters_of, query, query_of,
)

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


BODIES = {
    ("PUT", r"/api/series/(\d+)/glossary"): schemas.GlossarySaveBody,
    ("PUT", r"/api/movies/(\d+)/glossary"): schemas.GlossarySaveBody,
    ("POST", r"/api/series/(\d+)/glossary/restore"): schemas.GlossaryRestoreBody,
    ("POST", r"/api/movies/(\d+)/glossary/restore"): schemas.GlossaryRestoreBody,
    ("POST", r"/api/series/(\d+)/glossary/copy"): schemas.GlossaryCopyBody,
    ("POST", r"/api/units/(\d+)/versions/(\d+)/adopt"): schemas.AdoptBody,
    # The three that spend model calls: ``estimate_only`` is a real boolean or a 400 (a queued job is not an estimate).
    ("POST", r"/api/units/(\d+)/retranslate"): schemas.EstimateBody,
    ("POST", r"/api/units/(\d+)/variant"): schemas.EstimateBody,
    ("POST", r"/api/units/(\d+)/versions/(\d+)/judge"): schemas.JudgeBody,
}


ANSWERS = {
    ("GET", r"/api/library/series"): ans.LibrarySeriesAnswer,
    ("GET", r"/api/library/series/(\d+)/units"): ans.LibraryUnits,
    ("GET", r"/api/library/movies"): ans.LibraryMovies,
    ("GET", r"/api/units/(\d+)"): ans.UnitDetail,
    ("GET", r"/api/units/(\d+)/cues"): ans.CuesPage,
    ("GET", r"/api/units/(\d+)/corrections"): ans.CorrectionsAnswer,
    ("POST", r"/api/units/(\d+)/corrections"): ans.CorrectionAdded,
    ("POST", r"/api/units/(\d+)/corrections/(\d+)/remove"): shared.OkAnswer,
    ("POST", r"/api/units/(\d+)/retranslate"): Union[ans.RetranslateQueued, ans.RetranslateEstimate],
    ("POST", r"/api/units/(\d+)/variant"): Union[ans.VariantQueued, ans.VariantEstimate],
    ("GET", r"/api/units/(\d+)/versions/(\d+)/compare"): ans.CompareAnswer,
    ("POST", r"/api/units/(\d+)/versions/(\d+)/choose"): ans.ChooseAnswer,
    ("POST", r"/api/units/(\d+)/versions/(\d+)/judge"): Union[ans.JudgeQueued, ans.JudgeEstimate],
    ("GET", r"/api/units/(\d+)/versions"): ans.VersionsAnswer,
    ("GET", r"/api/units/(\d+)/versions/(\d+)/diff"): ans.DiffAnswer,
    ("POST", r"/api/units/(\d+)/versions/(\d+)/revert"): ans.RevertAnswer,
    ("POST", r"/api/units/(\d+)/versions/(\d+)/adopt"): ans.AdoptAnswer,
    ("POST", r"/api/units/(\d+)/output/remove"): ans.RemoveOutputAnswer,
    ("GET", r"/api/units/(\d+)/context"): ans.UnitContextAnswer,
    ("GET", r"/api/units/(\d+)/facts"): ans.UnitFacts,
    ("POST", r"/api/units/(\d+)/context/extract"): ans.ExtractAnswer,
    ("GET", r"/api/series/(\d+)/memory"): ans.SeriesMemory,
    ("PUT", r"/api/series/(\d+)/memory"): ans.SeriesMemory,
    ("POST", r"/api/series/(\d+)/memory/adopt"): ans.AdoptEpisodesAnswer,
    ("GET", r"/api/series/(\d+)/glossary"): ans.GlossaryAnswer,
    ("PUT", r"/api/series/(\d+)/glossary"): ans.GlossarySave,
    ("GET", r"/api/series/(\d+)/glossary/history"): ans.GlossaryHistory,
    ("POST", r"/api/series/(\d+)/glossary/restore"): ans.GlossaryRestore,
    ("POST", r"/api/series/(\d+)/glossary/copy"): ans.GlossaryCopy,
    ("GET", r"/api/movies/(\d+)/glossary"): ans.GlossaryAnswer,
    ("PUT", r"/api/movies/(\d+)/glossary"): ans.GlossarySave,
    ("GET", r"/api/movies/(\d+)/glossary/history"): ans.GlossaryHistory,
    ("POST", r"/api/movies/(\d+)/glossary/restore"): ans.GlossaryRestore,
}


# What each route reads from the query, for the OpenAPI document. ``lang`` is a target language code: optional where the
# unit has one target, required for a glossary (a glossary is written in one language).
_STATUS = [query("status")]
_LANG = [query("lang")]
_GLOSSARY_LANG = [query("lang", required=True)]
QUERIES = {
    ("GET", r"/api/library/series/(\d+)/units"): _STATUS,
    ("GET", r"/api/library/movies"): _STATUS,
    ("GET", r"/api/units/(\d+)/cues"): [
        query("lang"), query("at"), query("q"), query("offset", "integer"), query("limit", "integer"),
    ],
    ("GET", r"/api/units/(\d+)/corrections"): _LANG,
    ("GET", r"/api/units/(\d+)/versions/(\d+)/compare"): _LANG,
    ("GET", r"/api/units/(\d+)/versions"): _LANG,
    ("GET", r"/api/units/(\d+)/versions/(\d+)/diff"): [query("against", "integer"), query("lang")],
    ("POST", r"/api/units/(\d+)/versions/(\d+)/revert"): _LANG,
    ("POST", r"/api/units/(\d+)/versions/(\d+)/adopt"): _LANG,
    ("POST", r"/api/units/(\d+)/output/remove"): _LANG,
    ("GET", r"/api/units/(\d+)/context"): _LANG,
    ("GET", r"/api/series/(\d+)/glossary"): _GLOSSARY_LANG,
    ("PUT", r"/api/series/(\d+)/glossary"): _GLOSSARY_LANG,
    ("GET", r"/api/series/(\d+)/glossary/history"): _GLOSSARY_LANG,
    # Restore and copy read the language from their body (the console sends it there), the query is the other way to say it.
    ("POST", r"/api/series/(\d+)/glossary/restore"): _LANG,
    ("POST", r"/api/series/(\d+)/glossary/copy"): _LANG,
    ("GET", r"/api/movies/(\d+)/glossary"): _GLOSSARY_LANG,
    ("PUT", r"/api/movies/(\d+)/glossary"): _GLOSSARY_LANG,
    ("GET", r"/api/movies/(\d+)/glossary/history"): _GLOSSARY_LANG,
    ("POST", r"/api/movies/(\d+)/glossary/restore"): _LANG,
}


def register(router: APIRouter) -> None:
    """The routes of this module on a FastAPI router: the table, and the glossary as a file. A body no model names is any
    JSON object: the use case validates it."""
    add_table_routes(
        router, ROUTES, "units", lambda ctx, service, method, path, query, body: handle(service, method, path, query, body),
        BODIES, ANSWERS, QUERIES,
    )

    async def glossary_file(request: Request) -> Response:
        service = ctx_of(request).get("units")
        exported = None if service is None else await blocking(download, service, request.url.path, query_of(request))
        if exported is None:
            return error_response("not found", status=404)
        status, name, text = exported
        if status != 200:
            return error_response(text, status=status)
        return download_response(text, "application/x-yaml; charset=utf-8", name)

    for kind in ("series", "movies"):
        template = f"/api/{kind}/{{unit_id:int}}/glossary.yaml"
        router.add_api_route(
            template, glossary_file, methods=["GET"], response_class=Response, responses=file_answer("application/x-yaml"),
            openapi_extra=parameters_of(template, _GLOSSARY_LANG),
        )


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
