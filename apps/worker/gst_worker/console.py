"""Console actions independent of the stdlib HTTP adapter."""
from typing import Any
from .queue import cancel_failed_job, delete_queue_job, enqueue_translation_jobs, retry_failed_job

class ConsoleActions:
    def __init__(self, queue_dir: str) -> None:
        self.queue_dir = queue_dir
    def retry(self, job_id: str) -> bool: return retry_failed_job(self.queue_dir, job_id)
    def cancel(self, job_id: str) -> bool: return cancel_failed_job(self.queue_dir, job_id)
    def delete(self, state: str, job_id: str) -> bool: return delete_queue_job(self.queue_dir, state, job_id)
    def enqueue(self, base_job: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]: return enqueue_translation_jobs(self.queue_dir, base_job, targets)
