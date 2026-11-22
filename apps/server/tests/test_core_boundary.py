"""The layers of ``subserver`` import in one direction (ADR-18, ADR-8, ADR-12, NFR-10, criterion 10a).

``subserver`` is a set of layer packages, and every module belongs to exactly one:

* ``domain``: pure decisions and the port types. Imports ``subcore`` and the standard library only.
* ``services`` and ``jobs``: the use cases, the queue and the handlers that run a job. They import ``domain``,
  each other, and the two parts of ``adapters`` the application owns (``CORE_MAY_USE``: its SQLite ``Store``
  (ADR-12) and the ``Telemetry`` sink). They never import another adapter, ``api``, an HTTP client or a socket.
* ``adapters``: what touches infrastructure (Bazarr, TMDB, the model providers, files, SQLite, the log). Imports
  ``domain`` and other adapters, never a use case, a job or ``api``.
* ``api``: the HTTP handler and its routers. They reach the queue and the database only through ``services``.
* ``runtime.py`` at the top, and ``worker.py`` beside the package, are the composition root: they may import any
  layer.

A module that sits in no layer fails here, so a new module is checked from its first commit. The violations that
exist are listed in ``KNOWN_VIOLATIONS``; the list may only shrink.

``urllib.parse`` and ``http.cookies`` are pure string handling, not clients, and stay allowed.
"""

import ast
import unittest
from pathlib import Path

PACKAGE = "subserver"
PACKAGE_DIR = Path(__file__).resolve().parent.parent / PACKAGE
LAYERS = ("domain", "services", "jobs", "adapters", "api")
COMPOSITION_ROOT = frozenset({"runtime"})  # modules directly under ``subserver``

# The adapters the core is allowed to import: the application's own storage and its telemetry sink (ADR-12).
CORE_MAY_USE = ("subserver.adapters.store", "subserver.adapters.telemetry")
# What ``api`` must not touch directly: a router asks a service (ADR-18).
API_NEVER_IMPORTS = ("subserver.adapters.store", "subserver.jobs.queue", "subserver.jobs.jobs")

FORBIDDEN_EXTERNAL = (
    "socket",
    "ssl",
    "http.client",
    "http.server",
    "urllib.request",
    "urllib.error",
    "urllib3",
    "requests",
    "httpx",
)

# (module, imported module) -> why it is allowed for now. Only ever delete entries.
KNOWN_VIOLATIONS: dict[tuple[str, str], str] = {
}


def module_name(path: Path) -> str:
    """``services.units`` for ``subserver/services/units.py`` and ``runtime`` for ``subserver/runtime.py``."""
    parts = path.relative_to(PACKAGE_DIR).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(source: str, module: str = "") -> set[str]:
    """Every module a source text may import, at any nesting depth, with relative imports resolved against
    ``module`` (``services.units``), a name relative to the package, or against the package itself when empty."""
    here = [PACKAGE] + (module.split(".")[:-1] if module else [])
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = here[: len(here) - (node.level - 1)]
                base = ".".join(anchor + ([base] if base else []))
            found.add(base)
            # `from x import y` may import the submodule x.y
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def layer_of(name: str) -> str | None:
    """The layer of a dotted ``subserver`` module name, ``""`` for the composition root, ``None`` for a stranger."""
    parts = name.split(".")
    if parts[0] != PACKAGE:
        return None
    if len(parts) == 1:
        return ""
    if parts[1] in LAYERS:
        return parts[1]
    return "" if parts[1] in COMPOSITION_ROOT else None


