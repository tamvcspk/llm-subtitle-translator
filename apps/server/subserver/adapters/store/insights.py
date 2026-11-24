"""The figures of Insights and their daily roll-up (P15, FR-19.5)."""

from __future__ import annotations

import json
from typing import Iterable

from ...domain.ports import CallDay, JobOutcomes, JobPrompt, MetricRow
from .base import Repo


class InsightsRepo(Repo):
    """The figures of Insights and their daily roll-up (P15, FR-19.5)."""

    # -- figures for Insights (P15, FR-19.5) -------------------------------------------------------
    def call_days(self, since: float) -> list[CallDay]:
        """Model calls since ``since`` grouped by UTC day, model, prompt version, task, kind and outcome."""
        rows = self.query(
            "SELECT strftime('%Y-%m-%d', at, 'unixepoch') AS day, model, prompt_version, task, kind, outcome,"
            " COUNT(*) AS calls, COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(tokens_out), 0) AS tokens_out,"
            " COALESCE(SUM(cost_est), 0) AS cost, COALESCE(SUM(latency_ms), 0) AS latency_ms,"
            " COALESCE(SUM(repairs), 0) AS repairs FROM llm_calls WHERE at >= ?"
            " GROUP BY day, model, prompt_version, task, kind, outcome ORDER BY day", (since,),
        )
        return [
            CallDay(
                day=r["day"], model=r["model"], prompt_version=r["prompt_version"], task=r["task"], kind=r["kind"],
                outcome=r["outcome"], calls=int(r["calls"]), tokens_in=int(r["tokens_in"]), tokens_out=int(r["tokens_out"]),
                cost=float(r["cost"]), latency_ms=int(r["latency_ms"]), repairs=int(r["repairs"]),
            )
            for r in rows
        ]

    def job_prompts(self, job_ids: Iterable[str]) -> list[JobPrompt]:
        """The prompt version each of these jobs translated with (an event or a signal names only the job). Only the
        translation calls say it: a job also reads story facts under its own prompt (``memory-x3``), which sorts first."""
        wanted = sorted({j for j in job_ids if j})
        out: list[JobPrompt] = []
        for start in range(0, len(wanted), self._PATH_CHUNK):
            chunk = wanted[start:start + self._PATH_CHUNK]
            rows = self.query(
                "SELECT job_id, MIN(prompt_version) AS prompt_version FROM llm_calls"
                f" WHERE job_id IN ({','.join('?' * len(chunk))}) AND task = 'translate' AND prompt_version <> ''"
                " GROUP BY job_id", chunk,
            )
            out.extend(JobPrompt(job_id=r["job_id"], prompt_version=r["prompt_version"]) for r in rows)
        return out

    def latencies(self, since: float, limit: int = 20_000) -> list[int]:
        """Milliseconds the answered calls took since ``since``, the newest ``limit`` of them."""
        rows = self.query(
            "SELECT latency_ms FROM llm_calls WHERE at >= ? AND outcome = 'ok' AND latency_ms > 0"
            " ORDER BY id DESC LIMIT ?", (since, int(limit)),
        )
        return [int(r["latency_ms"]) for r in rows]

    def job_waits(self, since: float, kinds: Iterable[str]) -> list[float]:
        """Seconds each job of these kinds waited between being asked for and starting, for jobs started since ``since``."""
        names = list(kinds)
        if not names:
            return []
        marks = ",".join("?" * len(names))
        rows = self.query(
            f"SELECT started_at - created_at AS waited FROM jobs WHERE started_at >= ? AND kind IN ({marks})"
            " AND started_at >= created_at", (since, *names),
        )
        return [float(r["waited"]) for r in rows]

    def job_outcomes(self, since: float, kinds: Iterable[str]) -> JobOutcomes:
        """How the jobs of these kinds that finished since ``since`` ended: done, or failed (a cancelled job is neither)."""
        names = list(kinds)
        if not names:
            return JobOutcomes(done=0, failed=0)
        marks = ",".join("?" * len(names))
        rows = self.query(
            f"SELECT state, COUNT(*) AS n FROM jobs WHERE finished_at >= ? AND state IN ('done', 'failed') AND kind IN ({marks})"
            " GROUP BY state", (since, *names),
        )
        by_state = {r["state"]: int(r["n"]) for r in rows}
        return JobOutcomes(done=by_state.get("done", 0), failed=by_state.get("failed", 0))

    def metric_rows(self, since_day: str) -> list[MetricRow]:
        rows = self.query(
            "SELECT day, name, labels_json, value FROM metrics_daily WHERE day >= ? ORDER BY day, name, labels_json",
            (since_day,),
        )
        return [
            MetricRow(
                day=r["day"], name=r["name"], labels=tuple(sorted(json.loads(r["labels_json"] or "{}").items())),
                value=float(r["value"]),
            )
            for r in rows
        ]

    def metrics_replace(self, first_day: str, names: Iterable[str], rows: Iterable[MetricRow]) -> None:
        """Replace the figures called ``names`` from ``first_day`` on with ``rows``, in one transaction: a half-rolled day is
        never seen."""
        wanted = list(names)
        marks = ",".join("?" * len(wanted))
        with self.transaction() as db:
            db.execute(f"DELETE FROM metrics_daily WHERE day >= ? AND name IN ({marks})", (first_day, *wanted))
            db.executemany(
                "INSERT OR REPLACE INTO metrics_daily (day, name, labels_json, value) VALUES (?, ?, ?, ?)",
                [(r.day, r.name, json.dumps(dict(r.labels), sort_keys=True, ensure_ascii=False), r.value) for r in rows],
            )

    def metric_marker(self, name: str) -> float:
        """How far a roll-up has got (epoch seconds), 0 before the first one."""
        rows = self.query("SELECT value FROM metrics_daily WHERE day = '0000-00-00' AND name = ?", (name,))
        return float(rows[0]["value"]) if rows else 0.0
