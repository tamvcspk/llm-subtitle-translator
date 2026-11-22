"""SPEC section 6, criterion 15 (a-i): the version lifecycle of a target subtitle (FR-16), on SQLite and a fake clock.

No model is called: results are handed to ``TranslationVersions.publish`` directly, as the attempt does.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from subserver.domain.ports import CANDIDATE, CURRENT, REMOVED, SUPERSEDED
from subserver.adapters.store import Store
from subserver.services.versions import AUTO, CUE_EDIT, RETRANSLATE, REVERT, TranslationVersions


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "app.db")
        self.addCleanup(self.store.close)
        self.now = [1_000.0]
        self.versions = TranslationVersions(
            self.store, self.root / "backups", coalesce_seconds=600, clock=lambda: self.now[0],
        )
        self.output = str(self.root / "Movie.vi.srt")

    def read(self) -> str:  # bytes, not text: Windows would translate "\n" and the hashes would no longer match
        return Path(self.output).read_bytes().decode("utf-8")

    def write(self, text: str) -> None:
        Path(self.output).write_bytes(text.encode("utf-8"))

    def history(self):
        return [(v.version, v.status, v.origin) for v in self.store.versions(self.output)]


class Criterion15a(LifecycleCase):
    def test_15a_a_second_translation_supersedes_the_first_and_names_it_as_parent(self):
        self.versions.publish(self.output, "one\n", trigger=AUTO)
        self.versions.publish(self.output, "two\n", trigger=RETRANSLATE, base_version=1)
        self.assertEqual(self.history(), [(1, SUPERSEDED, "machine"), (2, CURRENT, "machine")])
        self.assertEqual(self.store.current_version(self.output).parent_version, 1)
        self.assertEqual(self.read(), "two\n")


class Criterion15b(LifecycleCase):
    def test_15b_edits_in_one_sitting_are_one_edit_version(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.versions.publish(self.output, "edit 1\n", trigger=CUE_EDIT)
        self.now[0] += 60
        self.versions.publish(self.output, "edit 2\n", trigger=CUE_EDIT)
        self.now[0] += 120
        self.versions.publish(self.output, "edit 3\n", trigger=CUE_EDIT)
        self.assertEqual(self.history(), [(1, SUPERSEDED, "machine"), (2, CURRENT, "edit")])
        self.assertEqual(self.read(), "edit 3\n")
        self.assertEqual(Path(self.store.current_version(self.output).stored_path).read_text(encoding="utf-8"), "edit 3\n")
        self.assertEqual(self.store.current_version(self.output).sha256, sha_of("edit 3\n"))

    def test_15b_an_edit_after_the_window_is_a_new_version(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.versions.publish(self.output, "edit 1\n", trigger=CUE_EDIT)
        self.now[0] += 601
        self.versions.publish(self.output, "edit 2\n", trigger=CUE_EDIT)
        self.assertEqual([v[0] for v in self.history()], [1, 2, 3])
        self.assertEqual(self.store.current_version(self.output).parent_version, 2)

    def test_15b_an_edit_after_a_retranslation_is_not_coalesced_into_it(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.versions.publish(self.output, "edit\n", trigger=CUE_EDIT)
        self.versions.publish(self.output, "retranslated\n", trigger=RETRANSLATE)
        self.versions.publish(self.output, "edit again\n", trigger=CUE_EDIT)
        self.assertEqual(self.store.current_version(self.output).version, 4)


class Criterion15c(LifecycleCase):
    def test_15c_a_hand_edited_file_is_kept_as_external_and_never_overwritten_by_an_automatic_job(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.write("my hand edits\n")
        result = self.versions.publish(self.output, "machine again\n", trigger=AUTO)
        self.assertEqual(result.status, "candidate")
        self.assertEqual(self.read(), "my hand edits\n")
        external = self.store.current_version(self.output)
        self.assertEqual((external.origin, external.trigger), ("external", "external"))
        self.assertEqual(Path(external.stored_path).read_text(encoding="utf-8"), "my hand edits\n")

    def test_15c_the_same_hand_edit_is_recorded_once(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.write("my hand edits\n")
        self.versions.publish(self.output, "a\n", trigger=AUTO)
        self.versions.publish(self.output, "b\n", trigger=AUTO)
        self.assertEqual(sum(1 for v in self.store.versions(self.output) if v.origin == "external"), 1)


class Criterion15d(LifecycleCase):
    def test_15d_a_replace_that_finds_a_newer_version_publishes_a_candidate_and_leaves_the_file_alone(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        base = self.store.current_version(self.output).version  # the replace job starts from version 1
        self.versions.publish(self.output, "admin's fix\n", trigger=CUE_EDIT)  # version 2, while the job runs
        result = self.versions.publish(self.output, "replacement\n", trigger=RETRANSLATE, base_version=base)
        self.assertEqual((result.status, result.version), ("candidate", 3))
        self.assertEqual(self.read(), "admin's fix\n")
        self.assertEqual(self.store.current_version(self.output).version, 2)
        self.assertEqual(self.store.get_version(self.output, 3).status, CANDIDATE)


class Criterion15e(LifecycleCase):
    def test_15e_a_target_that_appears_during_a_fill_job_gets_a_candidate_and_is_not_overwritten(self):
        self.write("downloaded by Bazarr\n")  # appeared after the job checked and before it published
        result = self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.assertEqual(result.status, "candidate")
        self.assertEqual(self.read(), "downloaded by Bazarr\n")
        candidate = self.store.get_version(self.output, result.version)
        self.assertEqual((candidate.status, candidate.origin), (CANDIDATE, "machine"))

    def test_15e_a_source_that_changed_meanwhile_gives_a_candidate(self):
        result = self.versions.publish(
            self.output, "machine\n", trigger=RETRANSLATE, candidate_reason="the source subtitle changed",
        )
        self.assertEqual(result.status, "candidate")
        self.assertFalse(Path(self.output).exists())


class Criterion15f(LifecycleCase):
    def test_15f_removing_from_the_console_leaves_a_tombstone_and_no_file(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        result = self.versions.remove(self.output)
        self.assertEqual(result.status, "removed")
        self.assertFalse(Path(self.output).exists())
        self.assertEqual(self.history(), [(1, SUPERSEDED, "machine"), (2, REMOVED, "edit")])
        self.assertEqual(self.versions.latest_status(self.output), REMOVED)
        self.assertIsNone(self.store.current_version(self.output))

    def test_15f_a_removed_subtitle_can_be_brought_back_by_revert(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.versions.remove(self.output)
        self.versions.revert(self.output, 2)
        self.assertEqual(self.read(), "machine\n")
        self.assertEqual(self.store.current_version(self.output).origin, "revert")

    def test_15f_remove_needs_a_current_version(self):
        with self.assertRaises(LookupError):
            self.versions.remove(self.output)

    def test_15f_a_file_deleted_outside_the_console_is_recorded_as_removed_at_start_up(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        Path(self.output).unlink()
        counts = self.versions.reconcile()
        self.assertEqual(counts["vanished"], 1)
        self.assertEqual(self.versions.latest_status(self.output), REMOVED)
        self.assertEqual(self.store.get_version(self.output, 2).origin, "external")


class UnmountedMediaTests(LifecycleCase):
    def test_a_missing_media_folder_is_not_taken_for_removed_subtitles(self):
        folder = self.root / "share"
        folder.mkdir()
        output = str(folder / "Movie.vi.srt")
        self.versions.publish(output, "machine\n", trigger=AUTO)
        Path(output).unlink()
        folder.rmdir()  # the share is not mounted at start-up
        self.assertEqual(self.versions.reconcile()["vanished"], 0)
        self.assertEqual(self.store.current_version(output).version, 1)


class Criterion15g(LifecycleCase):
    def test_15g_revert_restores_the_hash_and_is_a_revert_version(self):
        self.versions.publish(self.output, "first\n", trigger=AUTO)
        first = self.store.current_version(self.output).sha256
        self.versions.publish(self.output, "second\n", trigger=RETRANSLATE, base_version=1)
        self.versions.revert(self.output, 1)
        current = self.store.current_version(self.output)
        self.assertEqual((current.sha256, current.origin, current.parent_version), (first, "revert", 1))
        self.assertEqual(sha_of(self.read()), first)

    def test_15g_a_candidate_cannot_be_reverted_to(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.write("external\n")
        candidate = self.versions.publish(self.output, "other\n", trigger=AUTO).version
        with self.assertRaisesRegex(ValueError, "adopt"):
            self.versions.revert(self.output, candidate)


class Criterion15h(LifecycleCase):
    def test_15h_a_crash_before_the_rename_is_cancelled_and_the_old_version_stays_current(self):
        self.versions.publish(self.output, "one\n", trigger=AUTO)
        # The announcement reached the store, the rename did not happen.
        self.store.add_version(
            self.output, version=2, stored_path=str(self.root / "copy2"), sha256=sha_of("two\n"),
            trigger=RETRANSLATE, status=CURRENT, publishing=True,
        )
        counts = self.versions.reconcile()
        self.assertEqual(counts["cancelled"], 1)
        self.assertEqual(self.read(), "one\n")
        self.assertEqual(self.store.current_version(self.output).version, 1)
        self.assertEqual(self.store.get_version(self.output, 2).status, CANDIDATE)
        self.assertEqual(self.store.publishing_versions(), [])

    def test_15h_a_crash_after_the_rename_is_confirmed(self):
        self.versions.publish(self.output, "one\n", trigger=AUTO)
        self.store.add_version(
            self.output, version=2, stored_path=str(self.root / "copy2"), sha256=sha_of("two\n"),
            trigger=RETRANSLATE, status=CURRENT, publishing=True,
        )
        self.write("two\n")  # the rename happened, the confirmation did not
        counts = self.versions.reconcile()
        self.assertEqual(counts["confirmed"], 1)
        self.assertEqual(self.store.current_version(self.output).version, 2)
        self.assertFalse(self.store.get_version(self.output, 2).publishing)

    def test_15h_a_file_matching_neither_version_is_kept_as_external(self):
        self.versions.publish(self.output, "one\n", trigger=AUTO)
        self.store.add_version(
            self.output, version=2, stored_path=str(self.root / "copy2"), sha256=sha_of("two\n"),
            trigger=RETRANSLATE, status=CURRENT, publishing=True,
        )
        self.write("somebody else\n")
        self.versions.reconcile()
        self.assertEqual(self.store.current_version(self.output).origin, "external")
        self.assertEqual(self.read(), "somebody else\n")

    def test_15h_a_clean_start_changes_nothing(self):
        self.versions.publish(self.output, "one\n", trigger=AUTO)
        self.assertEqual(self.versions.reconcile(), {"confirmed": 0, "cancelled": 0, "removed": 0, "vanished": 0})


class BazarrRefreshTests(unittest.TestCase):
    """The Bazarr rescan after a subtitle change runs in the background and retries; it never changes a job."""

    def test_a_failing_bazarr_is_retried_and_then_given_up_without_raising(self):
        from types import SimpleNamespace

        from subserver.services.units import UnitService

        calls = []

        def refresh(payload):
            calls.append(payload)
            raise OSError("bazarr is down")

        holder = SimpleNamespace(_refresh=refresh, REFRESH_ATTEMPTS=3, REFRESH_DELAY_SECONDS=0.0)
        self.assertFalse(UnitService._refresh_with_retry(holder, {"media_id": "1"}))
        self.assertEqual(len(calls), 3)

    def test_a_refresh_that_lands_on_a_later_attempt_stops_there(self):
        from types import SimpleNamespace

        from subserver.services.units import UnitService

        attempts = []

        def refresh(payload):
            attempts.append(payload)
            if len(attempts) < 2:
                raise OSError("slow")

        holder = SimpleNamespace(_refresh=refresh, REFRESH_ATTEMPTS=3, REFRESH_DELAY_SECONDS=0.0)
        self.assertTrue(UnitService._refresh_with_retry(holder, {"media_id": "1"}))
        self.assertEqual(len(attempts), 2)


class Criterion15i(LifecycleCase):
    def test_15i_adopting_a_candidate_makes_it_current_and_the_old_version_superseded(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        self.write("hand edit\n")
        candidate = self.versions.publish(self.output, "candidate text\n", trigger=AUTO).version
        result = self.versions.adopt(self.output, candidate)
        self.assertEqual(result.status, "adopted")
        self.assertEqual(self.read(), "candidate text\n")
        current = self.store.current_version(self.output)
        self.assertEqual((current.version, current.status), (candidate, CURRENT))
        self.assertEqual(self.store.get_version(self.output, candidate - 1).status, SUPERSEDED)
        self.assertTrue(self.versions.is_system_file(self.output))

    def test_15i_only_a_candidate_can_be_adopted(self):
        self.versions.publish(self.output, "machine\n", trigger=AUTO)
        with self.assertRaisesRegex(ValueError, "not a candidate"):
            self.versions.adopt(self.output, 1)
        with self.assertRaises(LookupError):
            self.versions.adopt(self.output, 9)


if __name__ == "__main__":
    unittest.main()
