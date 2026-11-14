from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import ADMISSION_AUTO, ADMISSION_HOLD, enabled_target_languages
from .ports import HeldJob
from .subtitles import existing_target_subtitle, target_output_path
from .translation import ContextGapError, DailyQuotaExceededError, HardJobError, ProviderUnavailableError
from .queue_policy import PROVIDER_RETRY_DELAYS, daily_quota_retry_at, provider_retry_decision, select_model


# `held` and `rejected` are the admission states of FR-12: a held job is received and stored but never claimed
# or run (no model call, no quota); a rejected job is the remembered "no" that keeps the Bazarr script and scans
# from queueing the same job again. Neither is picked up by `_ready_job_path`, quota failing or recovery.
HELD = "held"
REJECTED = "rejected"
QUEUE_STATES = ("pending", "processing", "deferred", "done", "failed", HELD, REJECTED)

# A job that already went through admission carries an `admission` field: "admin" (made by the console),
# "auto" (running or open when `hold` was switched on), "approved" (released from held). The script's own
# jobs have none, which is how the worker tells "new from Bazarr" from "already admitted" (FR-12.2, 12.3).
ADMISSION_FIELD = "admission"


def _write_job(path: Path, job: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.stem}.tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _read_job(path: Path) -> dict[str, Any] | None:
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return job if isinstance(job, dict) else None


def _safe_job_id(job_id: str) -> bool:
    """A job id names a file in the queue directory; anything that is not a plain file name is refused."""
    return bool(job_id) and job_id == Path(job_id).name and job_id not in {".", ".."}


def _move_job(source: Path, destination: Path, error: str | None = None) -> None:
    source.replace(destination)
    source.with_suffix(".error").unlink(missing_ok=True)
    error_path = destination.with_suffix(".error")
    if error is None:
        error_path.unlink(missing_ok=True)
    else:
        error_path.write_text(error, encoding="utf-8")


def _reset_retry(job: dict[str, Any]) -> None:
    for key in ("provider_retry_count", "retry_at", "deferred_reason", "last_error", "use_fallback_model", "gap_since"):
        job.pop(key, None)


