"""What every route of the console shares (ADR-18, P15d): the JSON answer and its error shape, the request body, the
route class that turns an exception into the answer the console has always given, and the table-driven routes.

The error shape is part of the contract with ``apps/web``: ``{"error": "<message>"}`` with a 400 for a ``ValueError``,
a 404 for a ``LookupError`` that a pure router maps, and a 500 (logged) for anything else.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any, Awaitable, Callable, Optional

from fastapi import Body, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute, APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import Response

from .schemas import RequestObject

JSON_TYPE = "application/json; charset=utf-8"
BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

Query = dict[str, list[str]]
# The one route whose body is a file, not JSON.
RAW_BODY_PATHS = frozenset({"/api/backups/import"})


def json_response(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Response(body, status_code=status, headers=headers, media_type=JSON_TYPE)


def error_response(message: str, status: int = 400, headers: dict[str, str] | None = None) -> Response:
    return json_response({"error": message}, status=status, headers=headers)


def download_response(text: str, content_type: str, filename: str) -> Response:
    """Text served as a download (the name is the caller's, already reduced to ``[a-z0-9._-]``)."""
    return Response(
        text.encode("utf-8"), media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


def file_answer(content_type: str) -> dict[int | str, dict[str, Any]]:
    """The OpenAPI entry of a route that answers a file (a download), so it is in the document without a JSON model."""
    return {200: {"description": "the file", "content": {content_type: {"schema": {"type": "string", "format": "binary"}}}}}


def validation_message(exc: RequestValidationError) -> str:
    """A refused body in the console's words: the field and what is wrong with it (``dry_run: Input should be a valid boolean``)."""
    parts = []
    for error in exc.errors():
        if error.get("type") == "json_invalid":  # its location is a character position, which tells a person nothing
            parts.append("the request body is not valid JSON")
            continue
        where = ".".join(str(part) for part in error.get("loc", ())[1:])  # the first part is "body"
        parts.append(f"{where}: {error.get('msg', 'invalid')}" if where else str(error.get("msg", "invalid request")))
    return "; ".join(parts) or "invalid request"


def payload(body: BaseModel | None) -> dict[str, Any]:
    """What the client sent, as the dict the use cases read (a field it left out stays out); no body is ``{}``."""
    return {} if body is None else body.model_dump(exclude_unset=True)


async def binary_body(request: Request) -> bytes:
    raw = await request.body()
    if not raw:
        raise ValueError("request body is empty")
    return raw


def query_of(request: Request) -> Query:
    return urllib.parse.parse_qs(request.url.query)


def ctx_of(request: Request) -> dict[str, Any]:
    """The runtime context of the process (``RuntimeContext.as_handler_context``)."""
    return request.app.state.ctx


async def blocking(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run synchronous work (SQLite, a call to Bazarr) on the thread pool, never on the event loop."""
    return await run_in_threadpool(function, *args, **kwargs)


class ConsoleRoute(APIRoute):
    """Every route answers an exception the way the console always did, so ``apps/web`` sees no change."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            if request.method in BODY_METHODS and self.path not in RAW_BODY_PATHS:
                # The console has always read a body as JSON whatever its Content-Type (``curl -d`` sends a form type).
                headers = [(k, v) for k, v in request.scope["headers"] if k != b"content-type"]
                request.scope["headers"] = [*headers, (b"content-type", b"application/json")]
            try:
                return await original(request)
            except HTTPException:
                raise
            except RequestValidationError as exc:  # a body that does not fit its model: a 400 in the console's shape
                return error_response(validation_message(exc), status=400)
            except ValueError as exc:
                return error_response(str(exc), status=400)
            except Exception as exc:  # noqa: BLE001
                logging.exception("%s %s failed", request.method, request.url.path)
                return error_response(str(exc), status=500)

        return handler


# --- routes that are a table of (method, regex, handler) -------------------------------------------------------------

_PARAMETER = re.compile(r"\((?:\\d\+|\[a-z0-9-\]\+)\)")


def to_template(pattern: str) -> str:
    """``/api/units/(\\d+)/cues`` as the path FastAPI routes on: ``/api/units/{p1:int}/cues``. The pure routers keep
    their regex tables (the one place a route is written down); this derives the route from it."""
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{{p{count}:int}}" if match[0] == r"(\d+)" else f"{{p{count}}}"

    template = _PARAMETER.sub(replace, pattern)
    if "(" in template:
        raise ValueError(f"route {pattern!r} has a group that to_template does not know")
    return template


# What a table's `handle` needs besides the request: it is given the service, the method, the path, the query and the body.
Invoke = Callable[[dict[str, Any], Any, str, str, Query, dict[str, Any] | None], "tuple[int, Any] | None"]


def path_parameters(template: str) -> list[dict[str, Any]]:
    """The path parameters of a route template, for the OpenAPI document (the routes read the path themselves)."""
    found = []
    for name, kind in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([a-z]+))?\}", template):
        found.append({"name": name, "in": "path", "required": True, "schema": {"type": "integer" if kind == "int" else "string"}})
    return found


def query(name: str, kind: str = "string", *, required: bool = False) -> dict[str, Any]:
    """One query parameter of a route, for the OpenAPI document (the routes read the query themselves, in their own words)."""
    return {"name": name, "in": "query", "required": required, "schema": {"type": kind}}


def parameters_of(template: str, queries: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The ``openapi_extra`` of a route: its path parameters and its query parameters (``None`` when it has neither)."""
    found = [*path_parameters(template), *(queries or [])]
    return {"parameters": found} if found else None


def add_table_routes(
    router: APIRouter, routes: list[tuple[str, str, Any]], service_key: str, invoke: Invoke,
    bodies: dict[tuple[str, str], type[BaseModel]] | None = None,
    answers: dict[tuple[str, str], Any] | None = None,
    queries: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> None:
    """One FastAPI route for each row of a pure router's table. The row is served by that router's own ``handle`` (so
    its validation and its error mapping stay as they were); a missing service or an answer of ``None`` is a 404. A row
    that takes a body has a model in ``bodies`` (any JSON object when it has none); a row that answers JSON has its model
    in ``answers`` (api/answers), which documents the answer and checks nothing at run time; the query parameters a row
    reads are in ``queries`` (``query(...)``), for the same document."""
    bodies = bodies or {}
    answers = answers or {}
    queries = queries or {}
    for method, pattern, _ in routes:
        template = to_template(pattern)

        def make(method: str = method, pattern: str = pattern) -> Callable[..., Awaitable[Response]]:
            async def serve(request: Request, body: BaseModel | None) -> Response:
                ctx = ctx_of(request)
                service = ctx.get(service_key)
                if service is None:
                    return error_response("not found", status=404)
                result = await blocking(invoke, ctx, service, method, request.url.path, query_of(request), payload(body))
                if result is None:
                    return error_response("not found", status=404)
                return json_response(result[1], status=result[0])

            if method not in BODY_METHODS:
                async def endpoint(request: Request) -> Response:
                    return await serve(request, None)
            else:
                async def endpoint(request: Request, body: Any = Body(default=None)) -> Response:
                    return await serve(request, body)

                # The model is chosen per row, so the annotation is set here, not written in the signature.
                endpoint.__annotations__ = {
                    "request": Request, "body": Optional[bodies.get((method, pattern), RequestObject)], "return": Response,
                }
            return endpoint

        router.add_api_route(
            template, make(), methods=[method], response_model=answers.get((method, pattern)),
            openapi_extra=parameters_of(template, queries.get((method, pattern))),
        )
