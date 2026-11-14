"""Queue use cases of the console: what the HTTP layer asks for, independent of the stdlib HTTP adapter.

The handler parses a request and calls one of these; building the job and choosing its targets happens here,
so a second front end (CLI, scheduler) would behave the same.
"""
from typing import Any

from .ports import JobQueue

CONSOLE_PROVIDER = "gemini-console"


class ConsoleActions:
    def __init__(self, jobs: JobQueue) -> None:
        self.jobs = jobs

    def retry(self, job_id: str) -> bool: return self.jobs.retry(job_id)
    def cancel(self, job_id: str) -> bool: return self.jobs.cancel(job_id)
    def delete(self, state: str, job_id: str) -> bool: return self.jobs.delete(state, job_id)
    def enqueue(self, base_job: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]: return self.jobs.enqueue(base_job, targets)

    def enqueue_subtitle(self, request: dict[str, Any], configured_targets: list[dict[str, Any]]) -> list[str]:
        """Queue one subtitle the admin picked: one job per configured target, or only the codes the request names."""
        targets = configured_targets
        codes = request.get("target_codes")
        if isinstance(codes, list) and codes:
            wanted = {str(code) for code in codes}
            targets = [target for target in targets if str(target["code"]) in wanted]
        return self.jobs.enqueue(
            {
                "video_path": request.get("video_path", ""),
                "subtitle_path": request.get("subtitle_path", ""),
                "provider": request.get("provider", CONSOLE_PROVIDER),
                "source_code": request.get("source_code") or request.get("language") or "en",
                "source_language": request.get("source_language") or "English",
                "series_id": request.get("series_id", ""),
                "media_id": request.get("media_id", ""),
                "media_type": request.get("media_type") or request.get("type") or "movie",
            },
            targets,
        )

    def enqueue_found(self, found: list[dict[str, Any]]) -> list[str]:
        """Queue what a library scan found: each item with the targets it is still missing."""
        created: list[str] = []
        for item in found:
            created.extend(
                self.jobs.enqueue(
                    {
                        "video_path": item["video_path"],
                        "subtitle_path": item["subtitle_path"],
                        "provider": CONSOLE_PROVIDER,
                        "source_code": item["source_code"],
                        "source_language": item["source_language"],
                        "media_type": "movie",
                    },
                    item["missing_targets"],
                )
            )
        return created
