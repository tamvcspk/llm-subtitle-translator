"""SPEC section 6, criterion 12 (a-f): file cleanup (FR-13, P9).

Everything runs on SQLite and a temporary folder with a fixed clock. Ages are set with ``os.utime`` and by stamping
job rows, never by waiting.
"""

import hashlib
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import worker
from subserver.auth import ConsoleAuth
from subserver.cleanup import GLOSSARY, JOBS_DONE, JOBS_FAILED, TEMP, VERSIONS, Cleanup, orphan_temp_files
from subserver.glossaries import Glossaries
from subserver.jobs import CANCELLED, DONE, FAILED, QUEUED, RUNNING, SKIPPED, WAITING, JobStore
from subserver.ports import CURRENT, PRUNED, SUPERSEDED
from subserver.store import Store
from subserver.versions import AUTO, RETRANSLATE, TranslationVersions

DAY = 86400
NOW = 2_000_000_000.0  # a fixed "today"; job rows and file times are laid out relative to it


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Env:
    """A worker's storage in a temporary folder: media, DATA_DIR, QUEUE_DIR, SQLite, and the retention use case."""

    def __init__(self, case: unittest.TestCase, **settings):
        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.media, self.data, self.queue = self.root / "media", self.root / "data", self.root / "queue"
        for folder in (self.media / "Show", self.data, self.queue):
            folder.mkdir(parents=True)
        self.store = Store(self.data / "app.db")
        case.addCleanup(self.store.close)
        self.clock = [NOW]
        self.jobs = JobStore(self.store, clock=lambda: self.clock[0])
        self.versions = TranslationVersions(self.store, self.data / "backups" / "subtitles", clock=lambda: self.clock[0])
        self.glossaries = Glossaries(self.store)
        self.settings = {
            "retention_done_days": 30, "retention_failed_days": 90, "retention_versions_keep": 5,
            "retention_glossary_keep": 20, **settings,
        }
        self.media_roots = [str(self.media)]
        self.temp_roots = [str(self.data), str(self.queue)]
        self.cleanup = Cleanup(
            self.jobs, self.versions, self.glossaries, self.store,
            settings_source=lambda: self.settings, temp_roots=lambda: self.temp_roots,
            media_roots=lambda: self.media_roots, clock=lambda: self.clock[0],
        )
        self.output = str(self.media / "Show" / "Ep1.vi.srt")

    # -- laying things out ------------------------------------------------------------------------------------------
    def job(self, name: str, state: str, age_days: float = 0.0) -> str:
        """A job row in ``state`` whose last change was ``age_days`` ago."""
        added = self.jobs.add({"subtitle_path": f"/media/{name}.srt"}, key=name, state=state, job_id=name)
        then = NOW - age_days * DAY
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET created_at = ?, updated_at = ?, finished_at = ? WHERE job_id = ?",
                (then, then, then if state in (DONE, FAILED, CANCELLED, SKIPPED) else None, name),
            )
        return added.job_id or name

    def job_ids(self) -> set[str]:
        return {row["job_id"] for row in self.store.query("SELECT job_id FROM jobs")}

    def publish_versions(self, count: int, output: str | None = None) -> None:
        output = output or self.output
        self.versions.publish(output, "text 1\n", trigger=AUTO)
        for number in range(2, count + 1):
            self.versions.publish(output, f"text {number}\n", trigger=RETRANSLATE)

    def copy_exists(self, version: int) -> bool:
        row = self.store.get_version(self.output, version)
        return bool(row and row.stored_path and Path(row.stored_path).is_file())

    def glossary_backups(self, key: str = "show", lang: str = "vi", count: int = 25) -> list[int]:
        """``count`` earlier states in the history of one glossary (``count + 1`` saves: the last one is the current
        glossary, not a history line). Returns the versions that went to the history, oldest first."""
        for number in range(count + 1):
            self.glossaries.save_doc("series", key, lang, {"terms": [{"source": f"s{number}", "target": "t"}]})
        return [row["version"] for row in self.history_rows(key, lang)][::-1]

    def history_rows(self, key: str, lang: str):
        return self.store.query(
            "SELECT h.version FROM glossary_history h JOIN glossaries g ON g.id = h.glossary_id"
            " WHERE g.owner_key = ? AND g.lang = ? ORDER BY h.version DESC", (key, lang),
        )

    def history_versions(self, key: str, lang: str) -> list[int]:
        return sorted(row["version"] for row in self.history_rows(key, lang))

    def stray(self, path: Path, age_days: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"half written")
        os.utime(path, (NOW - age_days * DAY, NOW - age_days * DAY))
        return path

    def seed_everything(self) -> None:
        """One old thing in every group."""
        self.job("done-old", DONE, 40)
        self.job("failed-old", FAILED, 100)
        self.publish_versions(8)
        self.glossary_backups()
        self.stray(self.queue / ".pending.tmp", 3)
        # The file a version's history is about is still where the media is.
        Path(self.output).parent.mkdir(parents=True, exist_ok=True)


