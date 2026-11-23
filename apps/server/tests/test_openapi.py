"""P15e (ADR-18 part 3): the OpenAPI document of the console is exported from the code and committed, and ``apps/web`` takes its
types from it. A field changed in ``api/answers`` or ``api/schemas.py`` and not exported fails here; one exported and not
generated into ``apps/web`` fails in the ``web`` job (``npm run gen:api`` and no diff).
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from subserver.api import openapi
from subserver.api.web import create_app

REPO = Path(__file__).resolve().parents[3]
EXPORT = "uv run --locked --directory apps/server python -m subserver.api.openapi"


class ExportedDocumentTests(unittest.TestCase):
    def test_the_committed_document_is_what_the_code_says(self):
        committed = (REPO / "docs" / "openapi.json").read_bytes().decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(committed, openapi.render(), f"docs/openapi.json is out of date: run `{EXPORT}` and commit it")

    def test_the_export_does_not_depend_on_a_running_server_and_is_the_same_every_time(self):
        self.assertEqual(openapi.render(), openapi.render())
        self.assertEqual(openapi.openapi_document(), json.loads(openapi.render()))

    def test_check_says_so_when_the_file_is_not_current_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "openapi.json"
            with contextlib.redirect_stderr(io.StringIO()) as shown:
                self.assertEqual(openapi.main(["--check", "--out", str(path)]), 1)
            self.assertIn("out of date", shown.getvalue())
            self.assertFalse(path.exists())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(openapi.main(["--out", str(path)]), 0)
            self.assertEqual(openapi.main(["--check", "--out", str(path)]), 0)
            path.write_text(path.read_text(encoding="utf-8").replace('"LLM Subtitle Translator"', '"Another"'), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(openapi.main(["--check", "--out", str(path)]), 1)


class WhatTheDocumentSaysTests(unittest.TestCase):
    document = openapi.openapi_document()

    def operations(self):
        for path, item in self.document["paths"].items():
            for method, operation in item.items():
                yield method.upper(), path, operation

    def test_every_route_of_the_app_is_in_the_document_and_nothing_else(self):
        served = {
            (method, route.path) for route in create_app({}, None).routes if route.path.startswith("/api/")
            for method in (route.methods or set()) - {"HEAD"}
        }
        described = {(method, path) for method, path, _ in self.operations()}

        def plain(path: str) -> str:  # the document says ``{p1}``, the router ``{p1:int}``
            import re

            return re.sub(r"\{(\w+):\w+\}", r"{\1}", path)

        self.assertEqual({(m, plain(p)) for m, p in served}, described)
        self.assertGreater(len(described), 90)

    def test_a_json_route_names_the_model_of_its_answer(self):
        files = {"/api/backups/download", "/api/series/{unit_id}/glossary.yaml", "/api/movies/{unit_id}/glossary.yaml"}
        for method, path, operation in self.operations():
            content = operation["responses"]["200"]["content"]
            if path in files:
                self.assertNotIn("application/json", content, path)
                continue
            schema = content["application/json"]["schema"]
            self.assertTrue(
                "$ref" in schema or "anyOf" in schema or schema.get("type") == "object", f"{method} {path} answers {schema}",
            )

    def test_a_refusal_is_the_console_error_not_the_framework_one(self):
        schemas = self.document["components"]["schemas"]
        self.assertNotIn("HTTPValidationError", schemas)
        self.assertNotIn("ValidationError", schemas)
        self.assertEqual(schemas["ErrorAnswer"]["required"], ["error"])
        for method, path, operation in self.operations():
            self.assertNotIn("422", operation["responses"], f"{method} {path}")
            self.assertIn("4XX", operation["responses"], f"{method} {path}")

    def test_the_query_a_route_reads_is_in_the_document(self):
        def query(path: str, method: str = "get") -> dict[str, dict]:
            found = self.document["paths"][path][method]["parameters"]
            return {p["name"]: p for p in found if p["in"] == "query"}

        cues = query("/api/units/{p1}/cues")
        self.assertEqual(set(cues), {"lang", "at", "q", "offset", "limit"})
        self.assertEqual(cues["offset"]["schema"]["type"], "integer")
        self.assertTrue(query("/api/series/{p1}/glossary")["lang"]["required"])
        self.assertTrue(query("/api/series/{unit_id}/glossary.yaml")["lang"]["required"])
        self.assertTrue(query("/api/series/{series_id}/golden")["lang"]["required"])
        self.assertEqual(set(query("/api/events")), {"job", "unit", "kind", "level", "since", "before", "limit"})
        self.assertEqual(set(query("/api/suggestions")), {"status", "series", "source", "lang", "limit"})
        self.assertTrue(query("/api/backups/download")["name"]["required"])
        self.assertEqual(set(query("/api/metrics")), {"days"})

    def test_a_series_and_a_movie_read_the_same_query_for_a_part_they_share(self):
        """``apps/web/src/api/route.ts`` (``ownerUrl``) checks a part's query names against the series route only, so the
        movie route must read the same names, with the same types, and answer the same way."""
        prefix = {"series": "/api/series/{p1}/", "movies": "/api/movies/{p1}/"}
        parts = {
            kind: {path[len(start):] for path in self.document["paths"] if path.startswith(start)}
            for kind, start in prefix.items()
        }
        shared = parts["series"] & parts["movies"]
        self.assertIn("glossary", shared)
        self.assertIn("style/preview", shared)
        self.assertNotIn("glossary/copy", parts["movies"])  # series only, and not a part `ownerUrl` offers

        def shape(path: str, method: str) -> tuple[object, object]:
            operation = self.document["paths"][path][method]
            query = {
                p["name"]: (p["schema"], bool(p.get("required")))
                for p in operation.get("parameters", []) if p["in"] == "query"
            }
            return json.dumps(query, sort_keys=True), operation["responses"]["200"]["content"]

        for part in sorted(shared):
            series, movie = self.document["paths"][prefix["series"] + part], self.document["paths"][prefix["movies"] + part]
            self.assertEqual(set(series), set(movie), f"{part}: the two routes allow different methods")
            for method in series:
                self.assertEqual(
                    shape(prefix["series"] + part, method), shape(prefix["movies"] + part, method), f"{method.upper()} {part}",
                )

    def test_what_a_field_means_reaches_the_document(self):
        schemas = self.document["components"]["schemas"]
        self.assertIn("Unix seconds", schemas["QueueJob"]["properties"]["retry_at"]["description"])
        self.assertIn("never", schemas["CharacterCard"]["properties"]["address"]["description"])

    def test_the_model_of_an_answer_says_which_fields_may_be_left_out(self):
        schemas = self.document["components"]["schemas"]
        self.assertEqual(set(schemas["QueueCounts"]["required"]), {"pending", "processing", "deferred", "done", "failed", "cancelled"})
        style = schemas["Style"]
        self.assertNotIn("required", style)  # every axis of a style may be left out
        self.assertNotIn("default", json.dumps(style))
        self.assertIn("from", schemas["GlossaryTerm"]["properties"])  # the JSON name, not the Python one
        self.assertIn("in", schemas["Tokens"]["properties"])
        self.assertIn("validate", schemas["InsightsQuality"]["properties"])


if __name__ == "__main__":
    unittest.main()
