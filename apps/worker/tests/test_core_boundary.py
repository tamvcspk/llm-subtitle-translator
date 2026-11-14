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
ADAPTERS = frozenset({
    "store", "queue", "http", "bazarr", "tmdb", "gemini", "llm", "backups", "connection_tests",
    "settings",  # environment + config files + Bazarr's own config
    "web",  # the console's HTTP handler
})
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

    def test_entry_point_wires_adapters_but_touches_no_infrastructure_itself(self):
        """``worker.py`` is the process's composition root: it may import any adapter, but sockets, SQLite and
        the stdlib HTTP server stay inside the adapters (``store``, ``http``, ``web``)."""
        source = (PACKAGE_DIR.parent / "worker.py").read_text(encoding="utf-8")
        direct = {
            name for name in imported_modules(source)
            for bad in FORBIDDEN_EXTERNAL if name == bad or name.startswith(bad + ".")
        }
        self.assertFalse(direct, f"worker.py imports infrastructure directly; put it behind an adapter: {sorted(direct)}")

    def test_adapter_and_root_names_are_real_modules(self):
        names = {path.stem for path in PACKAGE_DIR.glob("*.py")}
        self.assertFalse((ADAPTERS | COMPOSITION_ROOT) - names)


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
        tree = ast.parse((PACKAGE_DIR / "ports.py").read_text(encoding="utf-8"))
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
