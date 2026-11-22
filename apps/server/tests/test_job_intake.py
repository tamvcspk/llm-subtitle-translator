"""How an older queue settles into the table, and the button for a job that waits for earlier episodes (SPEC
criteria 13i, 14i; P8, P10). No network, no model."""

import json
import tempfile
import unittest
from pathlib import Path

from subserver.services.glossaries import Glossaries
from subserver.jobs.intake import MIGRATED_DIR, import_queue_tree
from subserver.jobs.jobs import FROM_LEGACY
from subserver.services.library import Library
from subserver.jobs.queue import JobQueue
from subserver.adapters.store import Store
from subserver.services.versions import TranslationVersions
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
    ZERO = {"imported": 0, "present": 0, "skipped": 0, "held": 0, "unrequested": 0, "ignored": 0, "unsettled": 0}

    def test_13i_every_folder_becomes_the_right_thing_and_the_old_tree_is_kept_aside(self):
        self.build_tree()
        counts = self.settle()
        self.assertEqual(counts, {"imported": 6, "present": 0, "skipped": 0, "held": 1, "unrequested": 1, "ignored": 1,
                                  "unsettled": 0})
        self.assertEqual(self.names("pending"), ["running"])  # `running` was running when the worker stopped
        self.assertEqual(self.names("deferred"), ["waits-503", "waits-gap"])
        self.assertEqual(self.queue.jobs.get("waits-503")["wait_reason"], "provider")
        self.assertEqual(self.queue.jobs.get("waits-gap")["wait_reason"], "context")
        self.assertEqual(self.queue.jobs.get("waits-503")["not_before"], 5_000)
        self.assertEqual(self.names("done"), ["ok", "skip"])
        self.assertEqual(self.queue.jobs.get("skip")["state"], "skipped")
        self.assertEqual(self.queue.jobs.get("ok")["result"], "translated")
        self.assertEqual(self.queue.jobs.get("bad")["error"], "Translation failed: boom")
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
        self.assertEqual(self.settle(), self.ZERO)
        self.build_tree()  # the same files again, as if a backup of the folders were restored
        again = self.settle()
        self.assertEqual((again["imported"], again["present"]), (0, 6))
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

    def add_gate_rows(self):
        for name, state in (("gate-held", "held"), ("gate-no", "rejected")):
            self.queue.jobs.add(
                {"job_id": name, "subtitle_path": f"/m/{name}.en.srt", "output_path": f"/m/{name}.vi.srt",
                 "source_code": "en", "target_code": "vi"}, key=name, state=state, job_id=name,
            )

    def test_r10_5_what_the_script_dropped_and_nobody_approved_is_Missing_not_a_job(self):
        self.write("pending", "fresh")
        self.write("pending", "fresh-2")
        self.write("pending", "mine", admission="admin")  # an explicit request of the admin is still a request
        self.write("pending", "again", trigger="retranslate", priority=100)
        counts = self.settle()
        self.assertEqual((counts["imported"], counts["unrequested"]), (2, 2))
        self.assertEqual(sorted(j["job_id"] for j in self.queue.jobs.by_state("queued")), ["again", "mine"])
        self.assertIsNone(self.queue.jobs.get("fresh"))
        self.assertTrue((self.queue_dir / MIGRATED_DIR / "pending" / "fresh.json").exists())  # kept aside, not deleted

    def test_r10_5_the_start_up_says_how_many_were_not_queued(self):
        self.write("pending", "fresh")
        with self.assertLogs(level="WARNING") as logged:
            self.settle()
        self.assertIn("1 job(s)", "\n".join(logged.output))

    def test_r10_2_a_refusal_that_cannot_be_written_yet_stays_and_is_tried_again(self):
        self.write("rejected", "no-1")
        self.write("done", "ok", result="translated")
        self.add_gate_rows()
        counts = import_queue_tree(self.queue, str(self.queue_dir), None)  # no library: nothing can be recorded
        self.assertEqual((counts["ignored"], counts["unsettled"], counts["held"], counts["imported"]), (0, 2, 1, 1))
        self.assertEqual(self.store.decisions(), set())
        self.assertTrue((self.queue_dir / "rejected" / "no-1.json").exists())  # the folder was not put aside
        self.assertFalse((self.queue_dir / "done").exists())  # the rest was
        self.assertIsNone(self.queue.jobs.get("gate-held"))
        self.assertEqual(self.queue.jobs.get("gate-no")["state"], "rejected")  # the row is the record of that "no"

        again = self.settle()  # the next start-up has the library
        self.assertEqual((again["ignored"], again["unsettled"]), (2, 0))
        self.assertEqual(len(self.store.decisions()), 2)
        self.assertIsNone(self.queue.jobs.get("gate-no"))
        self.assertTrue((self.queue_dir / MIGRATED_DIR / "rejected" / "no-1.json").exists())

    def test_r10_2_a_refusal_that_cannot_be_placed_is_kept_not_lost(self):
        self.add_gate_rows()
        real = self.library.register_job
        self.library.register_job = lambda job: (_ for _ in ()).throw(RuntimeError("database is locked"))
        with self.assertLogs(level="WARNING"):
            counts = self.settle()
        self.assertEqual((counts["ignored"], counts["unsettled"]), (0, 1))
        self.assertEqual(self.queue.jobs.get("gate-no")["state"], "rejected")
        self.library.register_job = real
        self.assertEqual(self.settle()["ignored"], 1)
        self.assertEqual(len(self.store.decisions()), 1)

    def test_r10_2_the_decision_is_written_before_the_row_goes(self):
        self.add_gate_rows()
        order = []
        real_set, real_drop = self.library.catalog.set_decision, self.queue.jobs.drop_gate_row
        self.library.catalog.set_decision = lambda *a, **k: (order.append("decision"), real_set(*a, **k))[1]
        self.queue.jobs.drop_gate_row = lambda row_id: (order.append("drop"), real_drop(row_id))[1]
        self.settle()
        self.assertEqual(order, ["drop", "decision", "drop"])  # the held row has no decision to write

    def test_r10_2_a_crash_between_the_decision_and_the_row_loses_nothing(self):
        self.add_gate_rows()
        real = self.queue.jobs.drop_gate_row
        self.queue.jobs.drop_gate_row = lambda row_id: (_ for _ in ()).throw(RuntimeError("crash"))
        with self.assertRaises(RuntimeError):
            self.settle()
        self.queue.jobs.drop_gate_row = real
        self.assertEqual(len(self.store.decisions()), 0)  # crashed on the held row before the rejected one
        self.queue.jobs.drop_gate_row = lambda row_id: real(row_id) if row_id == 1 else (_ for _ in ()).throw(RuntimeError("crash"))
        with self.assertRaises(RuntimeError):
            self.settle()
        self.queue.jobs.drop_gate_row = real
        self.assertEqual(len(self.store.decisions()), 1)  # the "no" was written before the crash
        self.assertEqual(self.queue.jobs.get("gate-no")["state"], "rejected")  # and the row is still there
        self.assertEqual(self.settle()["ignored"], 1)
        self.assertEqual(len(self.store.decisions()), 1)  # the same decision, not a second one
        self.assertIsNone(self.queue.jobs.get("gate-no"))

    def test_a_refusal_that_names_no_language_is_dropped_not_kept_forever(self):
        self.write("rejected", "odd", target_code="")
        counts = self.settle()
        self.assertEqual((counts["ignored"], counts["unsettled"]), (0, 0))
        self.assertTrue((self.queue_dir / MIGRATED_DIR / "rejected" / "odd.json").exists())

    def test_a_file_that_is_not_json_is_left_out_and_counted(self):
        (self.queue_dir / "done").mkdir(parents=True)
        (self.queue_dir / "done" / "junk.json").write_text("not json", encoding="utf-8")
        self.assertEqual(self.settle()["skipped"], 1)

    def test_a_queue_that_was_never_there_is_fine(self):
        self.assertEqual(import_queue_tree(self.queue, str(self.queue_dir / "nowhere"), self.library), self.ZERO)

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

        # The price first: it names what it would cost and what it cannot ask for, and makes no job.
        price = env.service.request_prerequisites("gap", estimate_only=True)
        self.assertEqual((price["estimate_only"], price["planned_total"], price["estimate"]["jobs"]), (True, 1, 1))
        self.assertEqual((price["unavailable"], price["skipped_by_reason"]), (["S01E02"], {"no source subtitle": 1}))
        self.assertEqual(price["planned"][0]["title"], "S01E01")
        self.assertEqual(env.jobs.jobs.by_state("queued"), [])

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
