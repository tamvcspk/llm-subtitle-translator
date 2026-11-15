"""The ``jobs`` and ``provider_health`` tables: jobs as rows, claimed atomically (FR-15, FR-17, ADR-11).

``JobStore`` knows rows and transitions; *which* job goes next, which model it may use and what a failure means
are decisions of ``queue_policy`` (pure) and ``queue.JobQueue`` (the lifecycle). Every method here is one short
SQLite transaction, because the worker thread and the HTTP threads both write.

A job travels as a plain dict (its *payload*: paths, languages, the retranslation range, ...), the shape the
translation code has always taken; the columns beside it are what the scheduler and the console query.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import queue_policy as policy
from .ports import Reclaimed
from .queue_policy import Candidate, Health
from .store import Store

QUEUED, RUNNING, WAITING = "queued", "running", "waiting"
DONE, FAILED, CANCELLED, SKIPPED = "done", "failed", "cancelled", "skipped"
OPEN_STATES = (QUEUED, RUNNING, WAITING)
ENDED_STATES = (DONE, FAILED, CANCELLED, SKIPPED)  # what cleanup may delete
STATES = (QUEUED, RUNNING, WAITING, DONE, FAILED, CANCELLED, SKIPPED)
# The two states of the script's admission gate (FR-12), gone since P10: the rows an older worker left are turned
# into decisions once, at start-up (``take_gate_rows``), and never written again.
GATE_HELD, GATE_REJECTED = "held", "rejected"

# What the console has always called the states (the queue was a folder tree), kept for the HTTP API and the
# pages built on it. `skipped` shows with `done`: both ended without anything left to do.
LEGACY_NAMES = {QUEUED: "pending", RUNNING: "processing", WAITING: "deferred", DONE: "done", SKIPPED: "done",
                FAILED: "failed"}
LEGACY_STATES = ("pending", "processing", "deferred", "done", "failed")
FROM_LEGACY = {"pending": (QUEUED,), "processing": (RUNNING,), "deferred": (WAITING,), "done": (DONE, SKIPPED),
                "failed": (FAILED,)}

PUBLISH, VARIANT = "publish", "variant"
FILL, REPLACE = "fill", "replace"

SKIPPED_RESULTS = ("skipped", "skipped-not-wanted", "skipped-existing-output")


def job_key(subtitle_path: str, output_path: str, target_code: str) -> str:
    """The sha1 that has always identified "this source subtitle into this target"."""
    return hashlib.sha1(f"{subtitle_path}|{output_path}|{target_code}".encode("utf-8")).hexdigest()


def retranslation_key(subtitle_path: str, output_path: str, target_code: str) -> str:
    return hashlib.sha1(f"retranslate|{subtitle_path}|{output_path}|{target_code}".encode("utf-8")).hexdigest()


def variant_key(subtitle_path: str, output_path: str, target_code: str) -> str:
    """The key of a variant job (FR-18.8). Distinct from the publish key, so a failed variant is never taken for a
    failed translation of the same file when a request looks for a job to revive."""
    return hashlib.sha1(f"variant|{subtitle_path}|{output_path}|{target_code}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Added:
    job_id: str | None  # the new job; None when an open job already holds the key
    existing: str | None = None  # the open job a request was merged into
    revived: bool = False  # a failed job of the same key was put back in line instead of a new one


def _loads(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def view(row: sqlite3.Row) -> dict[str, Any]:
    """A row as the job dict the rest of the worker reads: the payload, with the columns laid over it."""
    job = _loads(row["payload_json"])
    job.update(
        job_id=row["job_id"], state=row["state"], _state=LEGACY_NAMES.get(row["state"], row["state"]),
        mode=row["mode"], origin=row["origin"], prio=row["prio"], priority=row["prio"], wait_reason=row["wait_reason"],
        attempts=row["attempts"], not_before=row["not_before"], tier=row["tier"], created_at=row["created_at"],
        cancel_requested=bool(row["cancel_requested"]), result=row["result"] or job.get("result", ""),
    )
    if row["state"] == WAITING:
        job["retry_at"] = row["not_before"]
    if row["error"]:
        job["error"] = row["error"]
    elif job.get("last_error") and row["state"] in (WAITING, FAILED):
        job["error"] = str(job["last_error"])[-2000:]
    if row["state"] == SKIPPED:
        job["result"] = job.get("result") or "skipped"
    return job


class JobStore:
    """Repository of jobs and of the health of each (provider, model)."""

    def __init__(
        self, store: Store, *, clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.clock = clock
        self.monotonic = monotonic

    # -- reading ------------------------------------------------------------------------------------------------
    def get(self, job_id: str) -> dict[str, Any] | None:
        rows = self.store.query("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return view(rows[0]) if rows else None

    def state_of(self, job_id: str) -> str | None:
        rows = self.store.query("SELECT state FROM jobs WHERE job_id = ?", (job_id,))
        return rows[0]["state"] if rows else None

    def by_state(self, *states: str, limit: int | None = None, newest_first: bool = False) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(states))
        order = "updated_at DESC, id DESC" if newest_first else "created_at, id"
        sql = f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY {order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [view(row) for row in self.store.query(sql, states)]

    def counts(self) -> dict[str, int]:
        """Jobs per console state name (``pending``, ``processing``, ``deferred``, ``done``, ...)."""
        counts = {name: 0 for name in LEGACY_STATES}
        for row in self.store.query("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state"):
            name = LEGACY_NAMES.get(row["state"])
            if name:
                counts[name] += row["n"]
        return counts

    def open_count(self) -> int:
        marks = ",".join("?" * 3)
        return int(self.store.query(f"SELECT COUNT(*) AS n FROM jobs WHERE state IN ({marks})", (QUEUED, RUNNING, WAITING))[0]["n"])

    def all_payloads(self) -> Iterable[tuple[str, dict[str, Any]]]:
        for row in self.store.query("SELECT state, payload_json FROM jobs"):
            yield row["state"], _loads(row["payload_json"])

    def open_for(self, key: str, kind: str = PUBLISH) -> dict[str, Any] | None:
        rows = self.store.query(
            "SELECT * FROM jobs WHERE key = ? AND kind = ? AND state IN (?, ?, ?)", (key, kind, *OPEN_STATES)
        )
        return view(rows[0]) if rows else None

    def open_for_unit(self, unit_id: int, target_lang: str, kind: str = PUBLISH) -> dict[str, Any] | None:
        """The open job for this unit and language, whatever paths it was made with: a file Sonarr renamed gives the
        same request a new key, but it is still the same request (FR-15.3, FR-14.3)."""
        rows = self.store.query(
            "SELECT * FROM jobs WHERE unit_id = ? AND target_lang = ? AND kind = ? AND state IN (?, ?, ?)"
            " ORDER BY id DESC LIMIT 1", (unit_id, target_lang, kind, *OPEN_STATES),
        )
        return view(rows[0]) if rows else None

    def latest_for(self, key: str, *states: str) -> dict[str, Any] | None:
        marks = ",".join("?" * len(states))
        rows = self.store.query(
            f"SELECT * FROM jobs WHERE key = ? AND state IN ({marks}) ORDER BY id DESC LIMIT 1", (key, *states)
        )
        return view(rows[0]) if rows else None

    def last_served(self) -> dict[str, float]:
        rows = self.store.query(
            "SELECT series_key, MAX(started_at) AS at FROM jobs WHERE series_key <> '' AND started_at IS NOT NULL"
            " GROUP BY series_key"
        )
        return {row["series_key"]: float(row["at"]) for row in rows}

    # -- creating -----------------------------------------------------------------------------------------------
    def add(
        self, payload: dict[str, Any], *, key: str, state: str = QUEUED, mode: str = FILL, origin: str = "manual",
        prio: int = policy.AUTO, kind: str = PUBLISH, job_id: str | None = None, not_before: float = 0.0,
        created_at: float | None = None, wait_reason: str = "", error: str = "", result: str = "",
        finished_at: float | None = None,
    ) -> Added:
        """Insert a job. When an open job already holds ``(key, kind)`` nothing is inserted: the request is merged
        into it (a higher class lifts it, FR-15.3) and its id comes back as ``existing``."""
        now = self.clock()
        payload = {k: v for k, v in payload.items() if k not in ("_state", "state")}
        with self.store.transaction() as db:
            open_job = db.execute(
                "SELECT job_id, state, prio FROM jobs WHERE key = ? AND kind = ? AND state IN (?, ?, ?)",
                (key, kind, *OPEN_STATES),
            ).fetchone()
            if open_job is not None:
                if open_job["state"] in (QUEUED, WAITING) and prio > open_job["prio"]:
                    db.execute("UPDATE jobs SET prio = ?, updated_at = ? WHERE job_id = ?", (prio, now, open_job["job_id"]))
                return Added(None, open_job["job_id"])
            chosen = job_id or key
            if db.execute("SELECT 1 FROM jobs WHERE job_id = ?", (chosen,)).fetchone():
                chosen = f"{key}-{time.time_ns()}"
            payload["job_id"] = chosen
            db.execute(
                "INSERT INTO jobs (job_id, key, kind, mode, origin, prio, state, wait_reason, series_key, unit_id,"
                " target_lang, not_before, payload_json, error, result, created_at, updated_at, finished_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chosen, key, kind, mode, origin, prio, state, wait_reason, str(payload.get("series_id") or ""),
                 payload.get("unit_id") or None, str(payload.get("target_code") or ""), not_before,
                 json.dumps(payload, ensure_ascii=False), error, result, created_at or now, finished_at or now,
                 finished_at),
            )
        return Added(chosen)

    def merge_retranslation(self, job_id: str, patch: dict[str, Any], prio: int) -> bool:
        """An admin asks to retranslate what a ``fill`` job is about to translate: the open job becomes the
        replacement (FR-15.3). Only a job that has not started can change; returns False when it has."""
        now = self.clock()
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None or row["state"] not in (QUEUED, WAITING):
                return False
            payload = _loads(row["payload_json"])
            payload.update(patch)
            db.execute(
                "UPDATE jobs SET payload_json = ?, mode = ?, prio = MAX(prio, ?), origin = 'retranslate',"
                " updated_at = ? WHERE job_id = ?",
                (json.dumps(payload, ensure_ascii=False), REPLACE, prio, now, job_id),
            )
        return True

    # -- the scheduler's one atomic step --------------------------------------------------------------------------
    def health_map(self) -> dict[tuple[str, str], Health]:
        return {
            (row["provider"], row["model"]): Health(row["state"], row["failures"], row["level"], row["until"], row["reason"])
            for row in self.store.query("SELECT * FROM provider_health")
        }

    def set_health(self, provider: str, model: str, health: Health, db: sqlite3.Connection | None = None) -> None:
        sql = (
            "INSERT INTO provider_health (provider, model, state, failures, level, until, reason, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider, model) DO UPDATE SET state = excluded.state,"
            " failures = excluded.failures, level = excluded.level, until = excluded.until, reason = excluded.reason,"
            " updated_at = excluded.updated_at"
        )
        args = (provider, model, health.state, health.failures, health.level, health.until, health.reason, self.clock())
        if db is not None:
            db.execute(sql, args)
        else:
            with self.store.transaction() as conn:
                conn.execute(sql, args)

    def claim(
        self,
        decide: Callable[[Candidate, dict[str, Any], dict[tuple[str, str], Health]], tuple[dict[str, Any], dict[tuple[str, str], Health]] | None],
        *,
        lease_seconds: float = policy.LEASE_SECONDS,
        aging_seconds: float = policy.AGING_SECONDS,
    ) -> dict[str, Any] | None:
        """Take the best job that ``decide`` accepts and mark it running, in one ``BEGIN IMMEDIATE`` transaction,
        so two claimers can never get the same job (SPEC criterion 14b).

        ``decide(candidate, payload, health)`` returns the fields to stamp on the job's payload and the new
        health rows (a half-open breaker's one trial call), or None to leave the job where it is.
        """
        now = self.clock()
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT id, prio, created_at, series_key, tier, payload_json FROM jobs"
                " WHERE state = ? AND not_before <= ?", (QUEUED, now),
            ).fetchall()
            if not rows:
                return None
            by_id = {row["id"]: row for row in rows}
            served = {
                row["series_key"]: float(row["at"]) for row in db.execute(
                    "SELECT series_key, MAX(started_at) AS at FROM jobs WHERE series_key <> '' AND started_at IS NOT NULL"
                    " GROUP BY series_key")
            }
            health = {
                (row["provider"], row["model"]): Health(row["state"], row["failures"], row["level"], row["until"], row["reason"])
                for row in db.execute("SELECT * FROM provider_health")
            }
            candidates = [Candidate(r["id"], r["prio"], r["created_at"], r["series_key"], r["tier"]) for r in rows]
            for candidate in policy.order_candidates(candidates, served, now, aging_seconds):
                payload = _loads(by_id[candidate.id]["payload_json"])
                decision = decide(candidate, payload, health)
                if decision is None:
                    continue
                stamp, new_health = decision
                payload.update(stamp)
                db.execute(
                    "UPDATE jobs SET state = ?, wait_reason = '', started_at = ?, updated_at = ?, lease_until = ?,"
                    " cancel_requested = 0, payload_json = ? WHERE id = ? AND state = ?",
                    (RUNNING, now, now, self.monotonic() + lease_seconds,
                     json.dumps(payload, ensure_ascii=False), candidate.id, QUEUED),
                )
                for (provider, model), changed in new_health.items():
                    self.set_health(provider, model, changed, db)
                return view(db.execute("SELECT * FROM jobs WHERE id = ?", (candidate.id,)).fetchone())
        return None

    # -- the life of a running job ----------------------------------------------------------------------------------
    def save_payload(self, job_id: str, payload: dict[str, Any], *, lease_seconds: float | None = None) -> bool:
        """Write the job dict back (progress, stage); doubles as the heartbeat. False when the job was taken
        away (cancelled, recovered) so the runner can stop."""
        now = self.clock()
        clean = {k: v for k, v in payload.items() if k not in ("_state", "state")}
        lease = None if lease_seconds is None else self.monotonic() + lease_seconds
        with self.store.transaction() as db:
            cursor = db.execute(
                "UPDATE jobs SET payload_json = ?, updated_at = ?, lease_until = COALESCE(?, lease_until)"
                " WHERE job_id = ? AND state = ?",
                (json.dumps(clean, ensure_ascii=False), now, lease, job_id, RUNNING),
            )
            return cursor.rowcount > 0

    def heartbeat(self, job_id: str, lease_seconds: float) -> None:
        with self.store.transaction() as db:
            db.execute("UPDATE jobs SET lease_until = ? WHERE job_id = ? AND state = ?",
                       (self.monotonic() + lease_seconds, job_id, RUNNING))

    def bundle(self, job_id: str):
        """The bundle a job's latest attempt used (P11), or None when no attempt has gathered one yet."""
        return self.store.job_bundle(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        rows = self.store.query("SELECT cancel_requested FROM jobs WHERE job_id = ?", (job_id,))
        return bool(rows and rows[0]["cancel_requested"])

    def transition(
        self, job_id: str, state: str, *, payload: dict[str, Any] | None = None, wait_reason: str = "",
        not_before: float = 0.0, tier: str | None = None, result: str = "", error: str = "", snapshot: dict | None = None,
        attempts: int | None = None, only_from: tuple[str, ...] | None = None,
    ) -> bool:
        """Move a job to ``state`` with the fields that go with it. ``only_from`` guards against moving a job
        someone else already moved (the admin cancelled it while it ran)."""
        now = self.clock()
        sets = ["state = ?", "wait_reason = ?", "not_before = ?", "result = ?", "error = ?", "updated_at = ?",
                "lease_until = NULL", "cancel_requested = CASE WHEN ? IN ('running') THEN cancel_requested ELSE 0 END"]
        args: list[Any] = [state, wait_reason, not_before, result, error, now, state]
        if state in (DONE, FAILED, CANCELLED, SKIPPED):
            sets.append("finished_at = ?")
            args.append(now)
        if payload is not None:
            sets.append("payload_json = ?")
            args.append(json.dumps({k: v for k, v in payload.items() if k not in ("_state", "state")}, ensure_ascii=False))
        if tier is not None:
            sets.append("tier = ?")
            args.append(tier)
        if snapshot is not None:
            sets.append("snapshot_json = ?")
            args.append(json.dumps(snapshot, ensure_ascii=False, default=str))
        if attempts is not None:
            sets.append("attempts = ?")
            args.append(attempts)
        where = "job_id = ?"
        args.append(job_id)
        if only_from:
            where += f" AND state IN ({','.join('?' * len(only_from))})"
            args.extend(only_from)
        with self.store.transaction() as db:
            return db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE {where}", args).rowcount > 0

    # -- recovery and waking ------------------------------------------------------------------------------------------
    def recover(self, max_crashes: int = policy.MAX_CRASHES, *, expired_only: bool = False) -> list[tuple[dict[str, Any], str]]:
        """Running jobs the worker no longer holds go back in line with ``attempts + 1``; one that has died
        ``max_crashes`` times is failed (FR-15.9). At startup every running row is orphaned; later only those
        whose lease ran out. Returns ``(job, "queued" | "failed")`` for the events."""
        now = self.clock()
        out: list[tuple[dict[str, Any], str]] = []
        with self.store.transaction() as db:
            if expired_only:
                rows = db.execute("SELECT * FROM jobs WHERE state = ? AND lease_until IS NOT NULL AND lease_until < ?",
                                  (RUNNING, self.monotonic())).fetchall()
            else:
                rows = db.execute("SELECT * FROM jobs WHERE state = ?", (RUNNING,)).fetchall()
            for row in rows:
                attempts = row["attempts"] + 1
                payload = _loads(row["payload_json"])
                payload["stage"] = "Recovered after service restart"
                payload["updated_at"] = now
                if attempts >= max_crashes:
                    db.execute(
                        "UPDATE jobs SET state = ?, attempts = ?, error = ?, payload_json = ?, lease_until = NULL,"
                        " updated_at = ?, finished_at = ? WHERE id = ?",
                        (FAILED, attempts, f"crashed: the worker stopped while the job ran, {attempts} times",
                         json.dumps(payload, ensure_ascii=False), now, now, row["id"]),
                    )
                    state = FAILED
                else:
                    db.execute(
                        "UPDATE jobs SET state = ?, attempts = ?, payload_json = ?, lease_until = NULL, updated_at = ?"
                        " WHERE id = ?", (QUEUED, attempts, json.dumps(payload, ensure_ascii=False), now, row["id"]),
                    )
                    state = QUEUED
                out.append((view(db.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()), state))
        return out

    # -- what the console does -----------------------------------------------------------------------------------------
    def retry(self, job_id: str) -> dict[str, Any] | None:
        """A failed job goes back in line with a fresh retry cycle, as an admin's request."""
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ? AND state = ?", (job_id, FAILED)).fetchone()
            if row is None:
                return None
            if db.execute("SELECT 1 FROM jobs WHERE key = ? AND kind = ? AND state IN (?, ?, ?)",
                          (row["key"], row["kind"], *OPEN_STATES)).fetchone():
                return None
            payload = _loads(row["payload_json"])
            for field in ("provider_retry_count", "retry_at", "deferred_reason", "last_error", "use_fallback_model",
                          "gap_since", "escalated_from", "escalation_reason", "stage", "progress_done", "progress_total"):
                payload.pop(field, None)
            db.execute(
                "UPDATE jobs SET state = ?, wait_reason = '', error = '', result = '', not_before = 0, tier = '',"
                " attempts = 0, prio = MAX(prio, ?), payload_json = ?, updated_at = ?, finished_at = NULL WHERE id = ?",
                (QUEUED, policy.INTERACTIVE, json.dumps(payload, ensure_ascii=False), self.clock(), row["id"]),
            )
            return view(db.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone())

    def delete(self, states: tuple[str, ...], job_id: str) -> bool:
        with self.store.transaction() as db:
            deleted = db.execute(
                f"DELETE FROM jobs WHERE job_id = ? AND state IN ({','.join('?' * len(states))})", (job_id, *states)
            ).rowcount > 0
            self._sweep_bundles(db)
            return deleted

    @staticmethod
    def _sweep_bundles(db) -> None:
        """A bundle lives and dies with its job: whatever job left, its bundle goes (P11, FR-13)."""
        db.execute("DELETE FROM job_bundles WHERE job_id NOT IN (SELECT job_id FROM jobs)")

    def request_stop(self, job_id: str) -> str | None:
        """Stop a job: a waiting one at once, a running one at the next batch boundary (FR-15.11). Returns what
        happened: ``cancelled``, ``stopping`` or None when there was nothing to stop."""
        now = self.clock()
        with self.store.transaction() as db:
            row = db.execute("SELECT state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["state"] in (QUEUED, WAITING):
                db.execute("UPDATE jobs SET state = ?, wait_reason = '', updated_at = ?, finished_at = ? WHERE job_id = ?",
                           (CANCELLED, now, now, job_id))
                return "cancelled"
            if row["state"] == RUNNING:
                db.execute("UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE job_id = ?", (now, job_id))
                return "stopping"
        return None

    def take_gate_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The rows the script's admission gate left (FR-12), taken out of the table: ``(held, rejected)`` payloads.

        P10 removes the gate. A held job was "wanted, not decided yet", which is what Missing says; a rejected one
        was a "no", which is an Ignored decision. The caller turns them into that; running this again finds nothing.
        """
        with self.store.transaction() as db:
            rows = db.execute("SELECT id, state, payload_json FROM jobs WHERE state IN (?, ?) ORDER BY id",
                              (GATE_HELD, GATE_REJECTED)).fetchall()
            for row in rows:
                db.execute("DELETE FROM jobs WHERE id = ?", (row["id"],))
            self._sweep_bundles(db)
        held = [_loads(r["payload_json"]) for r in rows if r["state"] == GATE_HELD]
        rejected = [_loads(r["payload_json"]) for r in rows if r["state"] == GATE_REJECTED]
        return held, rejected

    def expired(self, states: tuple[str, ...], finished_before: float) -> Reclaimed:
        """Finished jobs in ``states`` that ended before ``finished_before``: how many rows and how much text they
        hold (payload, snapshot, error). Only ended states can be asked for, so no job that is still open (queued,
        running, waiting, held) and no refusal (``rejected``) can ever be counted or deleted (FR-13.2)."""
        marks = self._ended(states)
        row = self.store.query(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(payload_json) + LENGTH(snapshot_json) + LENGTH(error)"
            " + COALESCE((SELECT LENGTH(bundle_json) FROM job_bundles b WHERE b.job_id = jobs.job_id), 0)), 0) AS size"
            f" FROM jobs WHERE state IN ({marks}) AND COALESCE(finished_at, updated_at) < ?",
            (*states, finished_before),
        )[0]
        return Reclaimed(rows=int(row["n"]), bytes=int(row["size"]))

    def delete_expired(self, states: tuple[str, ...], finished_before: float) -> int:
        marks = self._ended(states)
        with self.store.transaction() as db:
            deleted = db.execute(
                f"DELETE FROM jobs WHERE state IN ({marks}) AND COALESCE(finished_at, updated_at) < ?",
                (*states, finished_before),
            ).rowcount
            self._sweep_bundles(db)
            return deleted

    @staticmethod
    def _ended(states: tuple[str, ...]) -> str:
        if not states or not set(states) <= set(ENDED_STATES):
            raise ValueError(f"only finished jobs can be cleaned up, not {sorted(set(states) - set(ENDED_STATES))}")
        return ",".join("?" * len(states))

    def lift(self, job_id: str, prio: int) -> bool:
        """A second request for what a job already does lifts it to the higher class, never lowers it (FR-15.3)."""
        with self.store.transaction() as db:
            return db.execute("UPDATE jobs SET prio = MAX(prio, ?), updated_at = ? WHERE job_id = ? AND state IN (?, ?)",
                              (prio, self.clock(), job_id, QUEUED, WAITING)).rowcount > 0

    def set_prio(self, job_id: str, prio: int) -> bool:
        with self.store.transaction() as db:
            return db.execute("UPDATE jobs SET prio = ? WHERE job_id = ? AND state IN (?, ?)",
                              (prio, job_id, QUEUED, WAITING)).rowcount > 0
