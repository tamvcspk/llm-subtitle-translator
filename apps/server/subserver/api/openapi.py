"""The OpenAPI document of the console's API, exported without a running server (ADR-18 part 3, P15e).

``docs/openapi.json`` is committed. ``apps/web`` generates its TypeScript types from it (``npm run gen:api``), so a field
changed in ``api/answers`` or ``api/schemas.py`` and not exported turns CI red twice: ``tests/test_openapi.py`` compares the
file with what the code says, and the ``web`` job generates the types again and asks for no diff.

    uv run --locked --directory apps/server python -m subserver.api.openapi          # write docs/openapi.json
    uv run --locked --directory apps/server python -m subserver.api.openapi --check  # fail when it is out of date

The document is built from the routes alone (``create_app`` needs neither a database nor a service to describe them) and is
the same bytes on every machine for one lock file.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .answers.common import ErrorAnswer
from .web import create_app

# api -> subserver -> server -> apps -> the repository
DEFAULT_PATH = Path(__file__).resolve().parents[4] / "docs" / "openapi.json"


def openapi_document() -> dict[str, Any]:
    """What the app says about itself, as the console's API really behaves: FastAPI documents a ``422`` for a body it
    refuses, but the console answers ``{"error": "..."}`` with a ``400`` (and ``404``, ``409``, ``500`` where the use case
    says so), so the document says that instead and carries no name of the framework (``summary``, a derived ``title``)."""
    document = copy.deepcopy(create_app({}, None).openapi())
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        schemas.pop(name, None)
    schemas["ErrorAnswer"] = TypeAdapter(ErrorAnswer).json_schema(ref_template="#/components/schemas/{model}")
    schemas["ErrorAnswer"].pop("title", None)
    refusal = {"description": "a refusal: the status says what went wrong, `error` says it in words",
               "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorAnswer"}}}}
    for item in document.get("paths", {}).values():
        for operation in item.values():
            operation.pop("summary", None)
            responses = operation.get("responses", {})
            responses.pop("422", None)
            responses["4XX"] = refusal
    return document


def render(document: dict[str, Any] | None = None) -> str:
    """The file as written: two-space JSON, UTF-8, a final newline."""
    return json.dumps(document if document is not None else openapi_document(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH, help=f"where the document is (default: {DEFAULT_PATH})")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when the file differs from the code")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = args.out.read_bytes().decode("utf-8") if args.out.is_file() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{args.out} is out of date: run `python -m subserver.api.openapi` and commit it", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(text.encode("utf-8"))  # bytes: the same file on Windows and Linux
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
