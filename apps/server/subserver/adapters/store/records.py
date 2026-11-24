"""Model calls, events, quality signals and the index of kept prompt bodies (P5, ADR-15)."""

from __future__ import annotations

import json
import time
from typing import Any, Iterable

from ...domain.ports import BodyRef, EventRecord, LlmCall, QualitySignal, TokenTotals
from .base import Repo


class RecordsRepo(Repo):
    """Model calls, events, quality signals and the index of kept prompt bodies (P5, ADR-15)."""

    # -- llm_calls ---------------------------------------------------------
    def record_llm_call(
        self,
        job_id: str,
        *,
        provider: str,
        model: str,
        task: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        outcome: str,
        at: float | None = None,
        kind: str = "",
        prompt_version: str = "",
        bundle_hash: str = "",
        style_hash: str = "",
        glossary_hash: str = "",
        batch: int = 0,
        http_status: int | None = None,
        finish_reason: str = "",
        retries: int = 0,
        repairs: int = 0,
        cost_est: float = 0.0,
        series_id: int | None = None,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO llm_calls (job_id, provider, model, task, tokens_in, tokens_out, latency_ms, outcome, at,"
                " kind, prompt_version, bundle_hash, style_hash, glossary_hash, batch, http_status, finish_reason,"
                " retries, repairs, cost_est, series_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, provider, model, task, tokens_in, tokens_out, latency_ms, outcome, at or time.time(),
                 kind, prompt_version, bundle_hash, style_hash, glossary_hash, batch, http_status, finish_reason,
                 retries, repairs, cost_est, series_id),
            )
            return int(cursor.lastrowid)

    def calls_for_series(self, series_id: int, task: str) -> int:
        """Calls of one task recorded for one series, over its whole life: what ``research_max_calls_per_series`` counts
        (FR-7.6). Read from the calls, not from ``jobs``, whose finished rows are cleaned up. A try the provider turned
        away (busy, down, out of quota) is not a call of the budget: one call is recorded once per try, and two outages
        would otherwise spend a series' whole life of research."""
        return int(self.query(
            "SELECT COUNT(*) AS n FROM llm_calls WHERE series_id = ? AND task = ?"
            " AND outcome NOT IN ('rate_limited', 'unavailable', 'quota_exhausted')", (int(series_id), task)
        )[0]["n"])

    def calls_since(self, at: float) -> int:
        """Model calls recorded since ``at`` (a Unix time): what `max_calls_per_day` counts (FR-17.6)."""
        return int(self.query("SELECT COUNT(*) AS n FROM llm_calls WHERE at >= ?", (at,))[0]["n"])

    def average_call_seconds(self, sample: int = 200) -> float | None:
        """How long a recent model call took, on average, in seconds; None before any call was recorded."""
        row = self.query(
            "SELECT AVG(latency_ms) AS ms, COUNT(*) AS n FROM (SELECT latency_ms FROM llm_calls"
            " WHERE latency_ms > 0 ORDER BY id DESC LIMIT ?)", (int(sample),),
        )[0]
        return float(row["ms"]) / 1000.0 if row["n"] else None

    def policy_jobs_since(self, at: float) -> int:
        """Jobs the ``auto_request`` policy created since ``at``: what ``auto_request_daily_cap`` counts (FR-15.4)."""
        return int(self.query(
            "SELECT COUNT(*) AS n FROM jobs WHERE origin = 'policy' AND created_at >= ?", (at,)
        )[0]["n"])

    def llm_calls(self, job_id: str | None = None) -> list[LlmCall]:
        with self._lock:
            if job_id is None:
                rows = self._db.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM llm_calls WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return [
            LlmCall(
                id=row["id"], job_id=row["job_id"], provider=row["provider"], model=row["model"], task=row["task"],
                tokens_in=row["tokens_in"], tokens_out=row["tokens_out"], latency_ms=row["latency_ms"],
                outcome=row["outcome"], at=row["at"], kind=row["kind"], prompt_version=row["prompt_version"],
                bundle_hash=row["bundle_hash"], style_hash=row["style_hash"], glossary_hash=row["glossary_hash"],
                batch=row["batch"], http_status=row["http_status"], finish_reason=row["finish_reason"],
                retries=row["retries"], repairs=row["repairs"], cost_est=row["cost_est"], series_id=row["series_id"],
            )
            for row in rows
        ]

    # -- events, quality signals, body index (P5, ADR-15) ----------------------------
    def add_event(
        self, kind: str, *, level: str = "INFO", job_id: str = "", unit_id: int | None = None, reason: str = "",
        data: dict[str, Any] | None = None, at: float | None = None,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events (at, level, kind, job_id, unit_id, reason, data_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at or time.time(), level, kind, job_id, unit_id, reason,
                 json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str)),
            )
            return int(cursor.lastrowid)

    def events(
        self, *, job_id: str | None = None, unit_id: int | None = None, kind: str | None = None,
        since: float | None = None, before_id: int | None = None, limit: int = 100,
    ) -> list[EventRecord]:
        clauses: list[str] = []
        args: list[Any] = []
        if job_id:
            clauses.append("job_id = ?")
            args.append(job_id)
        if unit_id is not None:
            clauses.append("unit_id = ?")
            args.append(unit_id)
        if kind:
            if kind.endswith("."):  # a family: "job." matches every job.* event
                clauses.append("kind LIKE ? ESCAPE '\\'")
                args.append(kind.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            else:
                clauses.append("kind = ?")
                args.append(kind)
        if since is not None:
            clauses.append("at >= ?")
            args.append(since)
        if before_id is not None:
            clauses.append("id < ?")
            args.append(before_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ?", (*args, max(1, min(int(limit), 1000)))
            ).fetchall()
        return [
            EventRecord(
                id=row["id"], at=row["at"], level=row["level"], kind=row["kind"], job_id=row["job_id"],
                unit_id=row["unit_id"], reason=row["reason"], data=json.loads(row["data_json"] or "{}"),
            )
            for row in rows
        ]

    def prune_events(self, before: float) -> int:
        with self._lock:
            return self._db.execute("DELETE FROM events WHERE at < ?", (before,)).rowcount

    def add_signal(
        self, kind: str, *, unit_id: int | None = None, version: int | None = None, cue: int | None = None,
        before: str = "", after: str = "", meta: dict[str, Any] | None = None, at: float | None = None,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO quality_signals (at, kind, unit_id, version, cue, before_text, after_text, meta_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (at or time.time(), kind, unit_id, version, cue, before, after,
                 json.dumps(meta or {}, ensure_ascii=False, sort_keys=True, default=str)),
            )
            return int(cursor.lastrowid)

    def signals(
        self, *, kind: str | None = None, unit_id: int | None = None, since: float | None = None,
        before_id: int | None = None, limit: int = 100,
    ) -> list[QualitySignal]:
        """The newest signals first; ``before_id`` pages backwards the way ``events`` does."""
        clauses: list[str] = []
        args: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        if unit_id is not None:
            clauses.append("unit_id = ?")
            args.append(unit_id)
        if since is not None:
            clauses.append("at >= ?")
            args.append(since)
        if before_id is not None:
            clauses.append("id < ?")
            args.append(before_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM quality_signals{where} ORDER BY id DESC LIMIT ?", (*args, max(1, min(int(limit), 5000)))
            ).fetchall()
        return [
            QualitySignal(
                id=row["id"], at=row["at"], kind=row["kind"], unit_id=row["unit_id"], version=row["version"],
                cue=row["cue"], before=row["before_text"], after=row["after_text"],
                meta=json.loads(row["meta_json"] or "{}"),
            )
            for row in rows
        ]

    def add_body_ref(self, call_id: int, file: str, offset: int, length: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_bodies (call_id, file, byte_offset, byte_length, rotated) VALUES (?, ?, ?, ?, 0)",
                (call_id, file, offset, length),
            )

    def body_ref(self, call_id: int) -> BodyRef | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM llm_bodies WHERE call_id = ?", (call_id,)).fetchone()
        if row is None:
            return None
        return BodyRef(
            call_id=row["call_id"], file=row["file"], offset=row["byte_offset"], length=row["byte_length"],
            rotated=bool(row["rotated"]),
        )

    def mark_bodies_rotated(self, files: Iterable[str]) -> int:
        names = list(files)
        if not names:
            return 0
        with self._lock:
            total = 0
            for name in names:
                total += self._db.execute("UPDATE llm_bodies SET rotated = 1 WHERE file = ? AND rotated = 0", (name,)).rowcount
            return total

    def token_totals(self, since: float = 0.0) -> TokenTotals:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens_in), 0) AS tokens_in,"
                " COALESCE(SUM(tokens_out), 0) AS tokens_out FROM llm_calls WHERE at >= ?",
                (since,),
            ).fetchone()
        return TokenTotals(calls=int(row["calls"]), tokens_in=int(row["tokens_in"]), tokens_out=int(row["tokens_out"]))
