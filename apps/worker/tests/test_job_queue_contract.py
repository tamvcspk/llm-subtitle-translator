"""Contract of the JobQueue port (SPEC criterion 10b): the same checks on the queue directory and on a fake."""

import tempfile
import unittest
from pathlib import Path

from gst_worker.ports import JobQueue
from gst_worker.queue import FileJobQueue

TARGETS = [{"code": "vi", "language": "Vietnamese", "enabled": True}, {"code": "de", "language": "German", "enabled": True}]


class InMemoryJobQueue:
    """Fake JobQueue: jobs are ids in named states."""

    def __init__(self) -> None:
        self.states: dict[str, str] = {}
        self.retranslations: set[str] = set()
        self._count = 0

    def _new(self) -> str:
        self._count += 1
        return f"job-{self._count}"

    def enqueue(self, base_job, targets):
        if not Path(str(base_job.get("subtitle_path", ""))).exists():
            return []
        made = []
        for target in targets:
            if target.get("enabled") and str(target["code"]) != str(base_job.get("source_code", "en")):
                job_id = self._new()
                self.states[job_id] = "pending"
                made.append(job_id)
        return made

    def enqueue_retranslation(self, job):
        key = (job["subtitle_path"], job["output_path"], job["target_code"])
        if key in self.retranslations:
            raise ValueError("A retranslation of this subtitle is already queued")
        self.retranslations.add(key)
        job_id = self._new()
        self.states[job_id] = "pending"
        return job_id

    def retry(self, job_id):
        if self.states.get(job_id) != "failed":
            return False
        self.states[job_id] = "pending"
        return True

    def cancel(self, job_id):
        return self.delete("failed", job_id)

    def delete(self, state, job_id):
        if self.states.get(job_id) != state:
            return False
        del self.states[job_id]
        return True


class JobQueueContract:
    """Mixed into a TestCase that provides ``self.repo``, ``self.subtitle`` and ``fail`` / ``state`` helpers."""

    repo: JobQueue

    def retranslation(self, **extra):
        return {"subtitle_path": str(self.subtitle), "output_path": str(self.subtitle.with_suffix(".vi.srt")),
                "target_code": "vi", **extra}

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.repo, JobQueue)

    def test_enqueue_makes_one_job_per_enabled_target(self):
        made = self.repo.enqueue({"subtitle_path": str(self.subtitle), "source_code": "en"}, TARGETS)
        self.assertEqual(len(made), 2)

    def test_enqueue_ignores_a_subtitle_that_is_not_there(self):
        self.assertEqual(self.repo.enqueue({"subtitle_path": str(self.subtitle) + ".gone", "source_code": "en"}, TARGETS), [])

    def test_a_retranslation_returns_an_id_and_only_one_per_target_may_wait(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertTrue(job_id)
        self.assertEqual(self.state(job_id), "pending")
        with self.assertRaises(ValueError):
            self.repo.enqueue_retranslation(self.retranslation())

    def test_retry_puts_a_failed_job_back_in_line_and_nothing_else(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertFalse(self.repo.retry(job_id))  # not failed
        self.assertFalse(self.repo.retry("unknown"))
        self.fail_job(job_id)
        self.assertTrue(self.repo.retry(job_id))
        self.assertEqual(self.state(job_id), "pending")

    def test_cancel_removes_a_failed_job_for_good(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertFalse(self.repo.cancel(job_id))  # still pending
        self.fail_job(job_id)
        self.assertTrue(self.repo.cancel(job_id))
        self.assertIsNone(self.state(job_id))
        self.assertFalse(self.repo.cancel(job_id))

    def test_delete_needs_the_right_state(self):
        job_id = self.repo.enqueue_retranslation(self.retranslation())
        self.assertFalse(self.repo.delete("failed", job_id))
        self.assertTrue(self.repo.delete("pending", job_id))
        self.assertFalse(self.repo.delete("pending", job_id))


class FileJobQueueTests(JobQueueContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.subtitle = self.root / "Show.S01E01.en.srt"
        self.subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
        self.queue_dir = str(self.root / "queue")
        self.repo = FileJobQueue(self.queue_dir)

    def state(self, job_id):
        found = [p.parent.name for p in Path(self.queue_dir).glob(f"*/{job_id}.json")]
        return found[0] if found else None

    def fail_job(self, job_id):
        (Path(self.queue_dir) / "pending" / f"{job_id}.json").replace(Path(self.queue_dir) / "failed" / f"{job_id}.json")


class InMemoryJobQueueTests(JobQueueContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.subtitle = Path(directory.name) / "Show.S01E01.en.srt"
        self.subtitle.write_text("x", encoding="utf-8")
        self.repo = InMemoryJobQueue()

    def state(self, job_id):
        return self.repo.states.get(job_id)

    def fail_job(self, job_id):
        self.repo.states[job_id] = "failed"


if __name__ == "__main__":
    unittest.main()
