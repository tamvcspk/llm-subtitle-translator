"""``scripts/doccheck.py``: the docs name every module and every route the code has (PLAN P15b)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location("doccheck", REPO_ROOT / "scripts" / "doccheck.py")
assert _SPEC and _SPEC.loader
doccheck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(doccheck)


class RouteTests(unittest.TestCase):
    def test_a_regex_route_and_its_doc_heading_are_the_same_route(self) -> None:
        self.assertEqual(doccheck.normalize_route(r"/api/units/(\d+)/cues"), "/api/units/{}/cues")
        self.assertEqual(doccheck.normalize_route("/api/units/{id}/cues?lang=&at="), "/api/units/{}/cues")
        self.assertEqual(doccheck.normalize_route(r"^/api/series/(\d+)/golden/?$"), "/api/series/{}/golden")
        self.assertEqual(doccheck.normalize_route(r"^/api/keys/(\d+)(/test|/remove)?/?$"), "/api/keys/{}")
        self.assertEqual(doccheck.normalize_route(r"/api/style/presets/([a-z0-9-]+)/clone"), "/api/style/presets/{}/clone")

    def test_what_is_not_a_route_is_left_out(self) -> None:
        self.assertIsNone(doccheck.normalize_route("not a route"))
        self.assertIsNone(doccheck.normalize_route("/login"))

    def test_code_routes_reads_literals_only(self) -> None:
        source = '''
"""Module doc: GET /api/in/a/docstring."""
ROUTES = [("GET", r"/api/units/(\\d+)", None)]
def handler(path, bazarr_url):
    """POST /api/also/a/docstring."""
    if path == "/api/status":
        pass
    if path.startswith("/api/jobs/"):  # a prefix, not a route
        pass
    return f"{bazarr_url}/api/series"  # a call to Bazarr
'''
        self.assertEqual(doccheck.code_routes(source), {"/api/units/{}", "/api/status"})

    def test_doc_routes_ignore_the_query_and_the_placeholder_name(self) -> None:
        text = "### GET /api/units/{unit_id}/cues?lang=&at=\n| `POST /api/queue/stop` | x |\nand /api/metrics?days=30."
        self.assertEqual(doccheck.doc_routes(text), {"/api/units/{}/cues", "/api/queue/stop", "/api/metrics"})

    def test_a_route_the_docs_lack_is_reported(self) -> None:
        problems = doccheck.missing_routes({"/api/a", "/api/b"}, {"/api/a"})
        self.assertEqual(problems, ["docs/API.md does not mention /api/b"])


class ModuleTests(unittest.TestCase):
    def test_a_module_is_named_as_file_name_and_a_subcore_module_as_dotted_name(self) -> None:
        text = "`queue.py` and `subcore.translate.engine` and subcore.memory"
        self.assertEqual(
            doccheck.missing_modules(text, ["queue", "jobs"], ["translate.engine", "memory", "srt"]),
            ["docs/ARCHITECTURE.md does not name subserver/jobs.py", "docs/ARCHITECTURE.md does not name subcore.srt"],
        )

    def test_a_file_of_another_name_does_not_count(self) -> None:
        # `subjobs.py` and `x.jobs.py` are not `jobs.py`; `subcore.memoryx` is not `subcore.memory`
        problems = doccheck.missing_modules("subjobs.py x.jobs.py subcore.memoryx", ["jobs"], ["memory"])
        self.assertEqual(len(problems), 2)

    def test_subcore_modules_lists_packages_and_modules_but_not_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            for name in ("__init__.py", "__main__.py", "srt.py", "pkg/__init__.py", "pkg/inner.py"):
                (root / name).write_text("", encoding="utf-8")
            self.assertEqual(doccheck.subcore_modules(root), ["pkg", "pkg.inner", "srt"])


class RepositoryTests(unittest.TestCase):
    def test_the_docs_name_every_module_and_every_route(self) -> None:
        """Adding a module or a route without saying so in docs/ARCHITECTURE.md or docs/API.md fails here."""
        self.assertEqual(doccheck.check(REPO_ROOT), [])


if __name__ == "__main__":
    unittest.main()
