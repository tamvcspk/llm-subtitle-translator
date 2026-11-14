"""FR-12: the `hold` admission mode and the approval of held jobs (SPEC criterion 11).

Jobs come from the real Bazarr script payload (unchanged by FR-12), the worker applies the mode, and no test
reaches a model: the translation attempt is a mock whose calls are counted.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import worker
from gst_worker import config, queue
from gst_worker.console import ConsoleActions

TARGETS = [{"code": "vi", "language": "Vietnamese", "enabled": True}]
SCRIPT = (Path(__file__).resolve().parents[3] / "bazarr-postprocess" / "gst_enqueue.sh").read_text(encoding="utf-8")
PAYLOAD = SCRIPT.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


class FakeMedia:
    def __init__(self):
        self.refreshed = []

    def target_still_missing(self, job):
        return None

    def refresh(self, job):
        self.refreshed.append(job)


class NoMetadata:
    def description(self, job):
        return ""


class HoldCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue_dir = str(self.root / "queue")
        self.media = self.root / "media"
        self.media.mkdir()
        queue.ensure_queue_dirs(self.queue_dir)  # the shell wrapper creates these before the payload runs
        (self.root / "targets.json").write_text(json.dumps({
            "source_languages": [{"code": "en", "language": "English"}], "target_languages": TARGETS,
        }), encoding="utf-8")
        self.attempt = Mock()
        self.attempt.run.return_value = "translated"
        self.media_port = FakeMedia()
        self.console = ConsoleActions(queue.FileJobQueue(self.queue_dir))

    def worker(self, mode="auto", **settings):
        return worker.QueueWorker(
            self.queue_dir, {"job_settle_seconds": 0, "admission_mode": mode, **settings}, None, None,
            attempt=self.attempt, media=self.media_port, metadata=NoMetadata(),
        )

    def subtitle(self, stem="Show.S01E01", folder=None):
        directory = folder or self.media
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}.en.srt"
        path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n", encoding="utf-8")
        os.utime(path, (100, 100))
        return path

    def bazarr(self, path, series_id="", **extra):
        """Run the real script payload, as Bazarr does after finding the subtitle."""
        environment = {
            **os.environ, "QUEUE_DIR": self.queue_dir, "TARGETS_FILE": str(self.root / "targets.json"),
            "SUBTITLE_PATH": str(path), "VIDEO_PATH": str(path.with_suffix(".mkv")), "LANGUAGE": "en",
            "PROVIDER": "embeddedsubtitles", "SERIES_ID": series_id, "MEDIA_ID": "7",
            "MEDIA_TYPE": "series" if series_id else "movie", "GST_ONLY_PATHS": "", "GST_MAX_PENDING": "0", **extra,
        }
        return subprocess.run([sys.executable, "-"], input=PAYLOAD, env=environment, text=True,
                              capture_output=True, check=True).stdout

    def names(self, state):
        return sorted(p.stem for p in (Path(self.queue_dir) / state).glob("*.json"))

    def only(self, state):
        [name] = self.names(state)
        return name


class AdmissionTests(HoldCase):
    def test_auto_translates_what_the_script_queued_exactly_as_before(self):  # 11a
        self.bazarr(self.subtitle())
        self.assertTrue(self.worker("auto").process_once())
        self.assertEqual(self.attempt.run.call_count, 1)
        self.assertEqual(self.names("held"), [])
        self.assertEqual(len(self.names("done")), 1)

    def test_hold_sets_the_script_job_aside_without_calling_the_model(self):  # 11b
        self.bazarr(self.subtitle())
        queue_worker = self.worker("hold")
        self.assertFalse(queue_worker.process_once())
        self.attempt.run.assert_not_called()
        self.assertEqual(self.names("pending"), [])
        held = json.loads((Path(self.queue_dir) / "held" / f"{self.only('held')}.json").read_text())
        self.assertEqual((held["target_code"], held["source_code"]), ("vi", "en"))
        self.assertIn("held_at", held)
        self.assertEqual(queue.queue_snapshot(self.queue_dir)["counts"]["held"], 1)

    def test_what_the_admin_creates_is_never_held(self):  # 11c
        subtitle = self.subtitle()
        base = {"subtitle_path": str(subtitle), "source_code": "en"}
        queue.enqueue_translation_jobs(self.queue_dir, base, TARGETS)
        self.assertTrue(self.worker("hold").process_once())
        self.assertEqual(self.names("held"), [])
        self.assertEqual(self.attempt.run.call_count, 1)

        retranslation = {"subtitle_path": str(subtitle), "output_path": str(subtitle.with_suffix(".vi.srt")), "target_code": "vi",
                         "source_code": "en"}
        queue.enqueue_retranslation(self.queue_dir, retranslation)
        self.assertTrue(self.worker("hold").process_once())
        self.assertEqual(self.names("held"), [])

    def test_a_failed_job_the_admin_retries_runs_even_in_hold(self):
        job_id = Path(queue.enqueue_translation_jobs(self.queue_dir, {"subtitle_path": str(self.subtitle()), "source_code": "en"}, TARGETS)[0]).stem
        queue.JobQueue(self.queue_dir).process_once(Mock(side_effect=ValueError("boom")))
        self.assertTrue(queue.retry_failed_job(self.queue_dir, job_id))
        self.assertTrue(self.worker("hold").process_once())
        self.assertEqual(self.names("held"), [])

    def test_switching_the_mode_does_not_move_jobs_already_in_the_line(self):  # 11d
        self.bazarr(self.subtitle("Show.S01E01"))
        queue_worker = self.worker("auto")
        queue_worker.apply_settings({**queue_worker.settings, "admission_mode": "hold"})
        self.assertEqual((self.names("held"), len(self.names("pending"))), ([], 1))
        self.assertTrue(queue_worker.process_once())  # the job that was already waiting still runs
        self.assertEqual(self.attempt.run.call_count, 1)

        self.bazarr(self.subtitle("Show.S01E02"))  # new while in hold
        queue_worker.process_once()
        self.assertEqual(len(self.names("held")), 1)
        queue_worker.apply_settings({**queue_worker.settings, "admission_mode": "auto"})
        self.assertFalse(queue_worker.process_once())  # back to auto does not release what is held
        self.assertEqual((len(self.names("held")), self.names("pending")), (1, []))
        self.assertEqual(self.attempt.run.call_count, 1)

    def test_a_job_interrupted_in_auto_is_not_held_after_a_restart_into_hold(self):
        self.bazarr(self.subtitle())
        def interrupt(job, update_status):
            update_status("Sending batches")
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            queue.JobQueue(self.queue_dir).process_once(interrupt)  # claimed in auto, then the service died
        self.assertEqual(len(self.names("processing")), 1)
        restarted = self.worker("hold")  # recovers the interrupted job
        restarted.process_once()
        self.assertEqual(self.names("held"), [])
        self.assertEqual(self.attempt.run.call_count, 1)

    def test_held_jobs_survive_a_restart(self):  # 11e
        self.bazarr(self.subtitle())
        self.worker("hold").process_once()
        held = self.only("held")
        self.worker("hold")  # a new worker recovers interrupted work at startup
        self.worker("auto").process_once()
        self.assertEqual(self.names("held"), [held])
        self.attempt.run.assert_not_called()

    def test_a_held_job_does_not_count_against_max_pending_and_blocks_no_one(self):
        self.bazarr(self.subtitle("Show.S01E01"))
        self.worker("hold").process_once()
        self.assertEqual(queue.open_job_count(self.queue_dir), 0)
        self.assertIn("queued", self.bazarr(self.subtitle("Show.S01E02"), GST_MAX_PENDING="1"))

    def test_hold_leaves_a_job_outside_only_paths_to_end_as_skipped(self):  # FR-12.7
        inside = self.subtitle("In.S01E01", self.media / "Test")
        outside = self.subtitle("Out.S01E01", self.media / "Other")
        self.bazarr(inside)
        # The script itself refuses outside the guard; a job that got in some other way is not held either.
        job = {"job_id": "out", "subtitle_path": str(outside), "source_code": "en", "target_code": "vi",
               "output_path": str(outside.with_suffix(".vi.srt")), "provider": "embeddedsubtitles"}
        (Path(self.queue_dir) / "pending" / "out.json").write_text(json.dumps(job), encoding="utf-8")
        with patch.dict(os.environ, {"GST_ONLY_PATHS": str(self.media / "Test")}):
            queue_worker = self.worker("hold")
            queue_worker.process_once()
            self.assertEqual(len(self.names("held")), 1)  # the one inside
            self.assertEqual(self.names("pending"), [])  # the other was claimed, skipped, and finished
            self.assertEqual(self.only("done"), "out")
        self.attempt.run.assert_not_called()


class DecisionTests(HoldCase):
    def hold_two(self):
        first, second = self.subtitle("Show.S01E01"), self.subtitle("Show.S01E02")
        self.bazarr(first, series_id="5")
        self.bazarr(second, series_id="5")
        self.bazarr(self.subtitle("Movie"), series_id="")
        self.worker("hold").process_once()
        return first, second

    def test_approve_releases_a_job_that_then_runs_and_is_not_limited_by_max_pending(self):  # 11f
        self.hold_two()
        ids = [job.job_id for job in self.console.held()]
        self.assertEqual(len(ids), 3)
        with patch.dict(os.environ, {"GST_MAX_PENDING": "1"}):
            self.assertEqual(self.console.approve(job_ids=ids), ids)
        self.assertEqual(sorted(self.names("pending")), sorted(ids))
        worker_in_auto = self.worker("hold")
        for _ in ids:
            self.assertTrue(worker_in_auto.process_once())
        self.assertEqual(self.attempt.run.call_count, 3)  # approved jobs are not held a second time

    def test_approve_still_skips_a_language_that_appeared_while_waiting(self):  # 11g, FR-12.6
        first, _ = self.hold_two()
        first.with_suffix(".vi.srt").write_text("already translated", encoding="utf-8")
        self.console.approve(everything=True)
        queue_worker = self.worker("hold")
        while queue_worker.process_once():
            pass
        self.assertEqual(self.attempt.run.call_count, 2)  # the third needed no model call
        skipped = [json.loads(p.read_text()) for p in (Path(self.queue_dir) / "done").glob("*.json")]
        self.assertEqual(sorted(job["result"] for job in skipped), ["skipped", "translated", "translated"])

    def test_approve_by_series_takes_only_that_series(self):
        self.hold_two()
        approved = self.console.approve(series_id="5")
        self.assertEqual(len(approved), 2)
        self.assertEqual(len(self.names("held")), 1)

    def test_a_decision_names_its_jobs_in_exactly_one_way(self):
        self.hold_two()
        with self.assertRaises(ValueError):
            self.console.approve()
        with self.assertRaises(ValueError):
            self.console.approve(job_ids=[], everything=True)
        self.assertEqual(self.console.approve(job_ids=[]), [])
        self.assertEqual(len(self.names("held")), 3)

    def test_ids_that_are_not_held_or_not_file_names_are_ignored(self):
        self.hold_two()
        pending = Path(self.queue_dir) / "pending"
        (self.root / "outside.json").write_text(json.dumps({"job_id": "x"}), encoding="utf-8")
        self.assertEqual(self.console.approve(job_ids=["nope", "../../outside", "..", ""]), [])
        self.assertEqual(self.console.reject(job_ids=["../outside"]), [])
        self.assertTrue((self.root / "outside.json").exists())
        self.assertEqual(list(pending.glob("*.json")), [])

    def test_approval_is_refused_while_the_daily_quota_is_paused(self):
        self.hold_two()
        with patch.object(queue, "daily_quota_pause_until", return_value=9e12):
            with self.assertRaises(ValueError):
                self.console.approve(everything=True)
        self.assertEqual(len(self.names("held")), 3)

    def test_a_rejection_is_remembered_against_the_script_and_a_scan(self):  # 11h
        first, _ = self.hold_two()
        [rejected_id] = [job.job_id for job in self.console.held() if job.subtitle_path == str(first)]
        self.assertEqual(self.console.reject(job_ids=[rejected_id]), [rejected_id])
        self.assertEqual(self.names("rejected"), [rejected_id])

        self.bazarr(first, series_id="5")  # Bazarr runs the script for the same item again
        self.assertEqual(queue.enqueue_translation_jobs(  # and so does a scan from the console
            self.queue_dir, {"subtitle_path": str(first), "source_code": "en"}, TARGETS), [])
        queue_worker = self.worker("auto")  # even with hold off the "no" stands
        queue_worker.process_once()
        self.assertEqual((self.names("pending"), self.names("processing")), ([], []))
        self.assertEqual(self.names("rejected"), [rejected_id])
        self.attempt.run.assert_not_called()

    def test_a_rejected_job_can_be_restored_and_approved(self):
        self.hold_two()
        job_ids = [job.job_id for job in self.console.held()]
        self.console.reject(everything=True)
        self.assertEqual((self.names("held"), len(self.names("rejected"))), ([], 3))
        self.assertEqual(len(self.console.rejected()), 3)
        self.assertEqual(self.console.restore(job_ids=job_ids[:1]), job_ids[:1])
        self.console.approve(job_ids=job_ids[:1])
        self.assertTrue(self.worker("hold").process_once())
        self.assertEqual(self.attempt.run.call_count, 1)

    def test_a_held_job_is_not_deleted_around_the_remembered_no(self):
        self.hold_two()
        held = self.only_of_series()
        self.assertFalse(queue.delete_queue_job(self.queue_dir, "held", held))
        self.assertEqual(len(self.names("held")), 3)

    def only_of_series(self):
        return self.console.held()[0].job_id

    def test_the_listing_describes_each_job_for_the_inbox(self):  # FR-12.4
        first, _ = self.hold_two()
        job = next(job for job in self.console.held() if job.subtitle_path == str(first))
        self.assertEqual((job.series_id, job.source_code, job.target_code, job.cue_count), ("5", "en", "vi", 2))
        self.assertGreater(job.held_at, 0)
        self.assertGreater(job.created_at, 0)


class SettingTests(unittest.TestCase):
    def test_admission_mode_defaults_to_auto_and_ignores_unknown_values(self):
        self.assertEqual(config.normalize_app_config({})["admission_mode"], "auto")
        self.assertEqual(config.normalize_app_config({"admission_mode": " HOLD "})["admission_mode"], "hold")
        self.assertEqual(config.normalize_app_config({"admission_mode": "manual"})["admission_mode"], "auto")


if __name__ == "__main__":
    unittest.main()