class Criterion12a(unittest.TestCase):
    def test_12a_finished_jobs_past_their_window_are_deleted_and_younger_ones_kept(self):
        env = Env(self)
        for name, state, age in [
            ("done-old", DONE, 31), ("done-new", DONE, 29), ("skipped-old", SKIPPED, 45), ("cancelled-old", CANCELLED, 45),
            ("failed-old", FAILED, 91), ("failed-new", FAILED, 89),
        ]:
            env.job(name, state, age)
        report = env.cleanup.run(dry_run=False)
        self.assertEqual(env.job_ids(), {"done-new", "failed-new"})
        self.assertEqual(report.group(JOBS_DONE).reclaimed.rows, 3)
        self.assertEqual(report.group(JOBS_FAILED).reclaimed.rows, 1)

    def test_12a_a_job_that_is_not_finished_is_never_deleted_however_old(self):
        env = Env(self)
        survivors = {env.job("queued", QUEUED, 400), env.job("running", RUNNING, 400), env.job("waiting", WAITING, 400)}
        # An old finished_at on a row that is still open must not make it look finished either.
        with env.store.transaction() as db:
            db.execute("UPDATE jobs SET finished_at = ? WHERE state IN ('queued', 'running', 'waiting')", (0.0,))
        env.cleanup.run(dry_run=False)
        self.assertEqual(env.job_ids(), survivors)

    def test_12a_the_store_refuses_to_be_asked_about_a_job_that_is_still_open(self):
        env = Env(self)
        for states in [(QUEUED,), (RUNNING,), (WAITING,), (DONE, WAITING), ()]:
            with self.subTest(states=states):
                with self.assertRaises(ValueError):
                    env.jobs.expired(states, NOW)
                with self.assertRaises(ValueError):
                    env.jobs.delete_expired(states, NOW)


