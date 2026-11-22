#!/usr/bin/env python3
"""Check that the docs name what the code has (standard library only).

Two checks, both one-directional (code -> docs; a doc may keep history):

* every module of ``apps/server/subserver`` is named in ``docs/ARCHITECTURE.md`` as ``<name>.py``, and every module of
  ``packages/subcore/src/subcore`` as ``subcore.<dotted.name>`` (a package as ``subcore.<package>``);
* every ``/api/...`` route found as a string literal in ``apps/server/subserver`` and ``worker.py`` appears in
  ``docs/API.md`` (``{name}`` and a regex group are the same placeholder; a query string is ignored).

Run from anywhere: ``python scripts/doccheck.py``. Exit status 1 and one line per gap when something is missing.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "apps" / "server"
SUBCORE = ROOT / "packages" / "subcore" / "src" / "subcore"


def server_modules(package: Path = SERVER / "subserver") -> list[str]:
    return sorted(p.stem for p in package.glob("*.py") if p.name != "__init__.py")


def subcore_modules(package: Path = SUBCORE) -> list[str]:
    """Dotted names under ``subcore``: every module and every sub-package (``memory``, ``translate``, ...)."""
    names: set[str] = set()
    for path in package.rglob("*.py"):
        if path.name == "__main__.py":
            continue
        relative = path.relative_to(package).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            names.add(".".join(parts))
    return sorted(names)


def missing_modules(architecture: str, server: list[str], subcore: list[str]) -> list[str]:
    problems = []
    for name in server:
        if not re.search(rf"(?<![\w.]){re.escape(name)}\.py\b", architecture):
            problems.append(f"docs/ARCHITECTURE.md does not name subserver/{name}.py")
    for name in subcore:
        if not re.search(rf"subcore\.{re.escape(name)}(?![\w])", architecture):
            problems.append(f"docs/ARCHITECTURE.md does not name subcore.{name}")
    return problems


def normalize_route(text: str) -> str | None:
    """``/api/units/(\\d+)/cues`` and ``/api/units/{id}/cues?lang=`` both become ``/api/units/{}/cues``."""
    text = text.strip().lstrip("^")
    if not text.startswith("/api/"):
        return None
    text = re.sub(r"\([^()]*\)\?", "", text)  # an optional group, e.g. ``(/test|/remove)?``
    text = text.replace("/?", "").rstrip("$")
    text = re.sub(r"\([^()]*\)", "{}", text)  # a regex group is a placeholder
    text = re.sub(r"\{[^{}]*\}", "{}", text)
    text = text.split("?", 1)[0].rstrip("/")
    if not re.fullmatch(r"/api(/[A-Za-z0-9_.{}-]+)+", text):
        return None
    return text


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def code_routes(source: str) -> set[str]:
    """The ``/api/...`` string literals of one module (docstrings and f-strings left out)."""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):  # f"{bazarr_url}/api/series": a call to Bazarr, not a route of this worker
        if isinstance(node, ast.JoinedStr):
            skip |= {id(part) for part in node.values}
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in skip:
            continue
        value = node.value
        if value.endswith("/") and not value.endswith("/?"):  # a prefix (``startswith("/api/jobs/")``)
            continue
        route = normalize_route(value)
        if route:
            routes.add(route)
    return routes


def doc_routes(api_doc: str) -> set[str]:
    found: set[str] = set()
    for match in re.finditer(r"/api(?:/[A-Za-z0-9_.{}-]+)+", api_doc):
        route = normalize_route(match.group(0))
        if route:
            found.add(route)
    return found


def missing_routes(routes: set[str], documented: set[str]) -> list[str]:
    return [f"docs/API.md does not mention {route}" for route in sorted(routes - documented)]


def check(root: Path = ROOT) -> list[str]:
    architecture = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    api_doc = (root / "docs" / "API.md").read_text(encoding="utf-8")
    server = root / "apps" / "server"
    sources = sorted((server / "subserver").glob("*.py")) + [server / "worker.py"]
    routes: set[str] = set()
    for path in sources:
        routes |= code_routes(path.read_text(encoding="utf-8"))
    problems = missing_modules(
        architecture,
        server_modules(server / "subserver"),
        subcore_modules(root / "packages" / "subcore" / "src" / "subcore"),
    )
    problems += missing_routes(routes, doc_routes(api_doc))
    return problems


def main() -> int:
    problems = check()
    for line in problems:
        print(line)
    if problems:
        print(f"doccheck: {len(problems)} gap(s)", file=sys.stderr)
        return 1
    print("doccheck: docs name every module and every route")
    return 0


if __name__ == "__main__":
    sys.exit(main())
