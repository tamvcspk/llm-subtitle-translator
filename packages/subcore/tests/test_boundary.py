"""Criterion 18c: subcore and its CLI import nothing from the worker, the database or the HTTP server (ADR-2, ADR-16).

The OpenAI-compatible provider is an adapter of the engine and may use sockets; only the worker's own pieces and
the database are off limits here.
"""

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "subcore"
FORBIDDEN = {"subserver", "sqlite3"}
FORBIDDEN_DOTTED = {"http.server"}


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports, absolute and relative (relative ones are reported as their first part)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


class BoundaryTests(unittest.TestCase):
    def test_no_module_imports_the_worker_or_the_database(self):
        offenders = {}
        for path in sorted(SRC.rglob("*.py")):
            bad = {
                name for name in imported_modules(path)
                if name.split(".")[0] in FORBIDDEN or any(name == d or name.startswith(d + ".") for d in FORBIDDEN_DOTTED)
            }
            if bad:
                offenders[str(path.relative_to(SRC))] = sorted(bad)
        self.assertEqual(offenders, {})

    def test_the_cli_and_the_bundle_are_part_of_the_scan(self):
        scanned = {p.name for p in SRC.rglob("*.py")}
        self.assertIn("cli.py", scanned)
        self.assertIn("bundle.py", scanned)

    def test_bundle_module_has_no_io_or_network(self):
        """The bundle is data and reading: nothing that touches files, processes or the network."""
        banned = {"os", "subprocess", "socket", "urllib", "http", "requests", "pathlib", "tempfile", "shutil"}
        found = {name.split(".")[0] for name in imported_modules(SRC / "bundle.py")}
        self.assertEqual(found & banned, set())


if __name__ == "__main__":
    unittest.main()