class Criterion12b(unittest.TestCase):
    def test_12b_only_current_the_first_and_the_newest_keep_a_copy_and_the_rest_are_marked_pruned(self):
        env = Env(self, retention_versions_keep=2)
        env.publish_versions(8)
        env.cleanup.run(dry_run=False)
        statuses = {v.version: v.status for v in env.store.versions(env.output)}
        self.assertEqual(
            statuses, {1: SUPERSEDED, 2: PRUNED, 3: PRUNED, 4: PRUNED, 5: PRUNED, 6: PRUNED, 7: SUPERSEDED, 8: CURRENT},
        )
        self.assertEqual([n for n in range(1, 9) if env.copy_exists(n)], [1, 7, 8])
        self.assertEqual(Path(env.output).read_bytes(), b"text 8\n")  # the file the media player reads is untouched

    def test_12b_going_back_to_a_version_that_still_has_its_copy_gives_the_same_sha256(self):
        env = Env(self, retention_versions_keep=2)
        env.publish_versions(8)
        env.cleanup.run(dry_run=False)
        env.versions.revert(env.output, 7)
        self.assertEqual(Path(env.output).read_bytes(), b"text 7\n")
        self.assertEqual(env.store.current_version(env.output).sha256, sha_of(b"text 7\n"))
        env.versions.revert(env.output, 1)
        self.assertEqual(env.store.current_version(env.output).sha256, sha_of(b"text 1\n"))

    def test_12b_going_back_to_a_pruned_version_is_refused_with_a_reason_and_the_history_still_shows_it(self):
        env = Env(self, retention_versions_keep=2)
        env.publish_versions(8)
        env.cleanup.run(dry_run=False)
        with self.assertRaisesRegex(ValueError, "pruned"):
            env.versions.revert(env.output, 3)
        self.assertEqual(Path(env.output).read_bytes(), b"text 8\n")
        self.assertEqual(env.store.get_version(env.output, 3).status, PRUNED)
        self.assertEqual(len(env.store.versions(env.output)), 8)  # the lines stay; only the copies went

    def test_12b_a_pruned_copy_is_not_offered_as_a_cached_result(self):
        env = Env(self, retention_versions_keep=1)
        env.versions.publish(env.output, "first\n", trigger=AUTO)
        env.versions.publish(  # a result made from known input and context can answer a later request (NFR-3)
            env.output, "cached\n", trigger=RETRANSLATE, source_sha256="src", target_lang="vi", context_hash="ctx",
        )
        for text in ("third\n", "fourth\n"):
            env.versions.publish(env.output, text, trigger=RETRANSLATE)
        self.assertEqual(env.versions.cached_text("src", "vi", "ctx"), "cached\n")
        env.cleanup.run(dry_run=False)
        self.assertEqual(env.store.get_version(env.output, 2).status, PRUNED)
        self.assertIsNone(env.versions.cached_text("src", "vi", "ctx"))

    def test_12b_a_hand_edit_equal_to_a_pruned_version_is_kept_again_as_an_external_version(self):
        env = Env(self, retention_versions_keep=1)
        env.publish_versions(4)
        env.cleanup.run(dry_run=False)
        self.assertEqual(env.store.get_version(env.output, 2).status, PRUNED)
        Path(env.output).write_bytes(b"text 2\n")  # someone puts the old text back by hand
        env.versions.publish(env.output, "text 5\n", trigger=RETRANSLATE)
        self.assertTrue(any(v.origin == "external" and v.sha256 == sha_of(b"text 2\n") and v.stored_path
                            for v in env.store.versions(env.output)))

    def test_12b_a_removed_subtitle_keeps_the_copy_that_restores_it(self):
        env = Env(self, retention_versions_keep=1)
        env.publish_versions(4)
        env.versions.remove(env.output)
        env.cleanup.run(dry_run=False)
        tombstone = env.store.versions(env.output)[-1]
        self.assertEqual((tombstone.status, bool(tombstone.stored_path)), ("removed", True))
        env.versions.revert(env.output, tombstone.version)
        self.assertEqual(Path(env.output).read_bytes(), b"text 4\n")

    def test_12b_a_version_whose_announced_publish_has_not_settled_is_not_touched(self):
        env = Env(self, retention_versions_keep=1)
        env.publish_versions(5)
        env.store.set_version(env.output, 2, publishing=True)
        env.cleanup.run(dry_run=False)
        self.assertTrue(env.copy_exists(2))
        self.assertEqual(env.store.get_version(env.output, 2).status, SUPERSEDED)


