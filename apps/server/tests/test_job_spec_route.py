"""GET /api/jobs/{id}/spec through the real handler: authenticated like every /api route, and answers with the
bundle document only (P11, FR-21.2)."""

import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from subcore import srt
from subcore.bundle import TranslationBundle
from subcore.translate import PromptContext
from subcore.translate.engine import make_bundle
from subserver.services.auth import ConsoleAuth
from subserver.services.console import ConsoleActions
from subserver.domain.ports import JobBundle

from tests.web_harness import serve_console

from .test_console_auth import PASSWORD, USER, basic

SAMPLE_CUES = [srt.Cue(n, (n - 1) * 2000, (n - 1) * 2000 + 1500, f"Line {n}", f"00:00:0{n},000 --> 00:00:0{n},500")
               for n in range(1, 4)]


class JobSpecRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        static = Path(self.tmp.name)
        (static / "index.html").write_text("<html>console</html>", encoding="utf-8")
        self.bundle = make_bundle(SAMPLE_CUES, PromptContext(target_lang="vi", title="Show"), model="m-1",
                                  max_output_tokens=4000)
        self.jobs = Mock()
        self.jobs.job_bundle.side_effect = lambda job_id: (
            JobBundle(job_id, self.bundle.bundle_hash(), self.bundle.to_json()) if job_id == "job-1" else None
        )
        self.ctx = {
            "static_dir": str(static),
            "queue_dir": str(static / "queue"),
            "console": ConsoleActions(self.jobs),
            "log_dir": str(static / "logs"),
        }

    def serve(self):
        return serve_console(self, self.ctx, ConsoleAuth.from_env({"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD})).port

    def get(self, port, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", path, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            conn.close()

    def test_spec_needs_the_same_login_as_every_api_route(self):
        port = self.serve()
        status, _ = self.get(port, "/api/jobs/job-1/spec")
        self.assertEqual(status, 401)

    def test_spec_is_the_bundle_document_the_cli_reads(self):
        port = self.serve()
        status, body = self.get(port, "/api/jobs/job-1/spec", {"Authorization": basic(USER, PASSWORD)})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(set(data), set(self.bundle.to_dict()))
        self.assertEqual(TranslationBundle.from_dict(data).bundle_hash(), self.bundle.bundle_hash())
        self.assertEqual(srt.serialize(list(TranslationBundle.from_dict(data).cues)), srt.serialize(list(self.bundle.cues)))

    def test_unknown_or_unfinished_job_is_404(self):
        port = self.serve()
        status, body = self.get(port, "/api/jobs/job-2/spec", {"Authorization": basic(USER, PASSWORD)})
        self.assertEqual(status, 404)
        self.assertIn("no bundle yet", body)

    def test_an_id_with_spaces_and_accents_is_found_after_decoding(self):
        """Ids of jobs imported from the old queue come from file names; the console sends them percent-encoded."""
        import urllib.parse

        odd = "My Show S01E01 — tập 1"
        self.jobs.job_bundle.side_effect = lambda job_id: (
            JobBundle(job_id, self.bundle.bundle_hash(), self.bundle.to_json()) if job_id == odd else None
        )
        port = self.serve()
        status, _ = self.get(port, f"/api/jobs/{urllib.parse.quote(odd)}/spec", {"Authorization": basic(USER, PASSWORD)})
        self.assertEqual(status, 200)

    def test_malformed_job_id_never_reaches_the_queue(self):
        port = self.serve()
        status, _ = self.get(port, "/api/jobs/..%2Fetc%2Fpasswd/spec", {"Authorization": basic(USER, PASSWORD)})
        self.assertEqual(status, 404)
        self.jobs.job_bundle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
