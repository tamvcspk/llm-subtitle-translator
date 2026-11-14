"""Queue use cases of the console: what the HTTP layer asks for, independent of the stdlib HTTP adapter.

The handler parses a request and calls one of these; building the job and choosing its targets happens here,
so a second front end (CLI, scheduler) would behave the same.
"""
from typing import Any

from .ports import HeldJob, JobQueue

CONSOLE_PROVIDER = "gemini-console"


class ConsoleActions:
    def __init__(self, jobs: JobQueue) -> None:
        self.jobs = jobs

    def retry(self, job_id: str) -> bool: return self.jobs.retry(job_id)
    def cancel(self, job_id: str) -> bool: return self.jobs.cancel(job_id)
    def delete(self, state: str, job_id: str) -> bool: return self.jobs.delete(state, job_id)
    def enqueue(self, base_job: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]: return self.jobs.enqueue(base_job, targets)

    # --- Held jobs (FR-12): the admin decides what the Bazarr script queued in `hold` mode ---

    def held(self) -> list[HeldJob]: return self.jobs.held()
    def rejected(self) -> list[HeldJob]: return self.jobs.rejected()

    def _select(self, jobs: list[HeldJob], job_ids: list[str] | None, series_id: str | None, everything: bool) -> list[str]:
        """Which jobs a decision covers: named jobs, every job of a series, or all of them (exactly one way)."""
        ways = [job_ids is not None, bool(series_id), bool(everything)]
        if sum(ways) != 1:
            raise ValueError("Name the jobs with exactly one of job_ids, series_id or all")
        if job_ids is not None:
            return [str(job_id) for job_id in job_ids]
        if series_id:
            return [job.job_id for job in jobs if job.series_id == str(series_id)]
        return [job.job_id for job in jobs]

    def approve(self, job_ids: list[str] | None = None, series_id: str | None = None, everything: bool = False) -> list[str]:
        """Release held jobs to the queue. They still go through the missing-language and no-overwrite checks
        when they run (FR-12.6), and `GST_MAX_PENDING` does not apply (FR-12.7)."""
        return self.jobs.approve(self._select(self.jobs.held(), job_ids, series_id, everything))

    def reject(self, job_ids: list[str] | None = None, series_id: str | None = None, everything: bool = False) -> list[str]:
        return self.jobs.reject(self._select(self.jobs.held(), job_ids, series_id, everything))

    def restore(self, job_ids: list[str] | None = None, everything: bool = False) -> list[str]:
        return self.jobs.restore(self._select(self.jobs.rejected(), job_ids, None, everything))

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