class Criterion12c(unittest.TestCase):
    def snapshot(self, env: Env):
        files = sorted(str(p.relative_to(env.root)) for p in env.root.rglob("*") if p.is_file() and p.name != "app.db"
                       and not p.name.startswith("app.db-"))
        rows = sorted(tuple(r) for r in env.store.query("SELECT job_id, state FROM jobs"))
        versions = [(v.version, v.status) for v in env.store.versions(env.output)]
        history = [tuple(r) for r in env.store.query("SELECT glossary_id, version FROM glossary_history ORDER BY id")]
        return files, rows, versions, history

    def test_12c_a_dry_run_removes_nothing_and_matches_the_real_run_that_follows(self):
        env = Env(self, retention_versions_keep=2, retention_glossary_keep=5)
        env.seed_everything()
        before = self.snapshot(env)
        preview = env.cleanup.run(dry_run=True)
        self.assertEqual(self.snapshot(env), before)
        real = env.cleanup.run(dry_run=False)
        self.assertNotEqual(self.snapshot(env), before)
        for left, right in zip(preview.groups, real.groups):
            with self.subTest(group=left.name):
                self.assertEqual((left.reclaimed, left.enabled), (right.reclaimed, right.enabled))
        self.assertTrue(preview.dry_run and not real.dry_run)
        # Every group had something to do, so a pass on counts of zero is not what is being checked.
        for name in (JOBS_DONE, JOBS_FAILED, VERSIONS, GLOSSARY, TEMP):
            self.assertGreater(real.group(name).reclaimed.files + real.group(name).reclaimed.rows, 0, name)
        self.assertGreater(real.group(VERSIONS).reclaimed.bytes, 0)

    def test_12c_the_bytes_reported_are_the_bytes_that_were_on_disk(self):
        env = Env(self, retention_versions_keep=1, retention_glossary_keep=5)
        env.publish_versions(4)
        expected = sum(Path(env.store.get_version(env.output, n).stored_path).stat().st_size for n in (2, 3))
        report = env.cleanup.run(dry_run=True)
        self.assertEqual((report.group(VERSIONS).reclaimed.files, report.group(VERSIONS).reclaimed.bytes), (2, expected))

    def test_12c_a_second_real_run_finds_nothing_more(self):
        env = Env(self, retention_versions_keep=2)
        env.seed_everything()
        env.cleanup.run(dry_run=False)
        again = env.cleanup.run(dry_run=False)
        self.assertEqual((again.total.files, again.total.rows), (0, 0))


class Criterion12d(unittest.TestCase):
    """A media folder with subtitles and leftover temporary files: nothing in it is changed, the leftovers are listed."""

    def media_files(self, env: Env) -> dict[str, bytes]:
        return {str(p): p.read_bytes() for p in env.media.rglob("*") if p.is_file()}

    def lay_out_media(self, env: Env) -> list[Path]:
        folder = env.media / "Show"
        for name in ("Ep1.mkv", "Ep1.en.srt", "Ep2.mkv", "Ep2.vi.srt"):
            (folder / name).write_bytes(f"media {name}".encode())
        return [
            env.stray(folder / ".Ep1.vi.srt.publish.tmp", 5),  # the worker's own leftover beside a target
            env.stray(folder / ".someone-elses.tmp", 5),
            env.stray(env.media / ".top.tmp", 5),
        ]

    def test_12d_no_file_in_the_media_folders_is_changed_or_deleted_and_leftovers_are_only_listed(self):
        env = Env(self)
        env.publish_versions(8)  # puts Ep1.vi.srt in the media folder and gives the cleanup something to prune
        leftovers = self.lay_out_media(env)
        env.temp_roots.append(str(env.media))  # the worst case: media inside a folder cleanup sweeps
        before = self.media_files(env)
        report = env.cleanup.run(dry_run=False)
        self.assertEqual(self.media_files(env), before)
        self.assertTrue(all(p.exists() for p in leftovers))
        self.assertTrue({str(p) for p in leftovers} <= set(report.media_temp), report.media_temp)
        self.assertGreater(report.group(VERSIONS).reclaimed.files, 0)  # the same run did clean the copies

    def test_12d_a_folder_that_holds_a_target_subtitle_counts_as_media_even_when_no_root_is_configured(self):
        env = Env(self)
        env.publish_versions(2)
        env.media_roots = []
        leftovers = self.lay_out_media(env)[:2]
        env.temp_roots.append(str(env.media))
        env.cleanup.run(dry_run=False)
        self.assertTrue(all(p.exists() for p in leftovers))

    def test_12d_a_dry_run_lists_them_too(self):
        env = Env(self)
        env.publish_versions(2)
        leftovers = self.lay_out_media(env)
        report = env.cleanup.run(dry_run=True)
        self.assertTrue({str(p) for p in leftovers[:1]} <= set(report.media_temp))

    def test_12d_a_version_row_that_points_into_the_media_folder_never_loses_that_file(self):
        env = Env(self, retention_versions_keep=1)
        env.publish_versions(3)
        precious = env.media / "Show" / "Ep1.mkv"
        precious.write_bytes(b"the movie")
        # A damaged or hand-written row: its "copy" is a media file.
        env.store.set_version(env.output, 2, stored_path=str(precious))
        env.cleanup.run(dry_run=False)
        self.assertEqual(precious.read_bytes(), b"the movie")
        self.assertEqual(env.store.get_version(env.output, 2).status, SUPERSEDED)

    def test_12d_the_sweep_function_never_returns_a_protected_file_as_deletable(self):
        env = Env(self)
        stray = env.stray(env.media / "Show" / ".x.tmp", 5)
        deletable, protected = orphan_temp_files([env.media], [env.media], older_than=NOW - DAY)
        self.assertEqual((deletable, protected), ([], [stray]))


