import json
import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import worker
from gst_worker import (
    backups as gst_backups,
    bazarr as gst_bazarr,
    config as gst_config,
    connection_tests as gst_connection_tests,
    gemini as gst_gemini,
    http as gst_http,
    logs as gst_logs,
    queue as gst_queue,
    subtitles as gst_subtitles,
    tmdb as gst_tmdb,
    translation as gst_translation,
)


class FakeHTTP:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params or {}, headers or {}))
        key = (url, tuple(sorted((params or {}).items())))
        return self.responses[key]

    def request_json(self, method, url, params=None, headers=None):
        self.calls.append((method, url, params or {}, headers or {}))
        return {"ok": True}




class TestWorkerTranslationTests(unittest.TestCase):
    def test_run_translation_does_not_overwrite_output_created_during_gst(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

            def fake_run(command, **kwargs):
                temp_output = Path(command[command.index("-o") + 1])
                temp_output.write_text("gemini translation", encoding="utf-8")
                output.write_text("embedded zh subtitle", encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                status = gst_translation.run_translation(
                    {
                        "subtitle_path": str(subtitle),
                        "output_path": str(output),
                        "target_code": "zh",
                        "target_language": "Simplified Chinese",
                    },
                    "",
                    {"gemini_api_key": "secret"},
                )

            self.assertEqual(status, "skipped-existing-output")
            self.assertEqual(output.read_text(encoding="utf-8"), "embedded zh subtitle")
            self.assertFalse((root / "Movie.zh.partial.srt").exists())


    def test_run_translation_preserves_partial_output_when_progress_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            partial = root / "Movie.zh.partial.srt"
            progress = root / "Movie.en.progress"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            partial.write_text("already translated chunk", encoding="utf-8")
            progress.write_text('{"line": 2, "input_file": "Movie.en.srt"}', encoding="utf-8")

            def fake_run(command, **kwargs):
                self.assertTrue(partial.exists())
                partial.write_text("resumed translation", encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                status = gst_translation.run_translation(
                    {
                        "subtitle_path": str(subtitle),
                        "output_path": str(output),
                        "target_code": "zh",
                        "target_language": "Simplified Chinese",
                    },
                    "",
                    {"gemini_api_key": "secret"},
                )

            self.assertEqual(status, "translated")
            self.assertEqual(output.read_text(encoding="utf-8"), "resumed translation")


    def test_run_translation_retries_exit_130_with_smaller_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            partial = root / "Movie.zh.partial.srt"
            progress = root / "Movie.en.progress"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                temp_output = Path(command[command.index("-o") + 1])
                if len(commands) == 1:
                    temp_output.write_text("unsafe partial translation", encoding="utf-8")
                    progress.write_text('{"line": 1, "input_file": "Movie.en.srt"}', encoding="utf-8")
                    return type(
                        "Result",
                        (),
                        {"returncode": 130, "stdout": "Expected 776 lines, got 679.", "stderr": ""},
                    )()

                self.assertFalse(partial.exists())
                self.assertFalse(progress.exists())
                temp_output.write_text("fallback translation", encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                status = gst_translation.run_translation(
                    {
                        "subtitle_path": str(subtitle),
                        "output_path": str(output),
                        "target_code": "zh",
                        "target_language": "Simplified Chinese",
                    },
                    "",
                    {"gemini_api_key": "secret", "gst_batch_size": 1000, "gst_retry_batch_size": 500},
                )

            self.assertEqual(status, "translated")
            self.assertEqual(output.read_text(encoding="utf-8"), "fallback translation")
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0][commands[0].index("--batch-size") + 1], "1000")
            self.assertEqual(commands[1][commands[1].index("--batch-size") + 1], "500")


    def test_run_translation_defers_gemini_503_that_outlasts_translator_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            result = type(
                "Result",
                (),
                {
                    "returncode": 130,
                    "stdout": (
                        "Stopping script due to reaching 3 consecutive errors. "
                        "Last error: 503 UNAVAILABLE. This model is currently experiencing high demand."
                    ),
                    "stderr": "",
                },
            )()

            with patch("gst_worker.translation.subprocess.run", return_value=result) as run:
                with self.assertRaises(gst_translation.ProviderUnavailableError):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {"gemini_api_key": "secret", "gst_batch_size": 500, "gst_retry_batch_size": 300},
                    )

            self.assertEqual(run.call_count, 1)


    def test_run_translation_does_not_retry_daily_quota_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            result = type(
                "Result",
                (),
                {
                    "returncode": 130,
                    "stdout": (
                        "429 RESOURCE_EXHAUSTED: Quota exceeded for metric "
                        "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
                    ),
                    "stderr": "",
                },
            )()

            with patch("gst_worker.translation.subprocess.run", return_value=result) as run:
                with self.assertRaises(gst_translation.DailyQuotaExceededError):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {"gemini_api_key": "secret", "gst_batch_size": 500, "gst_retry_batch_size": 300},
                    )

            self.assertEqual(run.call_count, 1)


    def test_run_translation_resumes_with_retry_batch_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            partial = root / "Movie.zh.partial.srt"
            progress = root / "Movie.en.progress"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            partial.write_text("already translated chunk", encoding="utf-8")
            progress.write_text('{"line": 701, "input_file": "Movie.en.srt"}', encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                temp_output = Path(command[command.index("-o") + 1])
                temp_output.write_text("finished from resume", encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                status = gst_translation.run_translation(
                    {
                        "subtitle_path": str(subtitle),
                        "output_path": str(output),
                        "target_code": "zh",
                        "target_language": "Simplified Chinese",
                    },
                    "",
                    {"gemini_api_key": "secret", "gst_batch_size": 1000, "gst_retry_batch_size": 500},
                )

            self.assertEqual(status, "translated")
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][commands[0].index("--batch-size") + 1], "500")
            self.assertEqual(output.read_text(encoding="utf-8"), "finished from resume")


    def test_run_translation_does_not_retry_unknown_resume_exit_130(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            partial = root / "Movie.zh.partial.srt"
            progress = root / "Movie.en.progress"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            partial.write_text("already translated chunk", encoding="utf-8")
            progress.write_text('{"line": 701, "input_file": "Movie.en.srt"}', encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                self.assertTrue(partial.exists())
                self.assertTrue(progress.exists())
                return type("Result", (), {"returncode": 130, "stdout": "", "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {
                            "gemini_api_key": "secret",
                            "gst_batch_size": 500,
                            "gst_retry_batch_size": 300,
                        },
                    )

            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][commands[0].index("--batch-size") + 1], "300")


    def test_run_translation_defers_overloaded_resume_without_further_batch_reduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            partial = root / "Movie.zh.partial.srt"
            progress = root / "Movie.en.progress"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            partial.write_text("already translated chunk", encoding="utf-8")
            progress.write_text('{"line": 701, "input_file": "Movie.en.srt"}', encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                temp_output = Path(command[command.index("-o") + 1])
                if len(commands) == 1:
                    self.assertTrue(partial.exists())
                    self.assertTrue(progress.exists())
                    return type("Result", (), {"returncode": 130, "stdout": "Model is overloaded", "stderr": ""})()

                temp_output.write_text("finished after resume fallback", encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("gst_worker.translation.subprocess.run", side_effect=fake_run):
                with self.assertRaises(gst_translation.ProviderUnavailableError):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {
                            "gemini_api_key": "secret",
                            "gst_batch_size": 500,
                            "gst_retry_batch_size": 300,
                        },
                    )

            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][commands[0].index("--batch-size") + 1], "300")


    def test_run_translation_failure_reports_stdout_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitle = root / "Movie.en.srt"
            output = root / "Movie.zh.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

            with patch(
                "gst_worker.translation.subprocess.run",
                return_value=type(
                    "Result",
                    (),
                    {"returncode": 1, "stdout": "useful stdout failure detail", "stderr": ""},
                )(),
            ):
                with self.assertRaisesRegex(RuntimeError, "useful stdout failure detail"):
                    gst_translation.run_translation(
                        {
                            "subtitle_path": str(subtitle),
                            "output_path": str(output),
                            "target_code": "zh",
                            "target_language": "Simplified Chinese",
                        },
                        "",
                        {"gemini_api_key": "secret"},
                    )


    def test_build_gst_command_uses_job_target_language(self):
        command = gst_translation.build_gst_command(
            "/media/Movie.en.srt",
            "/media/Movie.zt.srt",
            "",
            target_language="Traditional Chinese",
        )

        self.assertEqual(command[command.index("-l") + 1], "Traditional Chinese")


    def test_build_gst_command_uses_configured_gst_settings(self):
        command = gst_translation.build_gst_command(
            "/media/Movie.en.srt",
            "/media/Movie.zh.srt",
            "",
            target_language="Simplified Chinese",
            gst_settings={
                "gst_model": "gemini-2.5-flash",
                "gst_batch_size": 500,
                "gst_paid_quota": True,
                "gst_skip_upgrade": False,
                "gst_quiet": False,
                "gst_progress_log": True,
                "gst_thoughts_log": True,
                "gst_temperature": "0.7",
                "gst_top_p": "0.95",
                "gst_top_k": "40",
                "gst_thinking_budget": "2048",
                "gst_thinking_level": "medium",
                "gst_no_streaming": True,
                "gst_no_thinking": True,
                "gst_token_report": True,
                "gst_token_stats": True,
                "gst_context_size": 80,
            },
        )

        self.assertEqual(command[command.index("--model") + 1], "gemini-2.5-flash")
        self.assertEqual(command[command.index("--batch-size") + 1], "500")
        self.assertIn("--paid-quota", command)
        self.assertIn("--progress-log", command)
        self.assertIn("--thoughts-log", command)
        self.assertIn("--no-streaming", command)
        self.assertIn("--no-thinking", command)
        self.assertIn("--token-report", command)
        self.assertNotIn("--token-stats", command)
        self.assertEqual(command[command.index("--context-size") + 1], "80")
        self.assertIn("--temperature", command)
        self.assertIn("--top-p", command)
        self.assertIn("--top-k", command)
        self.assertIn("--thinking-budget", command)
        self.assertNotIn("--thinking-level", command)
        self.assertNotIn("--skip-upgrade", command)
        self.assertNotIn("--quiet", command)


    def test_build_gst_command_uses_one_thinking_option_when_both_are_configured(self):
        flash_25_command = gst_translation.build_gst_command(
            "/media/Movie.en.srt",
            "/media/Movie.zh.srt",
            "",
            gst_settings={
                "gst_model": "gemini-2.5-flash",
                "gst_thinking_budget": "2048",
                "gst_thinking_level": "medium",
            },
        )
        flash_latest_command = gst_translation.build_gst_command(
            "/media/Movie.en.srt",
            "/media/Movie.zh.srt",
            "",
            gst_settings={
                "gst_model": "gemini-flash-latest",
                "gst_thinking_budget": "2048",
                "gst_thinking_level": "medium",
            },
        )

        self.assertIn("--thinking-budget", flash_25_command)
        self.assertNotIn("--thinking-level", flash_25_command)
        self.assertNotIn("--thinking-budget", flash_latest_command)
        self.assertIn("--thinking-level", flash_latest_command)


if __name__ == "__main__":
    unittest.main()

