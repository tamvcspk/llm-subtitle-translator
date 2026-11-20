"""The one-time move of an older queue into the ``jobs`` table (ADR-10, ADR-11, FR-15.12).

Until P10 Bazarr's post-processing script dropped one JSON file per missing target into ``<queue>/pending/`` and the
worker sorted them into folders (``processing``, ``deferred``, ``done``, ``failed``, ``held``, ``rejected``); P8 moved
all but ``pending/`` into the table and kept an admission gate on the script's files. P10 removes the script and the
gate, so what they left is settled here, once, at start-up:

* ``processing`` and ``deferred`` become jobs (``queued`` and ``waiting`` with the reason they had): the gate had
  already let them in;
* ``pending`` is what the script dropped and nobody has approved (D5, R10-5), so it is **not** run: those items are
  Missing in the Library again and only the admin's request or the ``auto_request`` policy makes a job for them. An
  explicit request of the admin that is still there (a retranslation, ``admission: admin``) is a job as before;
* ``done`` and ``failed`` become the history;
* ``held`` was "wanted, not decided": Bazarr lists it as missing, so it is Missing in the Library again and the file
  is dropped;
* ``rejected`` was a "no": it becomes an Ignored decision for that item and language. The decision is written
  **before** the row or the folder that held it goes (R10-2): a "no" that cannot be written yet (no library, a job
  that cannot be placed) stays where it is and is tried again at the next start-up, never lost.

Running it again changes nothing (SPEC criterion 13i): a job already in the table under the same id is left alone,
a decision is the same decision, and the old folders have gone to ``queue.migrated/`` (nothing is deleted), except a
folder that still holds a "no" it could not record.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from . import queue_policy as policy
from .jobs import DONE, FAILED, QUEUED, REPLACE, FILL, SKIPPED, WAITING, job_key
from .library import Library
from .queue import JobQueue

MIGRATED_DIR = "queue.migrated"
TREE_STATES = ("pending", "processing", "deferred", "done", "failed", "held", "rejected")
IGNORED = "ignored"

# What happened to a "no": written down, or too broken to place (nothing to write: dropped), or not writable yet.
SETTLED, DROPPED, UNSETTLED = "settled", "dropped", "unsettled"


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


def _wait_reason(job: dict[str, Any]) -> str:
    return {"provider-unavailable": "provider", "context-gap": "context"}.get(str(job.get("deferred_reason") or ""), "rate")


def _requested_by_admin(job: dict[str, Any]) -> bool:
    return job.get("trigger") == "retranslate" or job.get("admission") == "admin"


def import_queue_tree(queue: JobQueue, queue_dir: str, library: Library | None = None) -> dict[str, int]:
    """Settle an older queue (see the module text). Returns what became what."""
    root = Path(queue_dir) if queue_dir else None
    counts = {"imported": 0, "present": 0, "skipped": 0, "held": 0, "unrequested": 0, "ignored": 0, "unsettled": 0}
    keep: set[str] = set()  # folders that still hold a "no" nobody could write down
    if root is not None and any((root / state).is_dir() and any((root / state).glob("*.json")) for state in TREE_STATES):
        for state in TREE_STATES:
            for path in sorted((root / state).glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name)):
                job = _read(path)
                if job is None:
                    counts["skipped"] += 1
                    continue
                job_id = str(job.get("job_id") or path.stem)
                job["job_id"] = job_id
                if state == "held":
                    counts["held"] += 1
                elif state == "rejected":
                    if _settle(counts, library, job, path.name) == UNSETTLED:
                        keep.add(state)
                elif state == "pending" and not _requested_by_admin(job):
                    counts["unrequested"] += 1
                else:
                    _import_job(queue, job, path, state, counts)
    for row in queue.jobs.gate_rows():
        if row.rejected and _settle(counts, library, row.payload, f"row {row.row_id}") == UNSETTLED:
            continue  # the row stays: it is the only record of that "no"
        if not row.rejected:
            counts["held"] += 1
        queue.jobs.drop_gate_row(row.row_id)
    if root is not None:
        _retire(root, keep)
    if counts["unrequested"]:
        logging.warning(
            "%d job(s) the old Bazarr script had dropped and nobody had approved were not queued: they are Missing "
            "in the Library again; request them there if you want them translated", counts["unrequested"],
        )
    if counts["unsettled"]:
        logging.warning(
            "%d refusal(s) of the old queue could not be recorded as Ignored yet; they are kept and tried again at "
            "the next start-up", counts["unsettled"],
        )
    if any(counts[name] for name in ("imported", "held", "unrequested", "ignored", "unsettled")):
        queue._emit(
            "queue.imported", reason="the old queue was settled into the database: held and unapproved jobs are "
            "Missing again, rejected ones are Ignored", **counts,
        )
    return counts


def _import_job(queue: JobQueue, job: dict[str, Any], path: Path, state: str, counts: dict[str, int]) -> None:
    job_id = job["job_id"]
    if queue.jobs.get(job_id) is not None:
        counts["present"] += 1
        return
    modified = path.stat().st_mtime
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
    elif state in ("done", "failed"):
        target = DONE if state == "done" else FAILED
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
        return
    counts["imported"] += 1


def _settle(counts: dict[str, int], library: Library | None, job: dict[str, Any], where: str) -> str:
    """Write down a "no" and count it; the caller removes what held it only when this is not ``UNSETTLED``."""
    outcome = _ignore(library, job, where)
    if outcome == SETTLED:
        counts["ignored"] += 1
    elif outcome == UNSETTLED:
        counts["unsettled"] += 1
    return outcome


def _ignore(library: Library | None, job: dict[str, Any], where: str) -> str:
    """A rejected job becomes an Ignored decision for its unit and language."""
    target = str(job.get("target_code") or "")
    if not target:
        logging.warning("A refusal of the old queue (%s) names no language; it is dropped", where)
        return DROPPED
    if library is None:
        return UNSETTLED
    try:
        unit_id = library.register_job(job)
        library.catalog.set_decision(unit_id, target, IGNORED, time.time())
    except Exception:  # noqa: BLE001 - never a reason to stop the start-up; the "no" stays and is tried again
        logging.warning("A refusal of the old queue (%s) could not be recorded yet; it is kept", where, exc_info=True)
        return UNSETTLED
    return SETTLED


def _retire(root: Path, keep: set[str] = frozenset()) -> None:
    """Put the old folders (and the old pause file) under ``queue.migrated/``; empty ones are simply removed. A folder
    named in ``keep`` stays where it is."""
    destination = root / MIGRATED_DIR
    for name in (*TREE_STATES, "provider-pause.json"):
        source = root / name
        if name in keep or not source.exists():
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
