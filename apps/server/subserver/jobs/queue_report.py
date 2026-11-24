"""Looking at the queue: ``ReportMixin`` holds the read side of ``JobQueue`` (``queue.py``): the snapshot the Activity
page shows, the report of the two model layers, the health of each model and the daily quota pause (FR-17.7).
"""

from __future__ import annotations

from typing import Any

from ..domain import queue_policy as policy
from .jobs import CANCELLED, DONE, FAILED, LEGACY_STATES, TRANSLATING_KINDS, FROM_LEGACY
from ..domain.queue_policy import Health


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class ReportMixin:
    def job_bundle(self, job_id: str):
        """The bundle of a job's latest attempt (FR-21.2), for "Xuất job spec"."""
        return self.jobs.bundle(job_id)

    def snapshot(self, limit: int = 100) -> dict[str, Any]:
        counts = self.jobs.counts()
        pause = self.quota_pause_until()
        snapshot: dict[str, Any] = {
            "counts": counts, "total": sum(counts.values()),
            "daily_quota_pause": {"retry_at": pause} if pause is not None else None,
            "providers": self.health_report(),
        }
        listed = {name: self.jobs.by_state(*FROM_LEGACY[name], limit=limit, newest_first=True) for name in LEGACY_STATES}
        names = self._unit_names({job["unit_id"] for jobs in listed.values() for job in jobs if _is_id(job.get("unit_id"))})
        for name, jobs in listed.items():
            for job in jobs:
                # A job with no episode (a ``suggest`` job) brings its own title; an older one falls back to its file.
                job["title"], job["label"] = names.get(job.get("unit_id"), (job.get("title") or "", job.get("label") or ""))
            snapshot[name] = jobs
        return snapshot

    def _unit_names(self, unit_ids: set[int]) -> dict[int, tuple[str, str]]:
        """``{unit id: (title, label)}`` for what the jobs are for: the series (or movie) and ``S01E04`` (empty for a
        movie). One query for the whole list: the console asks every few seconds. The payload only carries ids; a job made
        before units existed has neither, and the reader falls back to the file name."""
        if not unit_ids:
            return {}
        marks = ",".join("?" * len(unit_ids))
        rows = self.store.query(
            "SELECT u.id, u.title, u.season, u.episode, u.series_id, s.title AS series_title"
            f" FROM units u LEFT JOIN series s ON s.id = u.series_id WHERE u.id IN ({marks})",
            sorted(unit_ids),
        )
        out: dict[int, tuple[str, str]] = {}
        for row in rows:
            if row["series_id"] is None:
                out[row["id"]] = (row["title"] or "", "")
            else:
                label = f"S{row['season'] or 0:02d}E{row['episode'] or 0:02d}"
                out[row["id"]] = ((row["series_title"] or "") or (row["title"] or ""), label)
        return out

    def layer_report(self) -> dict[str, Any]:
        """How the two model layers are doing, from the jobs: is the strong model worth having?

        ``by_model`` counts finished translations per model; ``escalated`` counts jobs the default model could
        not finish (invalid output after repair) that moved to the strong model, and how those ended. If few
        jobs escalate, or the strong model does not rescue most of them, the layer is not earning its place.
        """
        by_model: dict[str, dict[str, int]] = {}
        escalated = {"total": 0, "done": 0, "failed": 0, "waiting": 0}
        for state, job in self.jobs.all_payloads(TRANSLATING_KINDS):
            if state == CANCELLED:
                continue
            # A finished translation is a job that ended ``translated`` or ``failed``: a candidate of a variant, or a job that
            # found its target already there, is neither a rescue nor a loss, and is not in ``by_model`` either.
            counted = state in (DONE, FAILED) and (state == FAILED or job.get("result") == "translated")
            if job.get("escalated_from") and (counted or state not in (DONE, FAILED)):
                escalated["total"] += 1
                escalated[state if state in (DONE, FAILED) else "waiting"] += 1
            if counted and job.get("gst_model"):
                counts = by_model.setdefault(str(job["gst_model"]), {"done": 0, "failed": 0})
                counts[state] += 1
        # The counts come from the job rows, and cleanup deletes finished ones past `retention_*_days` (FR-13.6):
        # the report says how far back it can see (0 = nothing is deleted, so it is the whole history).
        window = {
            "done_days": int(self._setting("retention_done_days", 30)),
            "failed_days": int(self._setting("retention_failed_days", 90)),
        }
        return {"by_model": by_model, "escalated": escalated, "window": window}

    def health_report(self) -> list[dict[str, Any]]:
        """The state of each configured model, as Settings and Insights show it (FR-17.7)."""
        now = self.clock()
        health = self.jobs.health_map()
        whole = health.get((self.provider, policy.ALL_MODELS), Health())
        report = []
        for model in self.models:
            h = health.get((self.provider, model), Health())
            shown = whole if whole.state == policy.INVALID else h
            until = shown.until if shown.state in (policy.OPEN, policy.EXHAUSTED) else None
            report.append({
                "provider": self.provider, "model": model, "state": shown.state, "reason": shown.reason,
                "until": until, "failures": h.failures, "usable": self._usable(model, health, now),
            })
        return report

    def quota_pause_until(self) -> float | None:
        """When every configured model has spent its daily quota, the earliest moment one of them is back."""
        if not self.models:
            return None
        now = self.clock()
        health = self.jobs.health_map()
        untils = []
        for model in self.models:
            h = health.get((self.provider, model), Health())
            if h.state != policy.EXHAUSTED or h.until <= now:
                return None
            untils.append(h.until)
        return min(untils)
