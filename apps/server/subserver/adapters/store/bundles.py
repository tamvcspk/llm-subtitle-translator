"""The bundle of a job's latest attempt (P11, FR-21.2)."""

from __future__ import annotations

from ...domain.ports import JobBundle
from .base import Repo


class BundleRepo(Repo):
    """The bundle of a job's latest attempt (P11, FR-21.2)."""

    # -- job bundles (P11, FR-21.2) ------------------------------------------------
    def save_job_bundle(self, job_id: str, bundle_hash: str, bundle_json: str) -> None:
        """Keep the bundle an attempt of this job translated from. One short write; the caller decides what a failure
        means (the translation itself does not depend on it)."""
        with self.transaction() as db:
            # Only for a job that has a row: a bundle without a job would never be swept.
            db.execute(
                "INSERT OR REPLACE INTO job_bundles (job_id, bundle_hash, bundle_json)"
                " SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM jobs WHERE job_id = ?)",
                (job_id, bundle_hash, bundle_json, job_id),
            )

    def job_bundle(self, job_id: str) -> JobBundle | None:
        rows = self.query("SELECT bundle_hash, bundle_json FROM job_bundles WHERE job_id = ?", (job_id,))
        if not rows:
            return None
        return JobBundle(job_id=job_id, bundle_hash=rows[0]["bundle_hash"], bundle_json=rows[0]["bundle_json"])