class Criterion12e(unittest.TestCase):
    def left(self, env: Env) -> dict[str, int]:
        return {
            JOBS_DONE: len([j for j in env.job_ids() if j.startswith("done")]),
            JOBS_FAILED: len([j for j in env.job_ids() if j.startswith("failed")]),
            VERSIONS: len([n for n in range(1, 9) if env.copy_exists(n)]),
            GLOSSARY: len(env.store.query("SELECT 1 FROM glossary_history")),
        }

    def test_12e_zero_switches_off_exactly_its_group(self):
        keys = {
            JOBS_DONE: "retention_done_days", JOBS_FAILED: "retention_failed_days",
            VERSIONS: "retention_versions_keep", GLOSSARY: "retention_glossary_keep",
        }
        for group, key in keys.items():
            with self.subTest(group=group):
                env = Env(self, **{"retention_versions_keep": 2, "retention_glossary_keep": 5, key: 0})
                env.seed_everything()
                full = {JOBS_DONE: 1, JOBS_FAILED: 1, VERSIONS: 8, GLOSSARY: 25}
                report = env.cleanup.run(dry_run=False)
                after = self.left(env)
                for name in keys:
                    if name == group:
                        self.assertEqual(after[name], full[name], f"{name} must be kept when {key} is 0")
                        self.assertFalse(report.group(name).enabled)
                    else:
                        self.assertLess(after[name], full[name], f"{name} must still be cleaned")
                        self.assertTrue(report.group(name).enabled)

    def test_12e_with_every_window_at_zero_only_leftover_temporary_files_go(self):
        env = Env(self, retention_done_days=0, retention_failed_days=0, retention_versions_keep=0, retention_glossary_keep=0)
        env.seed_everything()
        before = self.left(env)
        report = env.cleanup.run(dry_run=False)
        self.assertEqual(self.left(env), before)
        self.assertEqual([g.enabled for g in report.groups], [False, False, False, False, True])
        self.assertFalse((env.queue / ".pending.tmp").exists())

    def test_12e_a_setting_that_is_not_a_number_falls_back_to_the_default(self):
        env = Env(self, retention_done_days="soon")
        env.job("done-old", DONE, 40)
        env.cleanup.run(dry_run=False)
        self.assertEqual(env.job_ids(), set())


class Criterion12f(unittest.TestCase):
    def test_12f_a_temporary_file_older_than_a_day_is_cleaned_in_the_queue_and_in_data_dir_and_a_new_one_is_not(self):
        env = Env(self)
        old = [
            env.stray(env.queue / ".job.json.tmp", 2),
            env.stray(env.data / "series" / "show" / ".glossary.vi.yaml.ab12.tmp", 2),
            env.stray(env.data / "backups" / "subtitles" / "k" / ".v1-Ep.srt.tmp", 1.5),
        ]
        keep = [
            env.stray(env.queue / ".being-written.tmp", 0.5),
            env.stray(env.queue / "not-hidden.tmp", 9),  # not a file the worker names this way
            env.stray(env.queue / ".hidden-but-not-temp", 9),
            env.stray(env.data / "app-notes.txt", 9),
        ]
        report = env.cleanup.run(dry_run=False)
        self.assertEqual([p.exists() for p in old], [False] * 3)
        self.assertEqual([p.exists() for p in keep], [True] * 4)
        self.assertEqual(report.group(TEMP).reclaimed.files, 3)
        self.assertEqual(report.group(TEMP).reclaimed.bytes, 3 * len(b"half written"))

    def test_12f_the_age_limit_is_one_day_from_the_clock(self):
        env = Env(self)
        path = env.stray(env.queue / ".x.tmp", 0.9)
        env.cleanup.run(dry_run=False)
        self.assertTrue(path.exists())
        env.clock[0] += 0.2 * DAY
        env.cleanup.run(dry_run=False)
        self.assertFalse(path.exists())


