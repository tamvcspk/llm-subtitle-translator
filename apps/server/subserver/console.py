"""Queue use cases of the console: what the HTTP layer asks for, independent of the stdlib HTTP adapter.

The handler parses a request and calls one of these; building the job and choosing its targets happens here,
so a second front end (CLI, scheduler) would behave the same.
"""
import json
from typing import Any

from .queue import JobQueue

CONSOLE_PROVIDER = "gemini-console"


class ConsoleActions:
    def __init__(self, jobs: JobQueue) -> None:
        self.jobs = jobs

    def retry(self, job_id: str) -> bool: return self.jobs.retry(job_id)
    def cancel(self, job_id: str) -> bool: return self.jobs.cancel(job_id)
    def delete(self, state: str, job_id: str) -> bool: return self.jobs.delete(state, job_id)
    def stop(self, job_id: str) -> str | None: return self.jobs.stop(job_id)

    def snapshot(self, limit: int = 100) -> dict[str, Any]: return self.jobs.snapshot(limit)
    def job_spec(self, job_id: str) -> dict[str, Any] | None:
        """The bundle a job translated from, as the JSON document `subcore translate --bundle` reads (FR-21.2)."""
        found = self.jobs.job_bundle(job_id)
        return None if found is None else json.loads(found.bundle_json)
    def layer_report(self) -> dict[str, Any]: return self.jobs.layer_report()


