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




class TestWorkerConfigTests(unittest.TestCase):
    def test_target_output_path_replaces_english_language_code(self):
        self.assertEqual(
            gst_subtitles.target_output_path("/media/Movie/Test.Movie.en.srt", "zh"),
            "/media/Movie/Test.Movie.zh.srt",
        )
        self.assertEqual(
            gst_subtitles.target_output_path("/media/Show/Episode.eng.srt", "zh"),
            "/media/Show/Episode.zh.srt",
        )
        self.assertEqual(
            gst_subtitles.target_output_path("/media/Show/Episode.en.hi.srt", "zh"),
            "/media/Show/Episode.zh.hi.srt",
        )


    def test_target_output_path_replaces_english_with_configured_target(self):
        self.assertEqual(
            gst_subtitles.target_output_path("/media/Movie/Test.Movie.en.srt", "zt"),
            "/media/Movie/Test.Movie.zt.srt",
        )
        self.assertEqual(
            gst_subtitles.target_output_path("/media/Show/Episode.eng.srt", "ja"),
            "/media/Show/Episode.ja.srt",
        )


    def test_target_output_path_replaces_configured_source_language(self):
        self.assertEqual(
            gst_subtitles.target_output_path("/media/Movie/Test.Movie.ja.srt", "zh", source_code="ja"),
            "/media/Movie/Test.Movie.zh.srt",
        )
        self.assertEqual(
            gst_subtitles.target_output_path("/media/Show/Episode.ko.sdh.srt", "en", source_code="ko"),
            "/media/Show/Episode.en.sdh.srt",
        )


    def test_enabled_source_languages_default_to_english(self):
        self.assertEqual(
            gst_config.enabled_source_languages({}),
            [{"code": "en", "language": "English", "enabled": True}],
        )
        self.assertEqual(
            gst_config.enabled_source_languages(
                {
                    "source_languages": [
                        {"code": "en", "language": "English", "enabled": False},
                        {"code": "ja", "language": "Japanese", "enabled": True},
                    ]
                }
            ),
            [{"code": "ja", "language": "Japanese", "enabled": True}],
        )


    def test_enabled_target_languages_default_to_simplified_chinese(self):
        self.assertEqual(
            gst_config.enabled_target_languages({}),
            [{"code": "zh", "language": "Simplified Chinese", "enabled": True}],
        )
        self.assertEqual(
            gst_config.enabled_target_languages(
                {
                    "target_languages": [
                        {"code": "zh", "language": "Simplified Chinese", "enabled": False},
                        {"code": "zt", "language": "Traditional Chinese", "enabled": True},
                    ]
                }
            ),
            [{"code": "zt", "language": "Traditional Chinese", "enabled": True}],
        )


    def test_supported_target_languages_normalizes_bazarr_languages(self):
        http = FakeHTTP(
            {
                (
                    "http://bazarr:6767/api/system/languages",
                    (),
                ): [
                    {"name": "Chinese Simplified", "code2": "zh", "code3": "zho", "enabled": True},
                    {"name": "English", "code2": "en", "code3": "eng", "enabled": True},
                    {"name": "Japanese", "code2": "ja", "code3": "jpn", "enabled": False},
                ]
            }
        )

        languages = gst_config.supported_languages(http, "http://bazarr:6767", "key")

        self.assertEqual(
            languages,
            [
                {
                    "code": "zh",
                    "code3": "zho",
                    "name": "Chinese Simplified",
                    "language": "Simplified Chinese",
                    "enabled_in_bazarr": True,
                },
                {
                    "code": "en",
                    "code3": "eng",
                    "name": "English",
                    "language": "English",
                    "enabled_in_bazarr": True,
                },
                {
                    "code": "ja",
                    "code3": "jpn",
                    "name": "Japanese",
                    "language": "Japanese",
                    "enabled_in_bazarr": False,
                },
            ],
        )


    def test_save_app_config_writes_worker_and_postprocess_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            targets_path = Path(tmp) / "targets.json"

            saved = gst_config.save_app_config(
                str(config_path),
                {
                    "source_languages": [
                        {"code": "en", "language": "English", "enabled": True},
                        {"code": "ja", "language": "Japanese", "enabled": False},
                    ],
                    "target_languages": [
                        {"code": "zh", "language": "Simplified Chinese", "enabled": True},
                        {"code": "ja", "language": "Japanese", "enabled": False},
                    ],
                    "media_roots": [str(Path(tmp) / "media")],
                    "bazarr_url": "http://bazarr.local:6767",
                    "bazarr_api_key": "secret",
                    "gemini_api_key": "gemini-1",
                    "gemini_api_key2": "gemini-2",
                    "tmdb_api_key": "tmdb",
                },
                postprocess_targets_path=str(targets_path),
            )

            self.assertEqual(saved["source_languages"][0]["code"], "en")
            self.assertEqual(saved["target_languages"][0]["code"], "zh")
            self.assertEqual(saved["bazarr_url"], "http://bazarr.local:6767")
            self.assertEqual(saved["bazarr_api_key"], "secret")
            self.assertEqual(saved["gemini_api_key"], "gemini-1")
            self.assertEqual(saved["gemini_api_key2"], "gemini-2")
            self.assertEqual(saved["tmdb_api_key"], "tmdb")
            self.assertTrue(config_path.exists())
            self.assertEqual(
                json.loads(targets_path.read_text(encoding="utf-8")),
                {
                    "source_languages": [{"code": "en", "language": "English", "enabled": True}],
                    "target_languages": [{"code": "zh", "language": "Simplified Chinese", "enabled": True}],
                },
            )


    def test_save_app_config_persists_gst_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"

            saved = gst_config.save_app_config(
                str(config_path),
                {
                    "gst_model": "gemini-2.5-flash",
                    "gst_fallback_model": " gemini-3.5-flash-lite ",
                    "gst_batch_size": 500,
                    "gst_retry_batch_size": 250,
                    "gst_resume_fallback_batch_size": 50,
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
                    "job_settle_seconds": 600,
                },
            )

            self.assertEqual(saved["gst_model"], "gemini-2.5-flash")
            self.assertEqual(saved["gst_fallback_model"], "gemini-3.5-flash-lite")
            self.assertEqual(saved["gst_batch_size"], 500)
            self.assertEqual(saved["gst_retry_batch_size"], 250)
            self.assertNotIn("gst_resume_fallback_batch_size", saved)
            self.assertTrue(saved["gst_paid_quota"])
            self.assertFalse(saved["gst_skip_upgrade"])
            self.assertFalse(saved["gst_quiet"])
            self.assertTrue(saved["gst_progress_log"])
            self.assertTrue(saved["gst_thoughts_log"])
            self.assertEqual(saved["gst_temperature"], "0.7")
            self.assertEqual(saved["gst_top_p"], "0.95")
            self.assertEqual(saved["gst_top_k"], "40")
            self.assertEqual(saved["gst_thinking_budget"], "2048")
            self.assertEqual(saved["gst_thinking_level"], "medium")
            self.assertTrue(saved["gst_no_streaming"])
            self.assertTrue(saved["gst_no_thinking"])
            self.assertTrue(saved["gst_token_report"])
            self.assertNotIn("gst_token_stats", saved)
            self.assertEqual(saved["gst_context_size"], 80)
            self.assertEqual(saved["job_settle_seconds"], 600)


    def test_fallback_model_equal_to_primary_is_disabled(self):
        config = gst_config.normalize_app_config({"gst_model": "gemini-3.8-flash", "gst_fallback_model": "gemini-3.8-flash"})

        self.assertEqual(config["gst_fallback_model"], "")


    def test_default_gst_tuning_matches_recommended_automation_profile(self):
        config = gst_config.normalize_app_config({"gst_no_context": True})

        self.assertEqual(config["gst_model"], "gemini-flash-latest")
        self.assertEqual(config["gst_fallback_model"], "")
        self.assertEqual(config["gst_batch_size"], 500)
        self.assertEqual(config["gst_retry_batch_size"], 300)
        self.assertNotIn("gst_resume_fallback_batch_size", config)
        self.assertEqual(config["job_settle_seconds"], 600)
        self.assertEqual(config["gst_temperature"], "0.7")
        self.assertEqual(config["gst_top_p"], "0.95")
        self.assertEqual(config["gst_top_k"], "40")
        self.assertEqual(config["gst_thinking_budget"], "2048")
        self.assertEqual(config["gst_thinking_level"], "medium")
        self.assertTrue(config["gst_no_streaming"])
        self.assertFalse(config["gst_paid_quota"])
        self.assertEqual(config["gst_context_size"], 50)
        self.assertNotIn("gst_no_context", config)


    def test_normalize_app_config_preserves_explicit_batch_sizes(self):
        config = gst_config.normalize_app_config(
            {
                "gst_batch_size": 1000,
                "gst_retry_batch_size": 500,
                "gst_resume_fallback_batch_size": 50,
            }
        )

        self.assertEqual(config["gst_batch_size"], 1000)
        self.assertEqual(config["gst_retry_batch_size"], 500)
        self.assertNotIn("gst_resume_fallback_batch_size", config)


    def test_save_app_config_preserves_blank_secret_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            gst_config.save_app_config(
                str(config_path),
                {
                    "bazarr_api_key": "bazarr-secret",
                    "gemini_api_key": "gemini-secret",
                    "gemini_api_key2": "gemini-secret-2",
                    "tmdb_api_key": "tmdb-secret",
                },
            )

            saved = gst_config.save_app_config(
                str(config_path),
                {
                    "bazarr_url": "http://new-bazarr:6767",
                    "bazarr_api_key": "",
                    "gemini_api_key": "",
                    "gemini_api_key2": "",
                    "tmdb_api_key": "",
                },
            )

            self.assertEqual(saved["bazarr_api_key"], "bazarr-secret")
            self.assertEqual(saved["gemini_api_key"], "gemini-secret")
            self.assertEqual(saved["gemini_api_key2"], "gemini-secret-2")
            self.assertEqual(saved["tmdb_api_key"], "tmdb-secret")


    def test_save_app_config_preserves_masked_secret_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            gst_config.save_app_config(
                str(config_path),
                {
                    "bazarr_api_key": "bazarr-secret",
                    "gemini_api_key": "gemini-secret",
                    "gemini_api_key2": "gemini-secret-2",
                    "tmdb_api_key": "tmdb-secret",
                },
            )

            saved = gst_config.save_app_config(
                str(config_path),
                {
                    "bazarr_api_key": "**********",
                    "gemini_api_key": "**********",
                    "gemini_api_key2": "**********",
                    "tmdb_api_key": "**********",
                },
            )

            self.assertEqual(saved["bazarr_api_key"], "bazarr-secret")
            self.assertEqual(saved["gemini_api_key"], "gemini-secret")
            self.assertEqual(saved["gemini_api_key2"], "gemini-secret-2")
            self.assertEqual(saved["tmdb_api_key"], "tmdb-secret")


    def test_public_app_config_hides_secret_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            gst_config.save_app_config(
                str(config_path),
                {
                    "bazarr_url": "http://bazarr:6767",
                    "bazarr_api_key": "bazarr-secret",
                    "gemini_api_key": "gemini-secret",
                    "gemini_api_key2": "gemini-secret-2",
                    "tmdb_api_key": "tmdb-secret",
                },
            )

            public = worker.public_app_config(str(config_path))

            self.assertEqual(public["bazarr_api_key"], "**********")
            self.assertEqual(public["gemini_api_key"], "**********")
            self.assertEqual(public["gemini_api_key2"], "**********")
            self.assertEqual(public["tmdb_api_key"], "**********")
            self.assertTrue(public["bazarr_api_key_configured"])
            self.assertTrue(public["gemini_api_key_configured"])
            self.assertTrue(public["gemini_api_key2_configured"])
            self.assertTrue(public["tmdb_api_key_configured"])


    def test_load_settings_reads_app_secret_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            gst_config.save_app_config(
                str(config_path),
                {
                    "bazarr_url": "http://bazarr.local:6767",
                    "bazarr_api_key": "bazarr-secret",
                    "gemini_api_key": "gemini-secret",
                    "gemini_api_key2": "gemini-secret-2",
                    "tmdb_api_key": "tmdb-secret",
                },
            )

            settings = worker.load_settings(str(config_path))

            self.assertEqual(settings["bazarr_url"], "http://bazarr.local:6767")
            self.assertEqual(settings["bazarr_api_key"], "bazarr-secret")
            self.assertEqual(settings["gemini_api_key"], "gemini-secret")
            self.assertEqual(settings["gemini_api_key2"], "gemini-secret-2")
            self.assertEqual(settings["tmdb_api_key"], "tmdb-secret")


    def test_translation_environment_uses_configured_gemini_keys(self):
        env = gst_translation.translation_environment(
            {
                "gemini_api_key": "gemini-secret",
                "gemini_api_key2": "gemini-secret-2",
            },
            base_env={},
        )

        self.assertEqual(env["GEMINI_API_KEY"], "gemini-secret")
        self.assertEqual(env["GEMINI_API_KEY1"], "gemini-secret")
        self.assertEqual(env["GEMINI_API_KEY2"], "gemini-secret-2")


    def test_seed_app_config_from_settings_writes_missing_runtime_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            targets_path = Path(tmp) / "targets.json"
            gst_config.save_app_config(str(config_path), {"bazarr_url": "http://old:6767"})

            seeded = worker.seed_app_config_from_settings(
                str(config_path),
                {
                    "bazarr_url": "http://old:6767",
                    "bazarr_api_key": "bazarr-secret",
                    "gemini_api_key": "gemini-secret",
                    "gemini_api_key2": "gemini-secret-2",
                    "tmdb_api_key": "tmdb-secret",
                },
                str(targets_path),
            )

            self.assertTrue(seeded)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["bazarr_api_key"], "bazarr-secret")
            self.assertEqual(saved["gemini_api_key"], "gemini-secret")
            self.assertEqual(saved["gemini_api_key2"], "gemini-secret-2")
            self.assertEqual(saved["tmdb_api_key"], "tmdb-secret")


    def test_create_backup_writes_config_and_targets_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "state" / "config.json"
            targets_path = root / "postprocess" / "targets.json"
            config_path.parent.mkdir()
            targets_path.parent.mkdir()
            config_path.write_text('{"gemini_api_key":"secret"}', encoding="utf-8")
            targets_path.write_text('{"target_languages":[]}', encoding="utf-8")

            backup = gst_backups.create_backup(str(root / "state"), str(config_path), str(targets_path), reason="manual")

            self.assertTrue(Path(backup["path"]).exists())
            self.assertEqual(backup["name"], Path(backup["path"]).name)
            self.assertTrue(backup["name"].startswith("gemini-srt-translator-bazarr-manual-"))
            with zipfile.ZipFile(backup["path"]) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    ["backup.json", "config/config.json", "postprocess/targets.json"],
                )
                metadata = json.loads(archive.read("backup.json").decode("utf-8"))
            self.assertEqual(metadata["app"], "gemini-srt-translator-bazarr")
            self.assertEqual(metadata["reason"], "manual")


    def test_restore_backup_archive_writes_config_and_targets_after_pre_import_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "state" / "config.json"
            targets_path = root / "postprocess" / "targets.json"
            config_path.parent.mkdir()
            targets_path.parent.mkdir()
            config_path.write_text('{"gemini_api_key":"old"}', encoding="utf-8")
            targets_path.write_text('{"target_languages":[{"code":"zh"}]}', encoding="utf-8")

            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("backup.json", '{"app":"gemini-srt-translator-bazarr"}')
                archive.writestr("config/config.json", '{"gemini_api_key":"new"}')
                archive.writestr("postprocess/targets.json", '{"target_languages":[{"code":"en"}]}')

            result = gst_backups.restore_backup_archive(
                payload.getvalue(),
                str(root / "state"),
                str(config_path),
                str(targets_path),
                now=2_000_000,
            )

            self.assertEqual(result["imported"], ["config/config.json", "postprocess/targets.json"])
            self.assertTrue(result["pre_import_backup"]["name"].startswith("gemini-srt-translator-bazarr-pre-import-"))
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["gemini_api_key"], "new")
            self.assertEqual(json.loads(targets_path.read_text(encoding="utf-8"))["target_languages"][0]["code"], "en")


    def test_settings_from_payload_uses_mask_as_existing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            gst_config.save_app_config(
                str(config_path),
                {
                    "bazarr_url": "http://old:6767",
                    "bazarr_api_key": "bazarr-secret",
                    "gemini_api_key": "gemini-secret",
                    "gemini_api_key2": "gemini-secret-2",
                    "tmdb_api_key": "tmdb-secret",
                },
            )

            settings = worker.settings_from_payload(
                str(config_path),
                {
                    "bazarr_url": "http://new:6767",
                    "bazarr_api_key": "**********",
                    "gemini_api_key": "new-gemini",
                    "gemini_api_key2": "**********",
                    "tmdb_api_key": "",
                },
            )

            self.assertEqual(settings["bazarr_url"], "http://new:6767")
            self.assertEqual(settings["bazarr_api_key"], "bazarr-secret")
            self.assertEqual(settings["gemini_api_key"], "new-gemini")
            self.assertEqual(settings["gemini_api_key2"], "gemini-secret-2")
            self.assertEqual(settings["tmdb_api_key"], "tmdb-secret")


    def test_job_should_skip_non_embedded_same_language_and_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Episode.ja.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            output = Path(tmp) / "Episode.zh.srt"
            output.write_text("translated", encoding="utf-8")

            self.assertTrue(gst_queue.should_skip_job({"provider": "opensubtitles", "language": "ja", "subtitle_path": str(subtitle)}))
            self.assertTrue(
                gst_queue.should_skip_job(
                    {"provider": "embeddedsubtitles", "source_code": "ja", "target_code": "ja", "subtitle_path": str(subtitle)}
                )
            )
            self.assertTrue(gst_queue.should_skip_job({"provider": "embeddedsubtitles", "language": "ja", "subtitle_path": str(subtitle), "target_code": "zh"}))
            output.unlink()
            self.assertFalse(gst_queue.should_skip_job({"provider": "embeddedsubtitles", "language": "ja", "subtitle_path": str(subtitle), "target_code": "zh"}))


    def test_job_should_skip_when_configured_target_output_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Episode.en.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            output = Path(tmp) / "Episode.zt.srt"
            output.write_text("translated", encoding="utf-8")

            self.assertTrue(
                gst_queue.should_skip_job(
                    {
                        "provider": "embeddedsubtitles",
                        "source_code": "en",
                        "subtitle_path": str(subtitle),
                        "target_code": "zt",
                        "target_language": "Traditional Chinese",
                    }
                )
            )

            output.unlink()
            self.assertFalse(
                gst_queue.should_skip_job(
                    {
                        "provider": "embeddedsubtitles",
                        "source_code": "en",
                        "subtitle_path": str(subtitle),
                        "target_code": "zt",
                        "target_language": "Traditional Chinese",
                    }
                )
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