class GlossaryHistory(unittest.TestCase):
    def test_each_series_and_language_keeps_its_newest_earlier_states_and_nothing_else_is_touched(self):
        env = Env(self, retention_glossary_keep=3)
        env.glossary_backups("show", "vi", 6)
        env.glossary_backups("show", "pt-br", 2)
        env.glossary_backups("other", "vi", 4)
        # What an older worker left on disk is not the cleanup's to delete any more: the files stay as they are.
        old_backup = env.data / "backups" / "glossary" / "series" / "show" / "vi-20260101-000000.yaml"
        old_backup.parent.mkdir(parents=True)
        old_backup.write_text("# an old backup\n", encoding="utf-8")
        live = env.data / "series" / "show" / "glossary.vi.yaml"
        live.parent.mkdir(parents=True)
        live.write_text("target_lang: vi\n", encoding="utf-8")
        current = env.glossaries.stored("series", "show", "vi")

        env.cleanup.run(dry_run=False)

        self.assertEqual(env.history_versions("show", "vi"), [4, 5, 6])  # the newest three of six
        self.assertEqual(env.history_versions("show", "pt-br"), [1, 2])
        self.assertEqual(env.history_versions("other", "vi"), [2, 3, 4])
        self.assertEqual(env.glossaries.stored("series", "show", "vi"), current)  # the glossary itself is never pruned
        self.assertTrue(old_backup.exists() and live.exists())

    def test_a_dry_run_counts_what_the_real_run_removes(self):
        env = Env(self, retention_glossary_keep=2)
        env.glossary_backups("show", "vi", 5)
        dry = env.cleanup.run(dry_run=True).group(GLOSSARY).reclaimed
        self.assertEqual(env.history_versions("show", "vi"), [1, 2, 3, 4, 5])
        real = env.cleanup.run(dry_run=False).group(GLOSSARY).reclaimed
        self.assertEqual((dry.rows, dry.bytes), (real.rows, real.bytes))
        self.assertEqual(real.rows, 3)
        self.assertEqual(env.history_versions("show", "vi"), [4, 5])


class DailyRun(unittest.TestCase):
    def test_it_runs_when_a_day_has_passed_and_the_last_run_survives_a_restart(self):
        env = Env(self)
        env.job("done-old", DONE, 40)
        first = env.cleanup.run_if_due()
        self.assertIsNotNone(first)
        self.assertEqual(env.job_ids(), set())
        env.job("done-older", DONE, 60)
        self.assertIsNone(env.cleanup.run_if_due())  # the same day
        self.assertEqual(env.job_ids(), {"done-older"})
        restarted = Cleanup(  # a new process: the last run is read from the database
            env.jobs, env.versions, env.glossaries, env.store, settings_source=lambda: env.settings,
            temp_roots=lambda: env.temp_roots, media_roots=lambda: env.media_roots, clock=lambda: env.clock[0],
        )
        self.assertIsNone(restarted.run_if_due())
        env.clock[0] += DAY + 1
        self.assertIsNotNone(restarted.run_if_due())
        self.assertEqual(env.job_ids(), set())

    def test_a_dry_run_does_not_count_as_a_daily_run(self):
        env = Env(self)
        env.cleanup.run(dry_run=True)
        self.assertIsNotNone(env.cleanup.run_if_due())

    def test_one_group_failing_does_not_stop_the_others(self):
        env = Env(self)
        env.job("done-old", DONE, 40)
        env.versions.prune = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertLogs(level="ERROR"):
            report = env.cleanup.run(dry_run=False)
        self.assertIn("boom", report.group(VERSIONS).error)
        self.assertEqual(env.job_ids(), set())


