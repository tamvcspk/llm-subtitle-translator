"""How jobs from outside the database come in: Bazarr's script and the old queue folders (ADR-11, FR-12, FR-15.12).

``gst_enqueue.sh`` stays as it is: it drops one JSON file per missing target into ``<queue>/pending/`` and returns.
``receive_script_jobs`` is the gate that turns those files into ``jobs`` rows (and deletes the file), every tick,
until P10 removes the script. ``import_queue_tree`` is the one-time move of a queue that still lives in folders
(``processing``, ``deferred``, ``done``, ``failed``, ``held``, ``rejected``) into the table; it can run again
without making a job twice (SPEC criterion 13i), and leaves the old folders under ``queue.migrated/``.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from . import queue_policy as policy
from .config import ADMISSION_HOLD
from .jobs import DONE, FAILED, HELD, QUEUED, REJECTED, SKIPPED, WAITING, REPLACE, FILL, job_key
from .queue import ADMISSION_FIELD, JobQueue, max_open_jobs, outside_allowed_paths

MIGRATED_DIR = "queue.migrated"
TREE_STATES = ("processing", "deferred", "done", "failed", "held", "rejected")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return job if isinstance(job, dict) else None


def _key_of(job: dict[str, Any], name: str) -> str:
    """The sha1 of the file the job names, or its old id: how a job in a folder and the same job in a row meet."""
    subtitle, output, target = (str(job.get(f) or "") for f in ("subtitle_path", "output_path", "target_code"))
    if subtitle and output and target:
        return job_key(subtitle, output, target)
    return str(job.get("job_id") or name)


def receive_script_jobs(queue: JobQueue, queue_dir: str, admission_mode: str) -> dict[str, int]:
    """Take what the Bazarr script dropped in ``pending/`` into the table; returns how many were queued, held and dropped.

    In ``hold`` mode a job without an admission mark (the script's own) is held for the admin, unless it lies
    outside ``GST_ONLY_PATHS`` (then it ends as skipped, FR-12.7). A job whose id is held or rejected, or is
    already in line, is dropped (the "no" is remembered, FR-12.5). ``GST_MAX_PENDING`` is enforced here, since
    the script can no longer count the folders (FR-15.4 replaces it at P10).
    """
    counts = {"queued": 0, "held": 0, "dropped": 0}
    pending = Path(queue_dir) / "pending"
    if not pending.is_dir():
        return counts
    for path in sorted(pending.glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name)):
        job = _read(path)
        if job is None:
            continue  # half written or not JSON: leave it for the next tick
        key = _key_of(job, path.stem)
        job_id = str(job.get("job_id") or path.stem)
        job["job_id"] = job_id
        if queue.jobs.has_decided(key, HELD, REJECTED) or queue.jobs.open_for(key) is not None:
            path.unlink(missing_ok=True)
            counts["dropped"] += 1
            queue._emit("job.dropped", job_id=job_id, reason="the same job is already in line, held for approval or was rejected",
                                  subtitle=Path(str(job.get("subtitle_path") or "")).name)
            continue
        limit = max_open_jobs()
        if limit > 0 and queue.jobs.open_count() >= limit:
            path.unlink(missing_ok=True)
            counts["dropped"] += 1
            logging.warning("Not queueing %s: GST_MAX_PENDING=%s jobs are already waiting", job.get("subtitle_path"), limit)
            queue._emit("enqueue.skipped", level="WARNING", job_id=job_id,
                                  reason=f"GST_MAX_PENDING={limit} jobs are already waiting",
                                  subtitle=Path(str(job.get("subtitle_path") or "")).name)
            continue
        retrans = job.get("trigger") == "retranslate"
        held = (admission_mode == ADMISSION_HOLD and ADMISSION_FIELD not in job and not retrans
                and not outside_allowed_paths(str(job.get("subtitle_path", ""))))
        if held:
            job["held_at"] = time.time()
        prio = int(job.get("priority") or 0) or (policy.RETRANSLATE if retrans else policy.AUTO)
        added = queue.jobs.add(
            job, key=key, state=HELD if held else QUEUED, mode=REPLACE if retrans else FILL,
            origin="retranslate" if retrans else "script", prio=prio, job_id=job_id,
            created_at=float(job.get("created_at") or 0) or None,
        )
        path.unlink(missing_ok=True)
        if added.job_id is None:
            counts["dropped"] += 1
            continue
        counts["held" if held else "queued"] += 1
        if held:
            logging.info("Held job %s | %s -> %s | waiting for approval", job_id,
                         Path(str(job.get("subtitle_path", ""))).name, job.get("target_code", "?"))
            queue._emit("job.held", job_id=job_id, reason="admission_mode is hold: the job waits for the admin's approval",
                                  subtitle=Path(str(job.get("subtitle_path") or "")).name, target=job.get("target_code"))
        else:
            queue._emit("job.received", job_id=job_id, reason="Bazarr's script queued the job",
                                  subtitle=Path(str(job.get("subtitle_path") or "")).name, target=job.get("target_code"),
                                  origin="script")
    return counts


def _wait_reason(job: dict[str, Any]) -> str:
    return {"provider-unavailable": "provider", "context-gap": "context"}.get(str(job.get("deferred_reason") or ""), "rate")


def import_queue_tree(queue: JobQueue, queue_dir: str) -> dict[str, int]:
    """Move a queue that lives in folders into the ``jobs`` table, once.

    ``pending/`` is not touched: it is the script's gate and ``receive_script_jobs`` reads it. A job already in
    the table under the same id is left as it is, so running this again adds nothing (SPEC criterion 13i). The
    old folders go to ``queue.migrated/`` and nothing is deleted. A job that was running when the old worker
    stopped goes back in line; ``deferred`` becomes ``waiting`` with the reason it had.
    """
    root = Path(queue_dir)
    counts = {"imported": 0, "present": 0, "skipped": 0}
    if not any((root / state).is_dir() and any((root / state).glob("*.json")) for state in TREE_STATES):
        _retire(root)
        return counts
    for state in TREE_STATES:
        for path in sorted((root / state).glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name)):
            job = _read(path)
            if job is None:
                counts["skipped"] += 1
                continue
            job_id = str(job.get("job_id") or path.stem)
            if queue.jobs.get(job_id) is not None:
                counts["present"] += 1
                continue
            modified = path.stat().st_mtime
            job["job_id"] = job_id
            retrans = job.get("trigger") == "retranslate"
            common: dict[str, Any] = dict(
                key=_key_of(job, path.stem), mode=REPLACE if retrans else FILL,
                origin="retranslate" if retrans else ("manual" if job.get("admission") == "admin" else "script"),
                prio=int(job.get("priority") or 0) or (policy.RETRANSLATE if retrans else policy.AUTO), job_id=job_id,
                created_at=float(job.get("created_at") or modified),
            )
            target, wait, not_before = QUEUED, "", 0.0
            if state == "deferred":
                target, wait, not_before = WAITING, _wait_reason(job), float(job.get("retry_at") or 0)
            elif state in ("done", "failed", "held", "rejected"):
                target = {"done": DONE, "failed": FAILED, "held": HELD, "rejected": REJECTED}[state]
                if state == "done" and str(job.get("result") or "").startswith("skipped"):
                    target = SKIPPED
            ended: dict[str, Any] = {}
            if state in ("done", "failed"):
                error_file = path.with_suffix(".error")
                text = error_file.read_text(encoding="utf-8")[-2000:] if error_file.exists() else str(job.get("last_error") or "")
                ended = dict(finished_at=modified, result=str(job.get("result") or ""), error=text if state == "failed" else "")
            added = queue.jobs.add(job, state=target, not_before=not_before, wait_reason=wait, **ended, **common)
            if added.job_id is None:  # the same file is already in line under another id
                counts["present"] += 1
                continue
            counts["imported"] += 1
    _retire(root)
    queue._emit("queue.imported", reason="the queue folders were moved into the database", **counts)
    return counts


def _retire(root: Path) -> None:
    """Put the old folders (and the old pause file) under ``queue.migrated/``; empty ones are simply removed."""
    destination = root / MIGRATED_DIR
    for name in (*TREE_STATES, "provider-pause.json"):
        source = root / name
        if not source.exists():
            continue
        if source.is_dir() and not any(source.iterdir()):
            try:
                source.rmdir()
            except OSError:
                pass
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / name
        if target.exists():
            target = destination / f"{name}-{int(time.time())}"
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            logging.warning("Could not move %s to %s: %s", source, target, exc)
