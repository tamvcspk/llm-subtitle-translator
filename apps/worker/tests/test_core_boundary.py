"""Core of the worker must not import infrastructure (SPEC ADR-8, NFR-10, criterion 10a).

Every module in ``gst_worker`` is *core* unless it is listed as an adapter or the composition root, so a
new module is checked by default. A core module may not import ``sqlite3``, an HTTP client, or an adapter
module. The violations that exist today are listed in ``KNOWN_VIOLATIONS`` with the P0d step that removes
them. The list may only shrink: a new violation fails the test, and so does an entry that no longer
occurs (delete it), until the list is empty and P0d part 1 is done.

``urllib.parse`` and ``http.cookies`` are pure string handling, not clients, and stay allowed.
"""

import ast
import unittest
from pathlib import Path

PACKAGE = "gst_worker"
PACKAGE_DIR = Path(__file__).resolve().parent.parent / PACKAGE

# Infrastructure: implements a port (or will) and may touch storage, files, sockets.
ADAPTERS = frozenset({"store", "queue", "http", "bazarr", "tmdb", "gemini", "llm", "backups", "connection_tests"})
# Wires adapters into the core; the only place allowed to know all of them.
COMPOSITION_ROOT = frozenset({"runtime"})

FORBIDDEN_EXTERNAL = (
    "sqlite3",
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

# (core module, imported module) -> the P0d step that removes it. Only ever delete entries.
KNOWN_VIOLATIONS = {
    ("console", "gst_worker.queue"): "JobQueue port + use case layer",
    ("library", "gst_worker.store"): "CatalogRepo",
    ("library", "gst_worker.http"): "MediaLibrary port (lazy import of first_data)",
    ("memory", "gst_worker.store"): "MemoryRepo",
    ("translation_attempt", "gst_worker.llm"): "ProviderFactory port",
    ("units", "gst_worker.llm"): "ProviderFactory port",
    ("units", "gst_worker.queue"): "JobQueue port",
}


def imported_modules(source: str) -> set[str]:
    """Every module a source text may import, at any nesting depth, with relative imports resolved."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = f"{PACKAGE}.{base}" if base else PACKAGE
            found.add(base)
            # `from x import y` may import the submodule x.y
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def violations_in(module: str, source: str) -> set[tuple[str, str]]:
    adapter_names = {f"{PACKAGE}.{name}" for name in ADAPTERS | COMPOSITION_ROOT}
    out = set()
    for name in imported_modules(source):
        if name in adapter_names:
            out.add((module, name))
        else:
            out.update((module, bad) for bad in FORBIDDEN_EXTERNAL if name == bad or name.startswith(bad + "."))
    return out


def core_violations() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        name = path.stem
        if name == "__init__" or name in ADAPTERS or name in COMPOSITION_ROOT:
            continue
        found |= violations_in(name, path.read_text(encoding="utf-8"))
    return found


class CoreBoundaryTests(unittest.TestCase):
    def test_core_has_no_new_infrastructure_import(self):
        new = core_violations() - set(KNOWN_VIOLATIONS)
        self.assertFalse(new, f"core module imports infrastructure; use a port instead (ADR-8): {sorted(new)}")

    def test_exception_list_only_shrinks(self):
        stale = set(KNOWN_VIOLATIONS) - core_violations()
        self.assertFalse(stale, f"no longer a violation, delete from KNOWN_VIOLATIONS: {sorted(stale)}")

    def test_adapter_and_root_names_are_real_modules(self):
        names = {path.stem for path in PACKAGE_DIR.glob("*.py")}
        self.assertFalse((ADAPTERS | COMPOSITION_ROOT) - names)


class PortTypeTests(unittest.TestCase):
    """Criterion 10c: a port returns named types, never ``dict`` or ``sqlite3.Row``."""

    BANNED = {"dict", "Dict", "Mapping", "MutableMapping", "Row", "Any", "object"}

    def test_no_port_method_returns_a_dict_like_type(self):
        tree = ast.parse((PACKAGE_DIR / "ports.py").read_text(encoding="utf-8"))
        offenders = []
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
                if fn.returns is None:
                    continue
                names = {n.id for n in ast.walk(fn.returns) if isinstance(n, ast.Name)}
                names |= {n.attr for n in ast.walk(fn.returns) if isinstance(n, ast.Attribute)}
                # string annotations ("dict[str, Any]") are parsed too
                for const in (n for n in ast.walk(fn.returns) if isinstance(n, ast.Constant) and isinstance(n.value, str)):
                    names |= {n.id for n in ast.walk(ast.parse(const.value, mode="eval")) if isinstance(n, ast.Name)}
                if names & self.BANNED:
                    offenders.append(f"{cls.name}.{fn.name}")
        self.assertFalse(offenders, f"port methods must return named types: {offenders}")


class ScannerTests(unittest.TestCase):
    """The scanner itself, so a quiet pass cannot come from a scanner that sees nothing."""

    def hits(self, source):
        return {name for _, name in violations_in("core_module", source)}

    def test_sees_top_level_and_nested_imports(self):
        self.assertEqual(self.hits("import sqlite3"), {"sqlite3"})
        self.assertEqual(self.hits("def f():\n    import sqlite3\n"), {"sqlite3"})

    def test_resolves_relative_and_absolute_adapter_imports(self):
        self.assertEqual(self.hits("from .store import Store"), {"gst_worker.store"})
        self.assertEqual(self.hits("from gst_worker.queue import enqueue"), {"gst_worker.queue"})
        self.assertEqual(self.hits("from . import http"), {"gst_worker.http"})
        self.assertEqual(self.hits("def f():\n    from .http import first_data\n"), {"gst_worker.http"})

    def test_sees_http_clients(self):
        self.assertEqual(self.hits("import urllib.request"), {"urllib.request"})
        self.assertEqual(self.hits("from urllib import request"), {"urllib.request"})
        self.assertEqual(self.hits("from http.client import HTTPSConnection"), {"http.client"})

    def test_allows_pure_modules(self):
        source = "from urllib.parse import urlsplit\nfrom http.cookies import SimpleCookie\nfrom .rules import x\nimport json"
        self.assertEqual(self.hits(source), set())


if __name__ == "__main__":
    unittest.main()