class Logging(unittest.TestCase):
    def test_the_summary_names_groups_and_counts_and_no_file_name_or_subtitle_text(self):
        env = Env(self, retention_versions_keep=1)
        env.publish_versions(4)
        env.stray(env.queue / ".secret-name.tmp", 3)
        with self.assertLogs(level="INFO") as logs:
            env.cleanup.run(dry_run=False)
        text = "\n".join(logs.output)
        self.assertIn("Cleanup:", text)
        self.assertIn("versions=2 files", text)
        self.assertNotIn("secret-name", text)
        self.assertNotIn("Ep1", text)
        self.assertNotIn("text 2", text)


class SettingsAndStatistics(unittest.TestCase):
    KEYS = ("retention_done_days", "retention_failed_days", "retention_versions_keep", "retention_glossary_keep")

    def test_the_defaults_are_the_ones_in_the_spec_and_a_bad_value_is_repaired(self):
        from subserver.config import normalize_app_config

        self.assertEqual([normalize_app_config({})[key] for key in self.KEYS], [30, 90, 5, 20])
        repaired = normalize_app_config({key: bad for key, bad in zip(self.KEYS, (-3, "soon", None, "7"))})
        self.assertEqual([repaired[key] for key in self.KEYS], [0, 90, 5, 7])

    def test_they_are_editable_settings_that_reach_the_runtime_settings(self):
        from subserver.settings import SettingsService

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = temporary.name
        store = Store(Path(folder) / "app.db")
        self.addCleanup(store.close)  # runs first: Windows will not delete a database that is still open
        service = SettingsService(
            store, config_path=str(Path(folder) / "config.json"), postprocess_targets_path=str(Path(folder) / "t.json"),
            env={"BAZARR_CONFIG_PATH": str(Path(folder) / "none.yaml")},
        )
        service.start()
        self.assertEqual([service.settings()[key] for key in self.KEYS], [30, 90, 5, 20])
        service.save({"retention_done_days": 7, "retention_versions_keep": 0})
        self.assertEqual([service.settings()[key] for key in self.KEYS], [7, 90, 0, 20])

    def test_the_model_statistics_say_how_far_back_they_reach(self):
        from subserver.queue import JobQueue

        queue = JobQueue(Store(":memory:"))
        queue.configure({"retention_done_days": 14, "retention_failed_days": 0}, ("light",), "gemini")
        self.assertEqual(queue.layer_report()["window"], {"done_days": 14, "failed_days": 0})
        queue.configure({}, ("light",), "gemini")
        self.assertEqual(queue.layer_report()["window"], {"done_days": 30, "failed_days": 90})


class HttpApi(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        (self.env.root / "index.html").write_text("<html></html>", encoding="utf-8")
        ctx = {"static_dir": str(self.env.root), "queue_dir": str(self.env.queue), "log_dir": str(self.env.root / "logs"),
               "cleanup": self.env.cleanup}
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.ConsoleHandler)
        server.ctx = ctx
        server.auth = ConsoleAuth.from_env({"CONSOLE_AUTH": "off"})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def post(self, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", "/api/cleanup", body=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()

    def test_a_request_without_dry_run_false_is_only_a_preview(self):
        self.env.job("done-old", DONE, 40)
        for body in ({}, {"dry_run": True}, {"dry_run": "false"}, {"dry_run": 0}):
            with self.subTest(body=body):
                status, data = self.post(body)
                self.assertEqual(status, 200)
                self.assertTrue(data["dry_run"])
                self.assertEqual(self.env.job_ids(), {"done-old"})
        done = next(g for g in data["groups"] if g["name"] == JOBS_DONE)
        self.assertEqual((done["rows"], done["enabled"], done["window"]), (1, True, 30))

    def test_dry_run_false_cleans_and_the_answer_says_how_much(self):
        self.env.job("done-old", DONE, 40)
        status, data = self.post({"dry_run": False})
        self.assertEqual((status, data["dry_run"], data["total"]["rows"]), (200, False, 1))
        self.assertEqual(self.env.job_ids(), set())
        self.assertEqual(set(data), {"dry_run", "at", "groups", "total", "media_temp"})


if __name__ == "__main__":
    unittest.main()
