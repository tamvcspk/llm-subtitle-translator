import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gst_worker import translation


class TranslationProgressTests(unittest.TestCase):
    def test_checkpoint_interpretation_agrees_with_resume_batch_selection(self):
        for checkpoint, expected_line in (("{", None), ("[]", None), ('{"line":"bad"}', None),
                                          ('{"line":1}', 1), ('{"line":"42"}', 42)):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "movie.en.srt"
                output = Path(tmp) / "movie.zh.srt"
                partial = Path(tmp) / "movie.zh.partial.srt"
                source.write_text("source", encoding="utf-8")
                source.with_suffix(".progress").write_text(checkpoint, encoding="utf-8")
                partial.write_text("partial", encoding="utf-8")
                # An omitted output path must resolve identically for execution and observation.
                job = {"subtitle_path": str(source), "target_code": "zh"}
                progress = translation.translation_progress(job)
                self.assertEqual(progress.get("progress_checkpoint"), expected_line)
                self.assertEqual(progress["partial_bytes"], 7)

                def run(command, **kwargs):
                    self.assertEqual(Path(command[command.index("-o") + 1]), partial)
                    self.assertEqual(command[command.index("--batch-size") + 1],
                                     "300" if expected_line == 42 else "500")
                    partial.write_text("translated", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0)

                with patch("gst_worker.translation.subprocess.run", side_effect=run):
                    self.assertEqual(translation.run_translation(job, "", {}), "translated")
                self.assertEqual(output.read_text(encoding="utf-8"), "translated")
                self.assertNotIn("partial_bytes", translation.translation_progress(job))

    def test_checkpoint_without_partial_does_not_select_resume_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "movie.en.srt"
            source.write_text("source", encoding="utf-8")
            source.with_suffix(".progress").write_text(json.dumps({"line": 42}), encoding="utf-8")
            job = {"subtitle_path": str(source)}
            self.assertEqual(translation.translation_progress(job), {"progress_checkpoint": 42})

            def run(command, **kwargs):
                self.assertEqual(command[command.index("--batch-size") + 1], "500")
                Path(command[command.index("-o") + 1]).write_text("translated", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            with patch("gst_worker.translation.subprocess.run", side_effect=run):
                self.assertEqual(translation.run_translation(job, "", {}), "translated")

    def test_incomplete_job_has_no_observable_progress(self):
        self.assertEqual(translation.translation_progress({}), {})
        self.assertEqual(translation.translation_progress({"subtitle_path": ""}), {})