def _read_provider_pause(queue_dir: str) -> dict[str, Any]:
    try:
        pause = json.loads((Path(queue_dir) / "provider-pause.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return pause if isinstance(pause, dict) else {}


def daily_quota_pause_until(queue_dir: str, now: float | None = None) -> float | None:
    """The global pause: every configured model has exhausted its daily quota."""
    pause = _read_provider_pause(queue_dir)
    try:
        retry_at = float(pause.get("retry_at") or 0)
    except (TypeError, ValueError):
        return None
    if pause.get("reason") == "daily-quota" and retry_at > (time.time() if now is None else now):
        return retry_at
    return None


def model_quota_pauses(queue_dir: str, now: float | None = None) -> dict[str, float]:
    """Models whose own daily quota is exhausted, mapped to when each may be used again."""
    current_time = time.time() if now is None else now
    models = _read_provider_pause(queue_dir).get("models")
    paused: dict[str, float] = {}
    if isinstance(models, dict):
        for model, retry_at in models.items():
            try:
                if float(retry_at) > current_time:
                    paused[str(model)] = float(retry_at)
            except (TypeError, ValueError):
                continue
    return paused


def sync_daily_quota_pause(queue_dir: str, models: tuple[str, ...], now: float | None = None) -> float | None:
    """Re-derive the global pause from per-model pauses for the models configured now.

    Switching to a model that still has quota lifts the pause; the pause file is
    rewritten so the Bazarr enqueue script sees the same admission decision.
    """
    current_time = time.time() if now is None else now
    pause = _read_provider_pause(queue_dir)
    if not models or not isinstance(pause.get("models"), dict):
        return daily_quota_pause_until(queue_dir, current_time)
    paused = model_quota_pauses(queue_dir, current_time)
    if all(model in paused for model in models):
        updated = {**pause, "retry_at": min(paused[model] for model in models), "reason": "daily-quota", "models": paused}
    else:
        updated = {"models": paused}
    if updated != pause:
        _write_job(Path(queue_dir) / "provider-pause.json", updated)
    return daily_quota_pause_until(queue_dir, current_time)


def reject_daily_quota(queue_dir: str) -> None:
    if daily_quota_pause_until(queue_dir) is not None:
        raise ValueError("Daily Gemini quota exhausted; new jobs and retries are disabled until the quota pause expires.")


def allowed_path_prefixes() -> tuple[str, ...]:
    """Folders automatic jobs may come from (`GST_ONLY_PATHS`, comma separated); empty means anywhere.

    A guard for trying the worker against a real Bazarr: only the named folders are ever translated,
    however many items Bazarr reports as wanted.
    """
    raw = os.getenv("GST_ONLY_PATHS", "")
    return tuple(p.strip().replace("\\", "/").rstrip("/") for p in re.split(r"[,\n]", raw) if p.strip())


def outside_allowed_paths(subtitle_path: str) -> bool:
    prefixes = allowed_path_prefixes()
    if not prefixes:
        return False
    normalized = str(subtitle_path).replace("\\", "/")
    return not any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in prefixes)


def max_open_jobs() -> int:
    """Most jobs allowed to wait or run at once (`GST_MAX_PENDING`); 0 means no cap."""
    try:
        return max(0, int(os.getenv("GST_MAX_PENDING", "0") or 0))
    except ValueError:
        return 0


def open_job_count(queue_dir: str) -> int:
    root = Path(queue_dir)
    return sum(len(list((root / state).glob("*.json"))) for state in ("pending", "processing", "deferred"))


def queue_is_full(queue_dir: str) -> bool:
    limit = max_open_jobs()
    return limit > 0 and open_job_count(queue_dir) >= limit


def skip_reason(job: dict[str, Any]) -> str | None:
    """Why a job must not translate anything, or None when it should run."""
    provider = str(job.get("provider", "")).lower()
    language = str(job.get("source_code") or job.get("language") or "").split(":", 1)[0].lower()
    subtitle_path = str(job.get("subtitle_path", ""))
    target_code = str(job.get("target_code") or "zh")
    output_path = str(job.get("output_path") or target_output_path(subtitle_path, target_code, source_code=language))

    if provider and provider not in {"embeddedsubtitles", "gemini-console", "manual", "local"}:
        return f"provider {provider} is not handled"
    if language and language == target_code.lower():
        return "source and target language are the same"
    if not subtitle_path or not Path(subtitle_path).exists():
        return "source subtitle is gone"
    # An explicit retranslation exists to replace the target (the version history keeps the old one).
    if job.get("trigger") == "retranslate":
        return None
    if outside_allowed_paths(subtitle_path):
        return "outside GST_ONLY_PATHS"
    if Path(output_path).exists():
        return "target subtitle already exists"
    found = existing_target_subtitle(subtitle_path, target_code, language)
    if found:
        return f"{target_code} subtitle already present: {Path(found).name}"
    return None


def should_skip_job(job: dict[str, Any]) -> bool:
    return skip_reason(job) is not None


def ensure_queue_dirs(queue_dir: str) -> None:
    root = Path(queue_dir)
    for state in QUEUE_STATES:
        (root / state).mkdir(parents=True, exist_ok=True)


def job_id_for(subtitle_path: str, output_path: str, target_code: str) -> str:
    raw = f"{subtitle_path}|{output_path}|{target_code}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def retry_failed_job(queue_dir: str, job_id: str) -> bool:
    reject_daily_quota(queue_dir)
    ensure_queue_dirs(queue_dir)
    failed = Path(queue_dir) / "failed" / f"{job_id}.json"
    pending = Path(queue_dir) / "pending" / f"{job_id}.json"
    if not failed.exists() or pending.exists():
        return False
    try:
        job = json.loads(failed.read_text(encoding="utf-8"))
        _reset_retry(job)
        job.pop("escalated_from", None)
        job.pop("escalation_reason", None)
        job[ADMISSION_FIELD] = "admin"  # the admin asked for this run, so `hold` does not catch it
        _write_job(failed, job)
    except (OSError, TypeError, ValueError):
        pass
    _move_job(failed, pending)
    return True


def cancel_failed_job(queue_dir: str, job_id: str) -> bool:
    """Permanently remove a failed job without scheduling another attempt."""
    return delete_queue_job(queue_dir, "failed", job_id)


def delete_queue_job(queue_dir: str, state: str, job_id: str) -> bool:
    # A held job leaves through approve or reject (so the "no" is remembered), never by plain deletion.
    if state not in QUEUE_STATES or state == HELD or not _safe_job_id(job_id):
        return False
    path = Path(queue_dir) / state / f"{job_id}.json"
    if not path.exists():
        return False
    path.unlink()
    error = path.with_suffix(".error")
    if error.exists():
        error.unlink()
    return True


def enqueue_translation_jobs(
    queue_dir: str,
    base_job: dict[str, Any],
    targets: list[dict[str, Any]],
) -> list[str]:
    reject_daily_quota(queue_dir)
    ensure_queue_dirs(queue_dir)
    created: list[str] = []
    subtitle_path = str(base_job.get("subtitle_path", ""))
    if not subtitle_path or not Path(subtitle_path).exists():
        return created
    source_code = str(base_job.get("source_code") or base_job.get("language") or "en").split(":", 1)[0].strip()
    source_language = str(base_job.get("source_language") or source_code or "English")
    if outside_allowed_paths(subtitle_path):
        logging.warning("Not queueing %s: outside GST_ONLY_PATHS", subtitle_path)
        return created

    for target in enabled_target_languages({"target_languages": targets}):
        target_code = str(target["code"])
        if source_code.lower() == target_code.lower():
            continue
        output_path = str(base_job.get("output_path") or target_output_path(subtitle_path, target_code, source_code=source_code))
        # Only a language that is missing gets translated: any sibling subtitle already in it counts.
        if Path(output_path).exists() or existing_target_subtitle(subtitle_path, target_code, source_code):
            continue
        jid = job_id_for(subtitle_path, output_path, target_code)
        pending = Path(queue_dir) / "pending" / f"{jid}.json"
        processing = Path(queue_dir) / "processing" / f"{jid}.json"
        deferred = Path(queue_dir) / "deferred" / f"{jid}.json"
        if pending.exists() or processing.exists() or deferred.exists():
            continue
        # Waiting for approval, or refused: not queued again (FR-12.5), even by a scan.
        if (Path(queue_dir) / HELD / pending.name).exists() or (Path(queue_dir) / REJECTED / pending.name).exists():
            continue
        if queue_is_full(queue_dir):
            logging.warning("Not queueing %s (%s): GST_MAX_PENDING=%s jobs are already waiting",
                            subtitle_path, target_code, max_open_jobs())
            break
        if retry_failed_job(queue_dir, jid):
            created.append(str(pending))
            continue

        job = {
            **base_job,
            "job_id": jid,
            "created_at": int(time.time()),
            "source_code": source_code,
            "source_language": source_language,
            "target_code": target_code,
            "target_language": str(target["language"]),
            "output_path": output_path,
            ADMISSION_FIELD: "admin",
        }
        _write_job(pending, job)
        created.append(str(pending))
    return created


RETRANSLATE_PRIORITY = 100  # automatic jobs have none (0); FR-11.3.4


def enqueue_retranslation(queue_dir: str, job: dict[str, Any]) -> str:
    """Queue one explicit retranslation ahead of automatic work; one per target at a time."""
    reject_daily_quota(queue_dir)
    ensure_queue_dirs(queue_dir)
    subtitle_path, output_path = str(job.get("subtitle_path", "")), str(job.get("output_path", ""))
    target_code = str(job.get("target_code", ""))
    root = Path(queue_dir)
    prefix = hashlib.sha1(f"retranslate|{subtitle_path}|{output_path}|{target_code}".encode("utf-8")).hexdigest()
    for state in ("pending", "processing", "deferred"):
        if any((root / state).glob(f"{prefix}-*.json")):
            raise ValueError("A retranslation of this subtitle is already queued")
    jid = f"{prefix}-{time.time_ns()}"
    pending = root / "pending" / f"{jid}.json"
    _write_job(pending, {
        **job, "job_id": jid, "trigger": "retranslate", "priority": RETRANSLATE_PRIORITY,
        "created_at": int(time.time()), ADMISSION_FIELD: "admin",
    })
    return jid


def _job_files(queue_dir: str, state: str) -> list[Path]:
    return sorted((Path(queue_dir) / state).glob("*.json"), key=lambda path: (path.stat().st_mtime, path.name))


def list_jobs(queue_dir: str, state: str) -> list[dict[str, Any]]:
    """Every job in `state`, oldest first, without the limit `queue_snapshot` applies (the Held inbox can be long)."""
    ensure_queue_dirs(queue_dir)
    jobs: list[dict[str, Any]] = []
    for path in _job_files(queue_dir, state):
        job = _read_job(path)
        if job is not None:
            jobs.append({**job, "job_id": str(job.get("job_id") or path.stem)})
    return jobs


def approve_jobs(queue_dir: str, job_ids: list[str]) -> list[str]:
    """Release held jobs into the normal queue (FR-12.5); returns the ids that moved to pending.

    Approval is only an admission: the job still meets `skip_reason` (target already present, FR-12.6)
    and the language check when it runs, and `GST_MAX_PENDING` does not apply to it (FR-12.7).
    """
    reject_daily_quota(queue_dir)
    ensure_queue_dirs(queue_dir)
    root = Path(queue_dir)
    approved: list[str] = []
    for job_id in dict.fromkeys(job_ids):
        if not _safe_job_id(job_id):
            continue
        source = root / HELD / f"{job_id}.json"
        job = _read_job(source) if source.exists() else None
        if job is None:
            continue
        if any((root / state / source.name).exists() for state in ("pending", "processing", "deferred")):
            source.unlink(missing_ok=True)  # already open under that id: the held copy is a leftover
            continue
        job[ADMISSION_FIELD] = "approved"
        job["approved_at"] = time.time()
        _write_job(source, job)
        _move_job(source, root / "pending" / source.name)
        approved.append(job_id)
    return approved


def _move_between(queue_dir: str, job_ids: list[str], source_state: str, destination_state: str, **stamp: Any) -> list[str]:
    ensure_queue_dirs(queue_dir)
    root = Path(queue_dir)
    moved: list[str] = []
    for job_id in dict.fromkeys(job_ids):
        if not _safe_job_id(job_id):
            continue
        source = root / source_state / f"{job_id}.json"
        job = _read_job(source) if source.exists() else None
        if job is None:
            continue
        destination = root / destination_state / source.name
        if destination.exists():
            source.unlink(missing_ok=True)
            continue
        _write_job(source, {**job, **stamp, "decided_at": time.time()})
        _move_job(source, destination)
        moved.append(job_id)
    return moved


def reject_jobs(queue_dir: str, job_ids: list[str]) -> list[str]:
    """Refuse held jobs: they never run, and the script or a scan cannot bring them back (FR-12.5)."""
    return _move_between(queue_dir, job_ids, HELD, REJECTED)


def restore_jobs(queue_dir: str, job_ids: list[str]) -> list[str]:
    """Take rejected jobs back to the inbox (held), where they can be approved."""
    return _move_between(queue_dir, job_ids, REJECTED, HELD)


def stamp_open_jobs(queue_dir: str) -> int:
    """Mark the jobs already waiting as admitted, at the moment `hold` is switched on (FR-12.8).

    Changing the mode does not touch what is in line: jobs without an admission mark are the script's new
    arrivals, and these are not new. File times are kept so the order of the line does not change.
    """
    ensure_queue_dirs(queue_dir)
    stamped = 0
    for state in ("pending", "deferred"):
        for path in _job_files(queue_dir, state):
            job = _read_job(path)
            if job is None or ADMISSION_FIELD in job:
                continue
            times = (path.stat().st_atime, path.stat().st_mtime)
            _write_job(path, {**job, ADMISSION_FIELD: ADMISSION_AUTO})
            os.utime(path, times)
            stamped += 1
    return stamped


def queue_snapshot(queue_dir: str, limit: int = 100) -> dict[str, Any]:
    ensure_queue_dirs(queue_dir)
    root = Path(queue_dir)
    pause_until = daily_quota_pause_until(queue_dir)
    snapshot: dict[str, Any] = {
        "counts": {}, "total": 0,
        "daily_quota_pause": {"retry_at": pause_until} if pause_until is not None else None,
    }
    for state in QUEUE_STATES:
        jobs: list[dict[str, Any]] = []
        files = sorted((root / state).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        snapshot["counts"][state] = len(files)
        snapshot["total"] += len(files)
        for path in files[:limit]:
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                job = {"job_id": path.stem}
            job["_state"] = state
            job["_path"] = str(path)
            if state in {"deferred", "failed"}:
                error_path = path.with_suffix(".error")
                if error_path.exists():
                    job["error"] = error_path.read_text(encoding="utf-8")[-2000:]
                elif job.get("last_error"):
                    job["error"] = str(job["last_error"])[-2000:]
            jobs.append(job)
        snapshot[state] = jobs
    return snapshot


def layer_report(queue_dir: str) -> dict[str, Any]:
    """How the two model layers are doing, from the job files: is the strong model worth having?

    ``by_model`` counts finished translations per model; ``escalated`` counts jobs the default model could
    not finish (invalid output after repair) that moved to the strong model, and how those ended. If few
    jobs escalate, or the strong model does not rescue most of them, the layer is not earning its place.
    """
    root = Path(queue_dir)
    by_model: dict[str, dict[str, int]] = {}
    escalated = {"total": 0, "done": 0, "failed": 0, "waiting": 0}
    for state in QUEUE_STATES:
        for path in (root / state).glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("escalated_from"):
                escalated["total"] += 1
                key = state if state in ("done", "failed") else "waiting"
                escalated[key] += 1
            if state in ("done", "failed") and job.get("gst_model") and (state == "failed" or job.get("result") == "translated"):
                counts = by_model.setdefault(str(job["gst_model"]), {"done": 0, "failed": 0})
                counts[state] += 1
    return {"by_model": by_model, "escalated": escalated}


class JobQueue:
    """Own queue state transitions; execute work through an injected callback."""

    def __init__(self, queue_dir: str) -> None:
        self.queue_dir = Path(queue_dir)
        ensure_queue_dirs(queue_dir)

    def recover_interrupted_jobs(self) -> None:
        """Return work interrupted by a service restart to the pending queue."""
        for processing_path in sorted((self.queue_dir / "processing").glob("*.json")):
            pending_path = self.queue_dir / "pending" / processing_path.name
            if pending_path.exists():
                logging.warning("Keeping interrupted job %s in processing; pending copy already exists", processing_path.name)
                continue
            try:
                job = json.loads(processing_path.read_text(encoding="utf-8"))
                job["stage"] = "Recovered after service restart"
                job["updated_at"] = time.time()
                _write_job(processing_path, job)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
            _move_job(processing_path, pending_path)
            logging.info("Recovered interrupted job %s to pending", processing_path.name)

    def admit_new_jobs(self, mode: str) -> int:
        """Apply the admission mode to what the Bazarr script just wrote (FR-12.2, 12.3); returns how many were held.

        The script writes straight into ``pending/`` and knows nothing of this mode, so the worker decides on
        receipt: in ``hold`` a job without an admission mark moves to ``held/``. Whatever the mode, a job the
        script queues again under an id that is held or rejected is dropped (the "no" is remembered, FR-12.5).
        A job outside ``GST_ONLY_PATHS`` is not held: it is left to end as skipped (FR-12.7).
        """
        held = 0
        for path in sorted((self.queue_dir / "pending").glob("*.json")):
            if (self.queue_dir / HELD / path.name).exists() or (self.queue_dir / REJECTED / path.name).exists():
                path.unlink(missing_ok=True)
                logging.info("Dropped %s: the same job is held or was rejected", path.name)
                continue
            if mode != ADMISSION_HOLD:
                continue
            job = _read_job(path)
            if job is None or ADMISSION_FIELD in job or outside_allowed_paths(str(job.get("subtitle_path", ""))):
                continue
            destination = self.queue_dir / HELD / path.name
            _write_job(destination, {**job, "held_at": time.time()})
            path.unlink(missing_ok=True)
            held += 1
            logging.info("Held job %s | %s -> %s | waiting for approval", path.stem,
                         Path(str(job.get("subtitle_path", ""))).name, job.get("target_code", "?"))
        return held

    def _fail_waiting_quota_jobs(self) -> None:
        for state in ("pending", "deferred"):
            for path in (self.queue_dir / state).glob("*.json"):
                destination = self.queue_dir / "failed" / path.name
                try:
                    _move_job(
                        path, destination,
                        "Daily Gemini quota exhausted; automatic retry cancelled. Submit again after the quota pause expires.",
                    )
                except FileNotFoundError:
                    continue

    def _promote_deferred_jobs(self, now: float) -> None:
        for deferred_path in sorted(
            (self.queue_dir / "deferred").glob("*.json"),
            key=lambda path: path.stat().st_mtime,
        ):
            try:
                job = json.loads(deferred_path.read_text(encoding="utf-8"))
                retry_at = float(job.get("retry_at") or 0)
            except Exception:
                retry_at = 0
            if retry_at > now:
                continue
            pending_path = self.queue_dir / "pending" / deferred_path.name
            if pending_path.exists():
                continue
            _move_job(deferred_path, pending_path)

    def _provider_hold_active(self, now: float) -> bool:
        """While one job waits out a Gemini outage, others would only spend quota on the same errors."""
        for deferred_path in (self.queue_dir / "deferred").glob("*.json"):
            try:
                job = json.loads(deferred_path.read_text(encoding="utf-8"))
                if job.get("deferred_reason") == "provider-unavailable" and float(job.get("retry_at") or 0) > now:
                    return True
            except (OSError, TypeError, ValueError):
                continue
        return False

    def _record_model_quota_pause(self, model: str, retry_at: float, now: float) -> dict[str, float]:
        paused = model_quota_pauses(str(self.queue_dir), now)
        paused[model] = retry_at
        _write_job(self.queue_dir / "provider-pause.json", {"models": paused})
        return paused

    def _ready_job_path(self, settle_seconds: int, now: float | None = None, models: tuple[str, ...] = ()) -> Path | None:
        current_time = time.time() if now is None else now
        if sync_daily_quota_pause(str(self.queue_dir), models, current_time) is not None:
            self._fail_waiting_quota_jobs()
            return None
        self._promote_deferred_jobs(current_time)
        if self._provider_hold_active(current_time):
            return None
        settle_seconds = max(0, settle_seconds)
        jobs = sorted((self.queue_dir / "pending").glob("*.json"), key=lambda path: path.stat().st_mtime)
        best: tuple[int, Path] | None = None  # highest priority wins; oldest first within a priority
        for job_path in jobs:
            priority = 0
            try:
                job = json.loads(job_path.read_text(encoding="utf-8"))
                priority = int(job.get("priority") or 0)
                subtitle_path = Path(str(job.get("subtitle_path") or ""))
                # The settle window protects an actively written subtitle, not
                # the queue event.  A manually queued, already-stable subtitle
                # should therefore start immediately.
                settled_from = subtitle_path.stat().st_mtime if subtitle_path.exists() else float(
                    job.get("created_at") or job_path.stat().st_mtime
                )
            except Exception:
                settled_from = float("-inf")  # unreadable: let the run fail it visibly
            if current_time - settled_from >= settle_seconds and (best is None or priority > best[0]):
                best = (priority, job_path)
        return best[1] if best else None

    def process_once(
        self,
        execute: Callable[..., str],
        *,
        settle_seconds: int = 0,
        now: float | None = None,
        models: tuple[str, ...] = (),
    ) -> bool:
        """Run one ready job; `models` is the primary model then an optional fallback."""
        current_time = time.time() if now is None else now
        job_path = self._ready_job_path(settle_seconds, now=current_time, models=models)
        if job_path is None:
            return False

        processing_path = self.queue_dir / "processing" / job_path.name
        try:
            _move_job(job_path, processing_path)
        except FileNotFoundError:
            return False

        started = time.monotonic()
        error = None
        try:
            job = json.loads(processing_path.read_text(encoding="utf-8"))
            # Once it runs a job is admitted for good: recovery after a restart must not hold it.
            job.setdefault(ADMISSION_FIELD, ADMISSION_AUTO)
            model = select_model(
                models,
                model_quota_pauses(str(self.queue_dir), current_time),
                prefer_fallback=bool(job.get("use_fallback_model")),
            )
            if model:
                job["gst_model"] = model

            def update_status(stage: str, **fields: Any) -> None:
                job["started_at"] = float(job.get("started_at") or time.time())
                job["updated_at"] = time.time()
                job["stage"] = stage
                job.update(fields)
                _write_job(processing_path, job)

            logging.info("Job %s started | %s | %s -> %s", processing_path.stem,
                         Path(str(job.get("subtitle_path", ""))).name,
                         job.get("source_code", "?"), job.get("target_code", "?"))
            update_status("Starting translation")
            status = execute(job, update_status)
            _reset_retry(job)
            for key in ("progress_done", "progress_total"):
                job.pop(key, None)
            job["result"] = status
            _write_job(processing_path, job)
            destination = self.queue_dir / "done" / processing_path.name
            logging.info("Job %s finished | status=%s | duration=%.1fs", processing_path.stem, status, time.monotonic() - started)
        except DailyQuotaExceededError as exc:
            failure_time = time.time()
            retry_at = daily_quota_retry_at(failure_time)
            job.pop("retry_at", None)
            job.pop("deferred_reason", None)
            job["last_error"] = str(exc)
            _write_job(processing_path, job)
            model = str(job.get("gst_model") or "")
            paused = self._record_model_quota_pause(model, retry_at, failure_time) if model and models else {}
            remaining = [candidate for candidate in models if candidate not in paused]
            if paused and remaining:
                # Quota is per model: keep the checkpoint and continue on a model that still has quota.
                destination = self.queue_dir / "pending" / processing_path.name
                logging.warning("Job %s: daily Gemini quota exhausted for %s until %s; continuing on %s.",
                                processing_path.stem, model,
                                datetime.fromtimestamp(retry_at, timezone.utc).isoformat(), remaining[0])
            else:
                if paused:
                    retry_at = min(paused[candidate] for candidate in models)
                destination = self.queue_dir / "failed" / processing_path.name
                error = str(exc)
                _write_job(
                    self.queue_dir / "provider-pause.json",
                    {"retry_at": retry_at, "reason": "daily-quota", "error": str(exc), "models": paused},
                )
                self._fail_waiting_quota_jobs()
                logging.warning("Job %s stopped: daily Gemini quota exhausted. New jobs blocked until %s; waiting jobs marked failed.",
                                processing_path.stem, datetime.fromtimestamp(retry_at, timezone.utc).isoformat())
        except ProviderUnavailableError as exc:
            fallback_available = (
                len(models) > 1
                and job.get("gst_model") != models[1]
                and models[1] not in model_quota_pauses(str(self.queue_dir))
            )
            decision = provider_retry_decision(int(job.get("provider_retry_count") or 0), time.time(), fallback_available)
            if decision.state == "fallback":
                job.pop("retry_at", None)
                job.pop("deferred_reason", None)
                job["use_fallback_model"] = True
                job["last_error"] = str(exc)
                _write_job(processing_path, job)
                destination = self.queue_dir / "pending" / processing_path.name
                logging.warning(
                    "Gemini %s remained unavailable after %s delayed retries; job %s continues on fallback model %s",
                    job.get("gst_model"), len(PROVIDER_RETRY_DELAYS), processing_path.name, models[1],
                )
            elif decision.state == "deferred":
                retry_count = decision.retry_count or 0
                failure_time = time.time()
                retry_at = decision.retry_at or failure_time
                job["provider_retry_count"] = retry_count
                job["retry_at"] = retry_at
                job["deferred_reason"] = "provider-unavailable"
                job["last_error"] = str(exc)
                _write_job(processing_path, job)
                destination = self.queue_dir / "deferred" / processing_path.name
                error = str(exc)
                logging.warning(
                    "Gemini unavailable; job %s deferred until %s (retry %s/%s)",
                    processing_path.name,
                    datetime.fromtimestamp(retry_at, timezone.utc).isoformat(),
                    retry_count,
                    len(PROVIDER_RETRY_DELAYS),
                )
            else:
                destination = self.queue_dir / "failed" / processing_path.name
                error = str(exc)
                logging.error(
                    "Gemini remained unavailable after %s delayed retries; job %s failed",
                    len(PROVIDER_RETRY_DELAYS),
                    processing_path.name,
                )
        except ContextGapError as exc:
            # An earlier episode has no source subtitle yet (FR-5.4): wait for it without holding the queue.
            job["gap_since"] = exc.since
            job["retry_at"] = exc.retry_at
            job["deferred_reason"] = "context-gap"
            job["last_error"] = str(exc)
            _write_job(processing_path, job)
            destination = self.queue_dir / "deferred" / processing_path.name
            error = str(exc)
            logging.info("Job %s deferred until %s: %s", processing_path.stem,
                         datetime.fromtimestamp(exc.retry_at, timezone.utc).isoformat(), exc)
        except HardJobError as exc:
            strong_available = (
                len(models) > 1
                and job.get("gst_model") != models[1]
                and models[1] not in model_quota_pauses(str(self.queue_dir))
            )
            if strong_available:
                # A hard job: the default model could not produce valid output. Try the strong model once.
                job["escalated_from"] = job.get("gst_model")
                job["escalation_reason"] = str(exc)[-300:]
                job["use_fallback_model"] = True
                job["last_error"] = str(exc)
                _write_job(processing_path, job)
                destination = self.queue_dir / "pending" / processing_path.name
                logging.warning("Job %s: %s could not produce valid output; continuing on strong model %s",
                                processing_path.stem, job.get("gst_model"), models[1])
            else:
                destination = self.queue_dir / "failed" / processing_path.name
                error = str(exc)
                logging.error("Job %s failed on %s: %s", processing_path.stem, job.get("gst_model"),
                              str(exc).splitlines()[0][:240] if str(exc) else type(exc).__name__)
        except Exception as exc:
            destination = self.queue_dir / "failed" / processing_path.name
            error = str(exc)
            logging.exception("Job %s failed after %.1fs: %s", processing_path.stem, time.monotonic() - started, str(exc).splitlines()[0][:240] if str(exc) else type(exc).__name__)

        destination.parent.mkdir(parents=True, exist_ok=True)
        _move_job(processing_path, destination, error)
        return True


class FileJobQueue:
    """The :class:`~gst_worker.ports.JobQueue` port over the queue directory tree (the JSON files that
    ``gst_enqueue.sh`` also writes)."""

    def __init__(self, queue_dir: str) -> None:
        self.queue_dir = queue_dir

    def enqueue(self, base_job: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]:
        return enqueue_translation_jobs(self.queue_dir, base_job, targets)

    def enqueue_retranslation(self, job: dict[str, Any]) -> str:
        return enqueue_retranslation(self.queue_dir, job)

    def retry(self, job_id: str) -> bool:
        return retry_failed_job(self.queue_dir, job_id)

    def cancel(self, job_id: str) -> bool:
        return cancel_failed_job(self.queue_dir, job_id)

    def delete(self, state: str, job_id: str) -> bool:
        return delete_queue_job(self.queue_dir, state, job_id)

    def held(self) -> list[HeldJob]:
        return [_held_job(job) for job in list_jobs(self.queue_dir, HELD)]

    def rejected(self) -> list[HeldJob]:
        return [_held_job(job) for job in list_jobs(self.queue_dir, REJECTED)]

    def approve(self, job_ids: list[str]) -> list[str]:
        return approve_jobs(self.queue_dir, list(job_ids))

    def reject(self, job_ids: list[str]) -> list[str]:
        return reject_jobs(self.queue_dir, list(job_ids))

    def restore(self, job_ids: list[str]) -> list[str]:
        return restore_jobs(self.queue_dir, list(job_ids))


_CUE_COUNTS: dict[tuple[str, float], int] = {}


def _cue_count(subtitle_path: str) -> int:
    """How many cues a source subtitle has (timecode lines); 0 when it cannot be read. Cached by file time."""
    try:
        key = (subtitle_path, Path(subtitle_path).stat().st_mtime)
    except OSError:
        return 0
    if key not in _CUE_COUNTS:
        try:
            text = Path(subtitle_path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return 0
        if len(_CUE_COUNTS) > 4096:
            _CUE_COUNTS.clear()
        _CUE_COUNTS[key] = sum(1 for line in text.splitlines() if "-->" in line)
    return _CUE_COUNTS[key]


def _held_job(job: dict[str, Any]) -> HeldJob:
    subtitle_path = str(job.get("subtitle_path") or "")
    return HeldJob(
        job_id=str(job.get("job_id") or ""),
        subtitle_path=subtitle_path,
        video_path=str(job.get("video_path") or ""),
        source_code=str(job.get("source_code") or ""),
        target_code=str(job.get("target_code") or ""),
        source_language=str(job.get("source_language") or ""),
        target_language=str(job.get("target_language") or ""),
        series_id=str(job.get("series_id") or ""),
        media_type=str(job.get("media_type") or ""),
        created_at=float(job.get("created_at") or 0),
        held_at=float(job.get("held_at") or 0),
        cue_count=_cue_count(subtitle_path) if subtitle_path else 0,
    )
