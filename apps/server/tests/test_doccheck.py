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

    def test_server_modules_are_found_in_every_layer_and_a_sub_package_is_named_with_its_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (
                "__init__.py", "runtime.py", "services/__init__.py", "services/units.py", "jobs/queue.py",
                "adapters/store/__init__.py", "adapters/store/auth.py", "adapters/http.py",
            ):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("", encoding="utf-8")
            self.assertEqual(
                doccheck.server_modules(root), ["http", "queue", "runtime", "store/auth", "units"],
            )
        problems = doccheck.missing_modules("`auth.py` and `units.py`", ["store/auth", "units"], [])
        self.assertEqual(problems, ["docs/ARCHITECTURE.md does not name subserver/store/auth.py"])

    def test_subcore_modules_lists_packages_and_modules_but_not_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            for name in ("__init__.py", "__main__.py", "srt.py", "pkg/__init__.py", "pkg/inner.py"):
                (root / name).write_text("", encoding="utf-8")
            self.assertEqual(doccheck.subcore_modules(root), ["pkg", "pkg.inner", "srt"])


class ReferenceTests(unittest.TestCase):
    def test_paths_are_read_from_backticks_and_patterns_are_left_out(self) -> None:
        text = (
            "see `docs/API.md`, `scripts/smoke/` and `apps/server/subserver/api/answers/<page>.py` "
            "or `apps/web/src/**/*.test.ts` and `docs/design/ARCHITECTURE.md`. Plain docs/NOPE.md is not in backticks; "
            "`uv run --locked` is not a path."
        )
        self.assertEqual(doccheck.instruction_paths(text), {"docs/API.md", "scripts/smoke/", "docs/design/ARCHITECTURE.md"})

    def test_a_path_that_does_not_exist_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "API.md").write_text("", encoding="utf-8")
            (root / ".claude" / "skills" / "x").mkdir(parents=True)
            (root / ".claude" / "skills" / "x" / "SKILL.md").write_text("`docs/API.md` and `docs/GONE.md`", encoding="utf-8")
            (root / "AGENTS.md").write_text("`scripts/gone.py`", encoding="utf-8")
            self.assertEqual(
                doccheck.missing_references(root),
                [
                    "AGENTS.md names scripts/gone.py, which does not exist",
                    ".claude/skills/x/SKILL.md names docs/GONE.md, which does not exist",
                ],
            )


class RepositoryTests(unittest.TestCase):
    def test_the_docs_name_every_module_and_every_route(self) -> None:
        """Adding a module or a route without saying so in docs/ARCHITECTURE.md or docs/API.md fails here."""
        self.assertEqual(doccheck.check(REPO_ROOT), [])

    def test_agents_md_points_at_the_architecture_instead_of_copying_it(self) -> None:
        """P15f: the seams and invariants live in docs/ARCHITECTURE.md only."""
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        architecture = (REPO_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("## 10. Seams", architecture)
        self.assertIn("## 11. Invariants", architecture)
        self.assertNotIn("Seams to respect", agents)
        self.assertNotIn("Invariants:", agents)
        self.assertIn("docs/ARCHITECTURE.md", agents)
        self.assertLess(len(agents), 20_000)  # it held the seams and invariants too once

    def test_every_role_has_a_skill_or_an_agent(self) -> None:
        for name in ("backend-dev", "frontend-dev", "designer", "architect"):
            self.assertTrue((REPO_ROOT / ".claude" / "skills" / name / "SKILL.md").is_file(), name)
        for name in ("qa", "designer"):
            self.assertTrue((REPO_ROOT / ".claude" / "agents" / f"{name}.md").is_file(), name)


if __name__ == "__main__":
    unittest.main()
