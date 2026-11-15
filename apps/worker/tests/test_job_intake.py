"""How jobs come in: the script's gate, the one-time move of an old queue folder tree, and the button for a job that
waits for earlier episodes (SPEC criteria 11, 13i, 14i; P8). No network, no model."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gst_worker.intake import MIGRATED_DIR, import_queue_tree, receive_script_jobs
from gst_worker.jobs import FROM_LEGACY
from gst_worker.queue import JobQueue
from gst_worker.store import Store
from tests.test_corrections import Env


class Intake(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.queue_dir = self.root / "queue"
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.queue = JobQueue(self.store)

    def write(self, state, job_id, **fields):
        folder = self.queue_dir / state
        folder.mkdir(parents=True, exist_ok=True)
        job = {"job_id": job_id, "subtitle_path": f"/m/{job_id}.en.srt", "output_path": f"/m/{job_id}.vi.srt",
               "source_code": "en", "target_code": "vi", "created_at": 1_000, **fields}
        (folder / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")
        return job

    def names(self, state):
        return sorted(job["job_id"] for job in self.queue.jobs.by_state(*FROM_LEGACY[state]))


class GateTests(Intake):
    def test_a_script_job_becomes_a_row_and_its_file_goes(self):
        self.write("pending", "a")
        self.assertEqual(receive_script_jobs(self.queue, str(self.queue_dir), "auto"), {"queued": 1, "held": 0, "dropped": 0})
        self.assertEqual(self.names("pending"), ["a"])
        self.assertEqual(list((self.queue_dir / "pending").glob("*.json")), [])
        job = self.queue.jobs.get("a")
        self.assertEqual((job["origin"], job["prio"], job["created_at"]), ("script", 0, 1_000))

    def test_a_half_written_file_waits_for_the_next_tick(self):
        (self.queue_dir / "pending").mkdir(parents=True)
        (self.queue_dir / "pending" / "x.json").write_text('{"job_id": "x", "subta', encoding="utf-8")
        self.assertEqual(receive_script_jobs(self.queue, str(self.queue_dir), "auto"), {"queued": 0, "held": 0, "dropped": 0})
        self.assertTrue((self.queue_dir / "pending" / "x.json").exists())

    def test_hold_sets_script_jobs_aside_but_never_the_admins(self):
        self.write("pending", "script")
        self.write("pending", "admin", admission="admin")
        self.assertEqual(receive_script_jobs(self.queue, str(self.queue_dir), "hold"), {"queued": 1, "held": 1, "dropped": 0})
        self.assertEqual((self.names("held"), self.names("pending")), (["script"], ["admin"]))
        self.assertGreater(self.queue.jobs.get("script")["held_at"], 0)

    def test_a_job_outside_only_paths_is_not_held(self):
        self.write("pending", "out", subtitle_path="/other/x.en.srt")
        with patch.dict(os.environ, {"GST_ONLY_PATHS": "/media/Test"}):
            self.assertEqual(receive_script_jobs(self.queue, str(self.queue_dir), "hold")["held"], 0)
        self.assertEqual(self.names("pending"), ["out"])  # it ends as skipped when it runs (FR-12.7)

    def test_a_job_already_in_line_held_or_rejected_is_dropped(self):
        for name in ("open", "held", "no"):
            self.write("pending", name)
        receive_script_jobs(self.queue, str(self.queue_dir), "auto")
        self.queue.jobs.transition("held", "held")
        self.queue.jobs.transition("no", "rejected")
        for name in ("open", "held", "no"):
            self.write("pending", name)  # Bazarr runs the script again for the same items
        self.assertEqual(receive_script_jobs(self.queue, str(self.queue_dir), "auto"), {"queued": 0, "held": 0, "dropped": 3})
        self.assertEqual((self.names("pending"), self.names("held"), self.names("rejected")), (["open"], ["held"], ["no"]))

    def test_max_pending_is_kept_by_the_gate_since_the_script_cannot_count_rows(self):
        for name in ("a", "b", "c"):
            self.write("pending", name)
        with patch.dict(os.environ, {"GST_MAX_PENDING": "2"}):
            counts = receive_script_jobs(self.queue, str(self.queue_dir), "auto")
        self.assertEqual((counts["queued"], counts["dropped"]), (2, 1))

    def test_a_script_job_for_a_file_that_failed_earlier_is_a_new_job(self):
        self.write("pending", "a")
        receive_script_jobs(self.queue, str(self.queue_dir), "auto")
        self.queue.jobs.transition("a", "failed", error="boom")
        self.write("pending", "a")
        self.assertEqual(receive_script_jobs(self.queue, str(self.queue_dir), "auto")["queued"], 1)
        self.assertEqual(self.queue.jobs.counts()["failed"], 1)  # the history stays
        self.assertEqual(len(self.names("pending")), 1)


class ImportTests(Intake):
    def build_tree(self):
        self.write("processing", "running", stage="Sending subtitle batches to Gemini")
        self.write("deferred", "waits-503", deferred_reason="provider-unavailable", retry_at=5_000, last_error="503")
        self.write("deferred", "waits-gap", deferred_reason="context-gap", retry_at=6_000)
        self.write("done", "ok", result="translated", gst_model="m-1")
        self.write("done", "skip", result="skipped")
        self.write("failed", "bad", last_error="boom")
        (self.queue_dir / "failed" / "bad.error").write_text("Translation failed: boom", encoding="utf-8")
        self.write("held", "held-1", held_at=1_500, admission=None)
        self.write("rejected", "no-1")
        self.write("pending", "fresh")  # the script's gate: not the importer's business
        (self.queue_dir / "provider-pause.json").write_text('{"reason": "daily-quota", "retry_at": 9}', encoding="utf-8")

    def test_every_folder_becomes_the_right_state_and_the_old_tree_is_kept_aside(self):
        self.build_tree()
        counts = import_queue_tree(self.queue, str(self.queue_dir))
        self.assertEqual(counts, {"imported": 8, "present": 0, "skipped": 0})
        self.assertEqual(self.names("pending"), ["running"])  # it was running when the worker stopped: back in line
        self.assertEqual(self.names("deferred"), ["waits-503", "waits-gap"])
        self.assertEqual(self.queue.jobs.get("waits-503")["wait_reason"], "provider")
        self.assertEqual(self.queue.jobs.get("waits-gap")["wait_reason"], "context")
        self.assertEqual(self.queue.jobs.get("waits-503")["not_before"], 5_000)
        self.assertEqual(self.names("done"), ["ok", "skip"])
        self.assertEqual(self.queue.jobs.get("skip")["state"], "skipped")
        self.assertEqual(self.queue.jobs.get("ok")["result"], "translated")
        self.assertEqual(self.queue.jobs.get("bad")["error"], "Translation failed: boom")
        self.assertEqual((self.names("held"), self.names("rejected")), (["held-1"], ["no-1"]))
        self.assertTrue((self.queue_dir / "pending" / "fresh.json").exists())  # still for the gate
        for old in ("processing", "deferred", "done", "failed", "held", "rejected", "provider-pause.json"):
            self.assertFalse((self.queue_dir / old).exists(), old)
            self.assertTrue((self.queue_dir / MIGRATED_DIR / old).exists(), old)
        self.assertEqual(json.loads((self.queue_dir / MIGRATED_DIR / "done" / "ok.json").read_text())["result"], "translated")

    def test_running_it_again_makes_no_job_twice_even_when_the_old_files_come_back(self):
        """SPEC criterion 13i: the importer is idempotent by job id."""
        self.build_tree()
        import_queue_tree(self.queue, str(self.queue_dir))
        total = sum(self.queue.jobs.counts().values())
        self.assertEqual(import_queue_tree(self.queue, str(self.queue_dir)), {"imported": 0, "present": 0, "skipped": 0})
        self.build_tree()  # the same files again, as if a backup of the folders were restored
        again = import_queue_tree(self.queue, str(self.queue_dir))
        self.assertEqual((again["imported"], again["present"]), (0, 8))
        self.assertEqual(sum(self.queue.jobs.counts().values()), total)
        self.assertEqual(len(list((self.queue_dir / MIGRATED_DIR).iterdir())), 14)  # two generations are kept, none deleted

    def test_a_file_that_is_not_json_is_left_out_and_counted(self):
        (self.queue_dir / "done").mkdir(parents=True)
        (self.queue_dir / "done" / "junk.json").write_text("not json", encoding="utf-8")
        self.assertEqual(import_queue_tree(self.queue, str(self.queue_dir))["skipped"], 1)

    def test_a_queue_that_was_never_there_is_fine(self):
        self.assertEqual(import_queue_tree(self.queue, str(self.queue_dir / "nowhere")), {"imported": 0, "present": 0, "skipped": 0})

    def test_an_imported_job_runs_like_any_other(self):
        self.write("deferred", "waits-503", deferred_reason="provider-unavailable", retry_at=5_000)
        import_queue_tree(self.queue, str(self.queue_dir))
        self.assertFalse(self.queue.process_once(lambda job, update: "translated", now=4_999))
        self.assertTrue(self.queue.process_once(lambda job, update: "translated", now=5_000))
        self.assertEqual(self.queue.jobs.state_of("waits-503"), "done")


class PrerequisiteTests(unittest.TestCase):
    def test_14i_the_button_queues_the_earlier_episodes_whose_source_is_there_and_names_the_rest(self):
        env = Env(self)
        for episode in (1, 2, 3):
            env.translate(episode)
        first, second = env.unit(1), env.unit(2)
        Path(env.output(1)).unlink()  # episode 1 can be translated again
        Path(env.output(2)).unlink()
        Path(env.job(2)["subtitle_path"]).unlink()  # episode 2 has no source: nothing to translate yet
        env.jobs.jobs.add(env.job(3), key="gap", job_id="gap")
        env.jobs.jobs.transition(
            "gap", "waiting", wait_reason="context", not_before=9e9,
            payload={**env.job(3), "missing_units": ["S01E01", "S01E02"], "missing_unit_ids": [first, second]},
        )

        result = env.service.request_prerequisites("gap")

        self.assertEqual(len(result["requested"]), 1)
        self.assertEqual(result["unavailable"], ["S01E02"])
        queued = env.jobs.jobs.get(result["requested"][0])
        self.assertEqual((queued["state"], queued["prio"], queued["unit_id"]), ("queued", 200, first))
        self.assertEqual(env.jobs.jobs.state_of("gap"), "waiting")  # the job itself goes on waiting
        self.assertEqual(env.service.request_prerequisites("gap")["requested"], [])  # asking twice makes no second job

    def test_only_a_job_waiting_for_context_has_the_button(self):
        env = Env(self)
        env.jobs.jobs.add(env.job(1), key="k", job_id="plain")
        with self.assertRaises(ValueError):
            env.service.request_prerequisites("plain")
        with self.assertRaises(ValueError):
            env.service.request_prerequisites("nope")


if __name__ == "__main__":
    unittest.main()