def under(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def external_hits(imports: set[str]) -> set[str]:
    """The forbidden packages the imports reach (``http.client`` for ``from http.client import HTTPSConnection``)."""
    return {bad for name in imports for bad in FORBIDDEN_EXTERNAL if name == bad or name.startswith(bad + ".")}


def violations_in(module: str, source: str) -> set[tuple[str, str]]:
    """The imports of ``module`` (``services.units``) that its layer does not allow."""
    first = module.split(".")[0]
    layer = first if first in LAYERS else ""
    imports = imported_modules(source, module)
    out: set[tuple[str, str]] = set()
    for name in imports:
        target = layer_of(name)
        parts = name.split(".")
        if target is None or (len(parts) == 2 and parts[1] in LAYERS):
            continue  # not ours, or a bare layer package: `from ..adapters import http` names the module as well
        name = ".".join(parts[:3])  # the module, not the class or function imported from it
        if layer == "domain" and target != "domain":
            out.add((module, name))
        elif layer in ("services", "jobs") and (
            target in ("api", "") or (target == "adapters" and not under(name, CORE_MAY_USE))
        ):
            out.add((module, name))
        elif layer == "adapters" and target not in ("domain", "adapters"):
            out.add((module, name))
        elif layer == "api" and under(name, API_NEVER_IMPORTS):
            out.add((module, name))
    if layer in ("domain", "services", "jobs"):
        out.update((module, bad) for bad in external_hits(imports))
    return out


def package_files() -> list[Path]:
    return [p for p in sorted(PACKAGE_DIR.rglob("*.py")) if "__pycache__" not in p.parts]


def package_violations() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in package_files():
        name = module_name(path)
        if name.split(".")[0] in LAYERS and path.name != "__init__.py":
            found |= violations_in(name, path.read_text(encoding="utf-8"))
    return found


class LayerBoundaryTests(unittest.TestCase):
    def test_every_module_sits_in_a_layer(self):
        strangers = [
            module_name(p) for p in package_files()
            if module_name(p) not in ("", "__init__") and module_name(p) not in COMPOSITION_ROOT
            and module_name(p).split(".")[0] not in LAYERS
        ]
        self.assertFalse(strangers, f"module outside the layers {LAYERS} (ADR-18); put it in one: {strangers}")

    def test_no_layer_imports_what_it_must_not(self):
        new = {v for v in package_violations() if v not in KNOWN_VIOLATIONS}
        self.assertFalse(new, f"a layer imports what ADR-18 forbids it (see the docstring of this file): {sorted(new)}")

    def test_exception_list_only_shrinks(self):
        stale = set(KNOWN_VIOLATIONS) - package_violations()
        self.assertFalse(stale, f"no longer a violation, delete from KNOWN_VIOLATIONS: {sorted(stale)}")

    def test_entry_point_wires_adapters_but_touches_no_infrastructure_itself(self):
        """``worker.py`` is the process's composition root: it may import any layer, but sockets and the
        stdlib HTTP server stay inside the adapters and ``api``."""
        source = (PACKAGE_DIR.parent / "worker.py").read_text(encoding="utf-8")
        direct = external_hits(imported_modules(source))
        self.assertFalse(direct, f"worker.py imports infrastructure directly; put it behind an adapter: {sorted(direct)}")

    def test_composition_root_modules_are_real(self):
        names = {path.stem for path in PACKAGE_DIR.glob("*.py")}
        self.assertFalse(COMPOSITION_ROOT - names)


def store_methods() -> list[tuple[str, ast.FunctionDef]]:
    """The methods of every class under ``adapters/store`` (the ``Store`` facade is assembled from them)."""
    out = []
    for path in sorted((PACKAGE_DIR / "adapters" / "store").glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef):
                out.extend((f"{node.name}.{fn.name}", fn) for fn in node.body if isinstance(fn, ast.FunctionDef))
    return out


class PortTypeTests(unittest.TestCase):
    """Criterion 10c: a port returns named types, never ``dict`` or ``sqlite3.Row``."""

    BANNED = {"dict", "Dict", "Mapping", "MutableMapping", "Row", "Any", "object"}
    MAPPINGS = {"dict", "Dict", "Mapping", "MutableMapping"}

    @classmethod
    def offends(cls, returns: ast.AST) -> bool:
        """``dict[int, StoredFacts]`` (an index of named values) is fine; a bare dict or ``dict[str, Any]`` is not."""
        allowed: set[int] = set()
        for node in ast.walk(returns):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in cls.MAPPINGS:
                args = node.slice.elts if isinstance(node.slice, ast.Tuple) else []
                value = args[1] if len(args) == 2 else None
                value_names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)} if value is not None else {"Any"}
                if value is not None and not value_names & cls.BANNED:
                    allowed.add(id(node.value))
        for node in ast.walk(returns):
            if isinstance(node, ast.Name) and node.id in cls.BANNED and id(node) not in allowed:
                return True
            if isinstance(node, ast.Attribute) and node.attr in cls.BANNED:
                return True
        return False

    def test_no_port_method_returns_a_dict_like_type(self):
        tree = ast.parse((PACKAGE_DIR / "domain" / "ports.py").read_text(encoding="utf-8"))
        offenders = []
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
                if fn.returns is None:
                    continue
                returns = fn.returns
                if isinstance(returns, ast.Constant) and isinstance(returns.value, str):  # "dict[str, Any]"
                    returns = ast.parse(returns.value, mode="eval").body
                if self.offends(returns):
                    offenders.append(f"{cls.name}.{fn.name}")
        self.assertFalse(offenders, f"port methods must return named types: {offenders}")

    # Low-level helpers of the store itself, used by ``JobStore`` for its own SQL; every other public method is a
    # repository method and must return a named type (ADR-12).
    STORE_HELPERS = {"query", "transaction"}

    def test_no_store_method_returns_a_dict_like_type(self):
        methods = store_methods()
        self.assertGreater(len(methods), 100, "the scan found no Store methods; did the package move?")
        offenders = [
            name
            for name, fn in methods
            if not fn.name.startswith("_") and fn.name not in self.STORE_HELPERS
            and fn.returns is not None and self.offends(fn.returns)
        ]
        self.assertFalse(offenders, f"Store methods must return named types: {offenders}")

    def test_the_check_itself_tells_records_from_indexes(self):
        def bad(annotation):
            return self.offends(ast.parse(annotation, mode="eval").body)

        self.assertTrue(bad("dict"))
        self.assertTrue(bad("dict[str, Any]"))
        self.assertFalse(bad("list[dict[str, int]]"))  # int values: a plain index, not a record
        self.assertTrue(bad("list[dict[str, Any]]"))
        self.assertTrue(bad("sqlite3.Row"))
        self.assertTrue(bad("Mapping[str, object]"))
        self.assertFalse(bad("dict[int, StoredFacts]"))
        self.assertFalse(bad("list[Account] | None"))


