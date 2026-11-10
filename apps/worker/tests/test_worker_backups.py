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




class TestWorkerBackupsTests(unittest.TestCase):
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


    def test_backup_file_path_accepts_only_existing_backup_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp) / "backups"
            backup_dir.mkdir()
            backup_path = backup_dir / "safe.zip"
            backup_path.write_bytes(b"zip")

            self.assertEqual(gst_backups.backup_file_path(tmp, "safe.zip"), backup_path)

            for name in ("", "../safe.zip", "..\\safe.zip", "/tmp/safe.zip", "missing.zip"):
                with self.assertRaises(ValueError):
                    gst_backups.backup_file_path(tmp, name)


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


    def test_restore_backup_archive_rejects_invalid_zip_without_pre_import_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "state" / "config.json"
            targets_path = root / "postprocess" / "targets.json"
            config_path.parent.mkdir()
            targets_path.parent.mkdir()
            config_path.write_text('{"gemini_api_key":"old"}', encoding="utf-8")
            targets_path.write_text('{"target_languages":[]}', encoding="utf-8")

            with self.assertRaises(ValueError):
                gst_backups.restore_backup_archive(
                    b"not a zip",
                    str(root / "state"),
                    str(config_path),
                    str(targets_path),
                )

            self.assertFalse((root / "state" / "backups").exists())
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["gemini_api_key"], "old")


    def test_list_backups_returns_zip_files_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp) / "backups"
            backup_dir.mkdir()
            older = backup_dir / "older.zip"
            newer = backup_dir / "newer.zip"
            older.write_text("old", encoding="utf-8")
            newer.write_text("new", encoding="utf-8")
            now = time.time()
            os.utime(older, (now - 20, now - 20))
            os.utime(newer, (now - 10, now - 10))

            backups = gst_backups.list_backups(tmp)

            self.assertEqual([item["name"] for item in backups], ["newer.zip", "older.zip"])


    def test_purge_old_backups_deletes_files_older_than_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp) / "backups"
            backup_dir.mkdir()
            now = 2_000_000
            old = backup_dir / "old.zip"
            recent = backup_dir / "recent.zip"
            old.write_text("old", encoding="utf-8")
            recent.write_text("recent", encoding="utf-8")
            os.utime(old, (now - 31 * 86400, now - 31 * 86400))
            os.utime(recent, (now - 29 * 86400, now - 29 * 86400))

            deleted = gst_backups.purge_old_backups(tmp, now=now, retention_days=30)

            self.assertEqual(deleted, [str(old)])
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())


    def test_create_scheduled_backup_if_due_runs_every_seven_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "state" / "config.json"
            targets_path = root / "postprocess" / "targets.json"
            config_path.parent.mkdir()
            targets_path.parent.mkdir()
            config_path.write_text('{"gemini_api_key":"secret"}', encoding="utf-8")
            targets_path.write_text('{"target_languages":[]}', encoding="utf-8")
            now = 2_000_000

            first = gst_backups.create_scheduled_backup_if_due(str(root / "state"), str(config_path), str(targets_path), now=now)
            second = gst_backups.create_scheduled_backup_if_due(str(root / "state"), str(config_path), str(targets_path), now=now + 6 * 86400)
            third = gst_backups.create_scheduled_backup_if_due(str(root / "state"), str(config_path), str(targets_path), now=now + 8 * 86400)

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertIsNotNone(third)
            self.assertIn("scheduled", first["name"])
            self.assertIn("scheduled", third["name"])


    def test_manual_backup_does_not_suppress_scheduled_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "state" / "config.json"
            targets_path = root / "postprocess" / "targets.json"
            config_path.parent.mkdir()
            targets_path.parent.mkdir()
            config_path.write_text('{"gemini_api_key":"secret"}', encoding="utf-8")
            targets_path.write_text('{"target_languages":[]}', encoding="utf-8")
            now = 2_000_000

            gst_backups.create_backup(str(root / "state"), str(config_path), str(targets_path), reason="manual", now=now)
            scheduled = gst_backups.create_scheduled_backup_if_due(str(root / "state"), str(config_path), str(targets_path), now=now + 60)

            self.assertIsNotNone(scheduled)
            self.assertIn("scheduled", scheduled["name"])


