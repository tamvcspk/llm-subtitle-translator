"""Version history, backup before overwrite, and revert (FR-8.1, NFR-4, SPEC criterion 8)."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from gst_worker.store import Store
from gst_worker.versions import AUTO, EXTERNAL, RETRANSLATE, REVERT, SKIPPED_EXISTING, TranslationVersions


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class VersionsTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "app.db")
        self.addCleanup(self.store.close)
        self.versions = TranslationVersions(self.store, self.root / "backups")
        self.output = str(self.root / "Movie.vi.srt")


class PublishTests(VersionsTestCase):
    def test_first_publish_creates_version_one_with_matching_hash(self):
        result = self.versions.publish(self.output, "one\n", trigger=AUTO)
        self.assertEqual((result.status, result.version), ("translated", 1))
        current = self.store.current_version(self.output)
        self.assertEqual(current["sha256"], sha(self.output))
        self.assertTrue(self.versions.is_system_file(self.output))

    def test_auto_publish_never_overwrites_any_existing_file(self):
        self.versions.publish(self.output, "system\n", trigger=AUTO)
        self.assertEqual(self.versions.publish(self.output, "again\n", trigger=AUTO).status, SKIPPED_EXISTING)
        Path(self.output).write_text("hand edited\n", encoding="utf-8")
        self.assertEqual(self.versions.publish(self.output, "again\n", trigger=AUTO).status, SKIPPED_EXISTING)
        self.assertEqual(Path(self.output).read_text(encoding="utf-8"), "hand edited\n")

    def test_hand_edited_file_is_no_longer_a_system_file(self):
        self.versions.publish(self.output, "system\n", trigger=AUTO)
        Path(self.output).write_text("edited\n", encoding="utf-8")
        self.assertFalse(self.versions.is_system_file(self.output))

    def test_foreign_file_without_history_is_not_a_system_file(self):
        Path(self.output).write_text("foreign\n", encoding="utf-8")
        self.assertFalse(self.versions.is_system_file(self.output))

    def test_retranslate_backs_up_the_old_version_and_overwrites(self):
        self.versions.publish(self.output, "old\n", trigger=AUTO)
        result = self.versions.publish(self.output, "new\n", trigger=RETRANSLATE)
        self.assertEqual(result.version, 2)
        self.assertEqual(Path(self.output).read_text(encoding="utf-8"), "new\n")
        first = self.store.get_version(self.output, 1)
        self.assertEqual(Path(first["stored_path"]).read_text(encoding="utf-8"), "old\n")
        self.assertEqual(first["status"], "superseded")
        self.assertEqual(self.store.current_version(self.output)["version"], 2)

    def test_retranslate_keeps_a_hand_edited_file_as_an_external_version(self):
        self.versions.publish(self.output, "system\n", trigger=AUTO)
        Path(self.output).write_text("my careful edits\n", encoding="utf-8")
        self.versions.publish(self.output, "new\n", trigger=RETRANSLATE)
        triggers = [row["trigger"] for row in self.store.versions(self.output)]
        self.assertEqual(triggers, [AUTO, EXTERNAL, RETRANSLATE])
        external = self.store.get_version(self.output, 2)
        self.assertEqual(Path(external["stored_path"]).read_text(encoding="utf-8"), "my careful edits\n")

    def test_retranslate_keeps_a_foreign_file_with_no_history(self):
        Path(self.output).write_text("foreign\n", encoding="utf-8")
        self.versions.publish(self.output, "new\n", trigger=RETRANSLATE)
        kept = self.store.get_version(self.output, 1)
        self.assertEqual(kept["trigger"], EXTERNAL)
        self.assertEqual(Path(kept["stored_path"]).read_text(encoding="utf-8"), "foreign\n")

    def test_backups_live_outside_the_media_directory(self):
        self.versions.publish(self.output, "x\n", trigger=AUTO)
        stored = Path(self.store.current_version(self.output)["stored_path"])
        self.assertTrue(stored.is_relative_to(self.root / "backups"))
        self.assertNotEqual(stored.parent, Path(self.output).parent)


class RevertTests(VersionsTestCase):
    def test_retranslate_then_revert_restores_the_previous_hash(self):
        """SPEC section 6, criterion 8."""
        self.versions.publish(self.output, "before\n", trigger=AUTO)
        before = sha(self.output)
        self.versions.publish(self.output, "after retranslate\n", trigger=RETRANSLATE)
        self.assertNotEqual(sha(self.output), before)

        result = self.versions.revert(self.output, 1)

        self.assertEqual(result.status, "reverted")
        self.assertEqual(sha(self.output), before)
        current = self.store.current_version(self.output)
        self.assertEqual((current["version"], current["trigger"], current["parent_version"]), (3, REVERT, 1))
        self.assertTrue(self.versions.is_system_file(self.output))

    def test_revert_can_go_back_and_forth(self):
        self.versions.publish(self.output, "a\n", trigger=AUTO)
        self.versions.publish(self.output, "b\n", trigger=RETRANSLATE)
        self.versions.revert(self.output, 1)
        self.versions.revert(self.output, 2)
        self.assertEqual(Path(self.output).read_text(encoding="utf-8"), "b\n")

    def test_revert_keeps_hand_edits_made_after_the_last_version(self):
        self.versions.publish(self.output, "a\n", trigger=AUTO)
        self.versions.publish(self.output, "b\n", trigger=RETRANSLATE)
        Path(self.output).write_text("edited b\n", encoding="utf-8")
        self.versions.revert(self.output, 1)
        edits = [r for r in self.store.versions(self.output) if r["trigger"] == EXTERNAL]
        self.assertEqual(Path(edits[0]["stored_path"]).read_text(encoding="utf-8"), "edited b\n")

    def test_revert_restores_a_deleted_target(self):
        self.versions.publish(self.output, "a\n", trigger=AUTO)
        Path(self.output).unlink()
        self.versions.revert(self.output, 1)
        self.assertEqual(Path(self.output).read_text(encoding="utf-8"), "a\n")

    def test_revert_refuses_unknown_or_damaged_versions(self):
        self.versions.publish(self.output, "a\n", trigger=AUTO)
        with self.assertRaises(ValueError):
            self.versions.revert(self.output, 9)
        Path(self.store.get_version(self.output, 1)["stored_path"]).write_text("rot", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "damaged"):
            self.versions.revert(self.output, 1)
        self.assertEqual(Path(self.output).read_text(encoding="utf-8"), "a\n")


class StoreTests(unittest.TestCase):
    def test_migrations_are_idempotent_across_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "app.db"
            first = Store(path)
            self.assertEqual(first.schema_version, 2)
            first.record_llm_call(
                "j", provider="p", model="m", task="translate", tokens_in=3, tokens_out=4, latency_ms=5, outcome="ok"
            )
            first.close()
            second = Store(path)
            try:
                self.assertEqual(second.schema_version, 2)
                self.assertEqual(second.token_totals(), {"calls": 1, "tokens_in": 3, "tokens_out": 4})
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