class ScannerTests(unittest.TestCase):
    """The scanner itself, so a quiet pass cannot come from a scanner that sees nothing."""

    def hits(self, source, module="services.some_use_case"):
        return {name for _, name in violations_in(module, source)}

    def test_sees_top_level_and_nested_imports(self):
        self.assertEqual(self.hits("import socket"), {"socket"})
        self.assertEqual(self.hits("def f():\n    import socket\n"), {"socket"})

    def test_resolves_relative_and_absolute_adapter_imports(self):
        self.assertEqual(self.hits("from ..adapters.bazarr import BazarrMedia"), {"subserver.adapters.bazarr"})
        self.assertEqual(self.hits("from subserver.adapters.tmdb import TmdbMetadata"), {"subserver.adapters.tmdb"})
        self.assertEqual(self.hits("from ..adapters import http"), {"subserver.adapters.http"})
        self.assertEqual(self.hits("def f():\n    from ..adapters.http import first_data\n"), {"subserver.adapters.http"})

    def test_a_relative_import_is_resolved_from_the_modules_own_package(self):
        self.assertEqual(self.hits("from .units import UnitService"), set())  # same layer
        self.assertEqual(self.hits("from . import units"), set())
        self.assertEqual(self.hits("from ..api import web", "jobs.queue"), {"subserver.api.web"})
        self.assertEqual(self.hits("from ..adapters import web", "domain.rules"), {"subserver.adapters.web"})

    def test_sees_http_clients(self):
        self.assertEqual(self.hits("import urllib.request"), {"urllib.request"})
        self.assertEqual(self.hits("from urllib import request"), {"urllib.request"})
        self.assertEqual(self.hits("from http.client import HTTPSConnection"), {"http.client"})

    def test_allows_pure_modules_and_the_applications_own_storage(self):
        source = (
            "from urllib.parse import urlsplit\nfrom http.cookies import SimpleCookie\nfrom ..domain.rules import x\n"
            "import json\nimport sqlite3\nfrom ..adapters.store import Store\nfrom ..adapters.telemetry import NULL\n"
            "from ..jobs.queue import JobQueue\n"
        )
        self.assertEqual(self.hits(source), set())  # the application's own SQLite is core (ADR-12)

    def test_domain_imports_domain_and_subcore_only(self):
        self.assertEqual(self.hits("from .ports import X\nimport subcore.srt", "domain.rules"), set())
        self.assertEqual(self.hits("from ..adapters.store import Store", "domain.rules"), {"subserver.adapters.store"})
        self.assertEqual(self.hits("from ..services import units", "domain.rules"), {"subserver.services.units"})

    def test_adapters_do_not_import_use_cases(self):
        self.assertEqual(self.hits("from ..services.units import UnitService", "adapters.settings"), {"subserver.services.units"})
        self.assertEqual(self.hits("from ..domain.ports import X\nimport socket", "adapters.http"), set())

    def test_api_asks_services_not_the_store_or_the_queue(self):
        self.assertEqual(self.hits("from ..services.units import UnitService", "api.web"), set())
        self.assertEqual(self.hits("from ..adapters.store import Store", "api.web"), {"subserver.adapters.store"})
        self.assertEqual(self.hits("from ..jobs.queue import JobQueue", "api.web"), {"subserver.jobs.queue"})
        self.assertEqual(self.hits("import http.server", "api.web"), set())  # the HTTP handler is the adapter until P15d

    def test_a_layer_is_told_from_a_stranger(self):
        self.assertEqual(layer_of("subserver.services.units"), "services")
        self.assertEqual(layer_of("subserver.runtime"), "")
        self.assertIsNone(layer_of("subserver.units"))
        self.assertIsNone(layer_of("subcore.srt"))


if __name__ == "__main__":
    unittest.main()
