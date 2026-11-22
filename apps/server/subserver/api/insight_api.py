"""HTTP routes of Insights and the golden set (docs/API.md, P15). Pure routing, like ``suggestion_api``: ``handle`` maps a
request to a call and returns ``(status, payload)``, or ``None`` when the path is not one of these routes.

* ``GET /api/metrics?days=30``: the figures of FR-19.5, from SQLite.
* ``GET /api/series/{id}/golden?lang=vi``: the corrected cues of a series as a golden set (FR-19.6); ``download``
  serves the same text as a file for ``subcore compare --golden``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import Response

from ..services.feedback import FeedbackProposer
from ..services.metrics import MAX_DAYS, Metrics
from .common import path_parameters, blocking, ctx_of, download_response, error_response, json_response, query_of

Query = dict[str, list[str]]
_GOLDEN = re.compile(r"^/api/series/(\d+)/golden/?$")
_LANG = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass
class Insights:
    metrics: Metrics
    feedback: FeedbackProposer


def _one(query: Query, name: str) -> str:
    return (query.get(name) or [""])[0]


def _days(query: Query) -> int:
    raw = _one(query, "days")
    if not raw:
        return 30
    try:
        days = int(raw)
    except ValueError:
        raise ValueError("days must be a number") from None
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")
    return days


def _lang(query: Query) -> str:
    lang = _one(query, "lang")
    if not _LANG.match(lang):
        raise ValueError("lang is required: the language of the translations, such as vi")
    return lang


def handles(path: str) -> bool:
    return path == "/api/metrics" or bool(_GOLDEN.match(path))


def register(router: APIRouter) -> None:
    """``GET /api/metrics`` and ``GET /api/series/{id}/golden`` (a file when ``download=1``)."""

    async def metrics(request: Request) -> Response:
        service = ctx_of(request).get("insights")
        result = None if service is None else await blocking(handle, service, "GET", request.url.path, query_of(request))
        if result is None:
            return error_response("not found", status=404)
        return json_response(result[1], status=result[0])

    async def golden(request: Request) -> Response:
        service = ctx_of(request).get("insights")
        if service is None:
            return error_response("not found", status=404)
        query = query_of(request)
        exported = await blocking(download, service, request.url.path, query)
        if exported is not None:
            status, name, text = exported
            if status != 200:
                return error_response(text, status=status)
            return download_response(text, "application/json; charset=utf-8", name)
        return await metrics(request)

    router.add_api_route("/api/metrics", metrics, methods=["GET"])
    router.add_api_route(
        "/api/series/{series_id:int}/golden", golden, methods=["GET"],
        openapi_extra={"parameters": path_parameters("/api/series/{series_id:int}/golden")},
    )


def handle(service: Insights, method: str, path: str, query: Query, body: dict[str, Any] | None = None) -> tuple[int, Any] | None:
    if method != "GET":
        return None
    try:
        if path == "/api/metrics":
            return 200, service.metrics.report(_days(query))
        found = _GOLDEN.match(path)
        if found:
            _, text = service.feedback.golden(int(found[1]), _lang(query))
            return 200, json.loads(text)
    except LookupError as exc:
        return 404, {"error": str(exc.args[0]) if exc.args else "not found"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return None


def download(service: Insights, path: str, query: Query) -> tuple[int, str, str] | None:
    """``(status, file name, text)`` of a golden set asked for with ``download=1``, or ``None`` for any other request."""
    found = _GOLDEN.match(path)
    if not found or not _one(query, "download"):
        return None
    try:
        name, text = service.feedback.golden(int(found[1]), _lang(query))
    except LookupError as exc:
        return 404, "", str(exc.args[0]) if exc.args else "not found"
    except ValueError as exc:
        return 400, "", str(exc)
    return 200, name, text
