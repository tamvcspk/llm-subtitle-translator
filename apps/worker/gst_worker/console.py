"""Console actions independent of the stdlib HTTP adapter."""
from typing import Any

from .ports import JobQueue


class ConsoleActions:
    def __init__(self, jobs: JobQueue) -> None:
        self.jobs = jobs

    def retry(self, job_id: str) -> bool: return self.jobs.retry(job_id)
    def cancel(self, job_id: str) -> bool: return self.jobs.cancel(job_id)
    def delete(self, state: str, job_id: str) -> bool: return self.jobs.delete(state, job_id)
    def enqueue(self, base_job: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]: return self.jobs.enqueue(base_job, targets)
