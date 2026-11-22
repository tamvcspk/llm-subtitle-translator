"""A queue on SQLite with a fake clock, for tests that need to drive the scheduler (P8)."""

import unittest

from subserver.domain import queue_policy as policy
from subserver.jobs.jobs import job_key
from subserver.jobs.queue import JobQueue
from subserver.adapters.store import Store

START = 1_800_000_000.0  # a Thursday, 2027-01-15 08:00 UTC: far from any midnight


class Clock:
    def __init__(self, now=START):
        self.now = now
        self.mono = 1000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.now += seconds
        self.mono += seconds


class SchedulerCase(unittest.TestCase):
    MODELS = ("light",)

    def setUp(self):
        self.clock = Clock()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.queue = JobQueue(self.store, clock=self.clock.time, monotonic=self.clock.monotonic)
        self.configure()
        self.n = 0

    def configure(self, models=None, **settings):
        self.queue.configure(settings, tuple(models if models is not None else self.MODELS), "gemini")

    def add(self, name=None, *, series="", prio=policy.AUTO, created_at=None, **extra):
        self.n += 1
        name = name or f"job{self.n}"
        payload = {"subtitle_path": f"/media/{name}.en.srt", "output_path": f"/media/{name}.vi.srt", "target_code": "vi",
                   "series_id": series, **extra}
        added = self.queue.jobs.add(payload, key=job_key(payload["subtitle_path"], payload["output_path"], "vi"),
                                    prio=prio, job_id=name, created_at=created_at)
        self.assertEqual(added.job_id, name)
        return name

    def tick(self, execute=None, advance=0):
        self.clock.advance(advance)
        seen = []

        def run(job, update_status):
            seen.append(job["job_id"])
            return execute(job, update_status) if execute else "translated"

        ran = self.queue.process_once(run)
        return seen[0] if ran and seen else (None if not ran else "")

    def state(self, name):
        return self.queue.jobs.state_of(name)

    def job(self, name):
        return self.queue.jobs.get(name)

    def drain(self, execute=None, step=1):
        order = []
        while True:
            name = self.tick(execute, advance=step)
            if not name:
                return order
            order.append(name)
