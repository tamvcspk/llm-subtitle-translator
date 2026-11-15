"""How an older queue settles into the table, and the button for a job that waits for earlier episodes (SPEC
criteria 13i, 14i; P8, P10). No network, no model."""

import json
import tempfile
import unittest
from pathlib import Path

from subserver.glossaries import Glossaries
from subserver.intake import MIGRATED_DIR, import_queue_tree
from subserver.jobs import FROM_LEGACY
from subserver.library import Library
from subserver.queue import JobQueue
from subserver.store import Store
from subserver.versions import TranslationVersions
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


class ImportTests(Intake):
    """P10 removed the script and its admission gate: what they left settles into the table once (ADR-10)."""

    def setUp(self):
        super().setUp()
        self.library = Library(
            self.store, Glossaries(self.store), TranslationVersions(self.store, str(self.root / "backups"))
        )

    def build_tree(self):
        self.write("processing", "running", stage="Sending subtitle batches to Gemini")
        self.write("deferred", "waits-503", deferred_reason="provider-unavailable", retry_at=5_000, last_error="503")
        self.write("deferred", "waits-gap", deferred_reason="context-gap", retry_at=6_000)
        self.write("done", "ok", result="translated", gst_model="m-1")
        self.write("done", "skip", result="skipped")
        self.write("failed", "bad", last_error="boom")
        (self.queue_dir / "failed" / "bad.error").write_text("Translation failed: boom", encoding="utf-8")
        self.write("held", "held-1", held_at=1_500)
        self.write("rejected", "no-1")
        self.write("pending", "fresh")  # what the script dropped and the worker had not taken yet
        (self.queue_dir / "provider-pause.json").write_text('{"reason": "daily-quota", "retry_at": 9}', encoding="utf-8")

    def settle(self):
        return import_queue_tree(self.queue, str(self.queue_dir), self.library)

    OLD = ("pending", "processing", "deferred", "done", "failed", "held", "rejected", "provider-pause.json")

    def test_13i_every_folder_becomes_the_right_thing_and_the_old_tree_is_kept_aside(self):
        self.build_tree()
        counts = self.settle()
        self.assertEqual(counts, {"imported": 7, "present": 0, "skipped": 0, "held": 1, "ignored": 1})
        self.assertEqual(self.names("pending"), ["fresh", "running"])  # `running` was running when the worker stopped
        self.assertEqual(self.names("deferred"), ["waits-503", "waits-gap"])
        self.assertEqual(self.queue.jobs.get("waits-503")["wait_reason"], "provider")
        self.assertEqual(self.queue.jobs.get("waits-gap")["wait_reason"], "context")
        self.assertEqual(self.queue.jobs.get("waits-503")["not_before"], 5_000)
        self.assertEqual(self.names("done"), ["ok", "skip"])
        self.assertEqual(self.queue.jobs.get("skip")["state"], "skipped")
        self.assertEqual(self.queue.jobs.get("ok")["result"], "translated")
        self.assertEqual(self.queue.jobs.get("bad")["error"], "Translation failed: boom")
        self.assertEqual(self.queue.jobs.get("fresh")["origin"], "script")
        for old in self.OLD:
            self.assertFalse((self.queue_dir / old).exists(), old)
            self.assertTrue((self.queue_dir / MIGRATED_DIR / old).exists(), old)
        self.assertEqual(json.loads((self.queue_dir / MIGRATED_DIR / "done" / "ok.json").read_text())["result"], "translated")

    def test_13i_a_held_job_is_Missing_again_not_a_job_and_a_rejected_one_is_Ignored(self):
        self.build_tree()
        self.settle()
        self.assertIsNone(self.queue.jobs.get("held-1"))  # "wanted, not decided" is what Missing says: no row, no model call
        self.assertIsNone(self.queue.jobs.get("no-1"))
        ignored = self.store.decisions()
        self.assertEqual({lang for _, lang in ignored}, {"vi"})
        ((unit_id, _),) = ignored
        self.assertEqual(self.store.get_unit(unit_id).source_path, "/m/no-1.en.srt")

    def test_13i_running_it_again_makes_nothing_twice_even_when_the_old_files_come_back(self):
        self.build_tree()
        self.settle()
        total = sum(self.queue.jobs.counts().values())
        decisions = self.store.decisions()
        zero = {"imported": 0, "present": 0, "skipped": 0, "held": 0, "ignored": 0}
        self.assertEqual(self.settle(), zero)
        self.build_tree()  # the same files again, as if a backup of the folders were restored
        again = self.settle()
        self.assertEqual((again["imported"], again["present"]), (0, 7))
        self.assertEqual(sum(self.queue.jobs.counts().values()), total)
        self.assertEqual(self.store.decisions(), decisions)
        self.assertEqual(len(list((self.queue_dir / MIGRATED_DIR).iterdir())), 2 * len(self.OLD))  # two generations, none deleted

    def test_13i_rows_the_gate_of_an_older_worker_left_are_settled_too(self):
        for name, state in (("gate-held", "held"), ("gate-no", "rejected")):
            self.queue.jobs.add(
                {"job_id": name, "subtitle_path": f"/m/{name}.en.srt", "output_path": f"/m/{name}.vi.srt",
                 "source_code": "en", "target_code": "vi"}, key=name, state=state, job_id=name,
            )
        self.assertEqual(self.queue.jobs.get("gate-held")["state"], "held")
        counts = self.settle()
        self.assertEqual((counts["held"], counts["ignored"]), (1, 1))
        self.assertEqual((self.queue.jobs.get("gate-held"), self.queue.jobs.get("gate-no")), (None, None))
        self.assertEqual(len(self.store.decisions()), 1)
        self.assertEqual(self.settle()["ignored"], 0)

    def test_a_file_that_is_not_json_is_left_out_and_counted(self):
        (self.queue_dir / "done").mkdir(parents=True)
        (self.queue_dir / "done" / "junk.json").write_text("not json", encoding="utf-8")
        self.assertEqual(self.settle()["skipped"], 1)

    def test_a_queue_that_was_never_there_is_fine(self):
        self.assertEqual(import_queue_tree(self.queue, str(self.queue_dir / "nowhere"), self.library),
                         {"imported": 0, "present": 0, "skipped": 0, "held": 0, "ignored": 0})

    def test_an_imported_job_runs_like_any_other(self):
        self.write("deferred", "waits-503", deferred_reason="provider-unavailable", retry_at=5_000)
        self.settle()
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
