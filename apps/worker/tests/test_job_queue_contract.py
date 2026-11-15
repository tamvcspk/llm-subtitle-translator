"""What the console may ask of the job queue (ADR-12: SQLite is the application's own store, so the contract is
checked on the real thing over ``:memory:``, not on a fake)."""

import tempfile
import unittest
from pathlib import Path

from gst_worker.jobs import FAILED, HELD, REJECTED, job_key
from gst_worker.ports import HeldJob
from gst_worker.queue import JobQueue
from gst_worker.store import Store

TARGETS = [{"code": "vi", "language": "Vietnamese", "enabled": True}, {"code": "de", "language": "German", "enabled": True}]


class JobQueueContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.subtitle = self.root / "Show.S01E01.en.srt"
        self.subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.repo = JobQueue(self.store)

    def state(self, job_id):
        return self.repo.jobs.state_of(job_id)

    def retranslation(self, **extra):
        return {"subtitle_path": str(self.subtitle), "output_path": str(self.subtitle.with_suffix(".vi.srt")),
                "target_code": "vi", **extra}

    def fail_job(self, job_id):
        self.repo.jobs.transition(job_id, FAILED, error="boom")

    def hold(self, name):
        """What the worker does to a script job in `hold` mode."""
        self.repo.jobs.add(
            {"subtitle_path": str(self.subtitle), "source_code": "en", "target_code": "vi", "held_at": 1},
            key=job_key(name, name, "vi"), state=HELD, job_id=name,
        )
        return name

    def test_enqueue_makes_one_job_per_enabled_target(self):
        made = self.repo.enqueue({"subtitle_path": str(self.subtitle), "source_code": "en"}, TARGETS)
        self.assertEqual(len(made), 2)
        self.assertEqual({self.state(job_id) for job_id in made}, {"queued"})

    def test_a_second_request_for_the_same_target_makes_no_second_job(self):
        self.repo.enqueue({"subtitle_path": str(self.subtitle), "source_code": "en"}, TARGETS)
        self.assertEqual(self.repo.enqueue({"subtitle_path": str(self.subtitle), "source_code": "en"}, TARGETS), [])

    def test_enqueue_ignores_a_subtitle_that_is_not_there(self):
        self.assertEqual(self.repo.enqueue({"subtitle_path": str(self.subtitle) + ".gone", "source_code": "en"}, TARGETS), [])

    def test_a_request_of_the_admin_is_the_interactive_class(self):
        (job_id,) = self.repo.enqueue({"subtitle_path": str(self.subtitle), "source_code": "en"}, TARGETS[:1])
        self.assertEqual(self.repo.jobs.get(job_id)["prio"], 200)

    def test_a_retranslation_returns_an_id_and_only_one_per_target_may_wait(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertTrue(job_id)
        self.assertEqual(self.state(job_id), "queued")
        self.assertEqual(self.repo.jobs.get(job_id)["mode"], "replace")
        with self.assertRaises(ValueError):
            self.repo.enqueue_retranslation(self.retranslation())

    def test_a_retranslation_turns_the_open_fill_job_of_the_same_file_into_the_replacement(self):
        (fill,) = self.repo.enqueue({"subtitle_path": str(self.subtitle), "source_code": "en"}, TARGETS[:1])
        job_id = self.repo.enqueue_retranslation(self.retranslation(output_path=self.repo.jobs.get(fill)["output_path"]))
        self.assertEqual(job_id, fill)  # no second job (FR-15.3)
        job = self.repo.jobs.get(fill)
        self.assertEqual((job["mode"], job["trigger"], job["prio"]), ("replace", "retranslate", 200))

    def test_retry_puts_a_failed_job_back_in_line_and_nothing_else(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertFalse(self.repo.retry(job_id))  # not failed
        self.assertFalse(self.repo.retry("unknown"))
        self.fail_job(job_id)
        self.assertTrue(self.repo.retry(job_id))
        self.assertEqual(self.state(job_id), "queued")

    def test_cancel_removes_a_failed_job_for_good(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertFalse(self.repo.cancel(job_id))  # still queued
        self.fail_job(job_id)
        self.assertTrue(self.repo.cancel(job_id))
        self.assertIsNone(self.state(job_id))
        self.assertFalse(self.repo.cancel(job_id))

    def test_delete_needs_the_right_state_and_cancels_what_waits(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertFalse(self.repo.delete("failed", job_id))
        self.assertTrue(self.repo.delete("pending", job_id))
        self.assertEqual(self.state(job_id), "cancelled")
        self.assertFalse(self.repo.delete("pending", job_id))

    def test_a_held_job_is_decided_not_deleted(self):
        job_id = self.hold("a")
        self.assertEqual([job.job_id for job in self.repo.held()], [job_id])
        self.assertIsInstance(self.repo.held()[0], HeldJob)
        self.assertFalse(self.repo.delete("held", job_id))
        self.assertEqual(self.state(job_id), HELD)

    def test_approve_moves_only_held_jobs_to_the_queue(self):
        job_id = self.hold("a")
        self.assertEqual(self.repo.approve([job_id, "unknown", job_id]), [job_id])
        self.assertEqual(self.state(job_id), "queued")
        self.assertEqual(self.repo.jobs.get(job_id)["admission"], "approved")
        self.assertEqual(self.repo.held(), [])
        self.assertEqual(self.repo.approve([job_id]), [])  # no longer held

    def test_reject_remembers_the_job_and_restore_returns_it_to_held(self):
        job_id = self.hold("a")
        self.assertEqual(self.repo.reject([job_id]), [job_id])
        self.assertEqual(self.state(job_id), REJECTED)
        self.assertEqual([job.job_id for job in self.repo.rejected()], [job_id])
        self.assertEqual(self.repo.approve([job_id]), [])  # a rejected job needs restoring first
        self.assertEqual(self.repo.restore([job_id]), [job_id])
        self.assertEqual(self.state(job_id), HELD)
        self.assertEqual(self.repo.restore([job_id]), [])


if __name__ == "__main__":
    unittest.main()
