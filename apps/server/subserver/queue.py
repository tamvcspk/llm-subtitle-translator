"""The job queue: how jobs are made, claimed, run, waited on and ended (FR-15, FR-17, ADR-11).

Jobs are rows of the ``jobs`` table (``jobs.JobStore``); this module is the lifecycle on top of them. What it
decides comes from ``queue_policy`` (pure): which job goes next, when a provider may be called again, when a
daily quota resets. A failure of the provider never fails a job: the job *waits* with a reason and runs when the
provider can be called again (NFR-1, FR-15.8); ``failed`` is for the job's own faults.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import queue_policy as policy
from .config import QUOTA_RESET_TZ, enabled_target_languages
from .jobs import (
    CANCELLED, DONE, FAILED, LEGACY_STATES, OPEN_STATES, PUBLISH, QUEUED, REPLACE, RUNNING, SKIPPED, VARIANT, WAITING,
    EXTRACT, SUGGEST, TRANSLATING_KINDS, JobStore, FROM_LEGACY, extract_key, job_key, retranslation_key, suggest_key,
    variant_key,
)
from .ports import Telemetry
from .queue_policy import Health
from .store import Store
from .subtitles import existing_target_subtitle, target_output_path
from .telemetry import NULL as NULL_TELEMETRY
from .translation import (
    CallBudgetError, ContextGapError, DailyQuotaExceededError, HardJobError, JobStopped, ModelNotFoundError,
    ProviderAuthError, ProviderUnavailableError, redact,
)

RETRANSLATE_PRIORITY = policy.RETRANSLATE
SETTINGS_SNAPSHOT_KEYS = (
    "llm_provider", "llm_base_url", "gst_model", "gst_fallback_model", "gst_temperature", "gst_thinking_budget",
    "gst_no_thinking", "memory_enabled", "memory_model", "summarize_model", "research_model",
    "max_calls_per_job", "max_calls_per_day", "breaker_threshold",
)


# --- what may be translated at all (kept from the file queue: pure checks on a job dict) ----------------------
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


def skip_reason(job: dict[str, Any]) -> str | None:
    """Why a job must not translate anything, or None when it should run."""
    provider = str(job.get("provider", "")).lower()
    language = str(job.get("source_code") or job.get("language") or "").split(":", 1)[0].lower()
    subtitle_path = str(job.get("subtitle_path", ""))
    target_code = str(job.get("target_code") or "vi")
    output_path = str(job.get("output_path") or target_output_path(subtitle_path, target_code, source_code=language))

    if provider and provider not in {"embeddedsubtitles", "gemini-console", "manual", "local"}:
        return f"provider {provider} is not handled"
    if language and language == target_code.lower():
        return "source and target language are the same"
    if not subtitle_path or not Path(subtitle_path).exists():
        return "source subtitle is gone"
    # An explicit retranslation exists to replace the target (the version history keeps the old one); a variant
    # exists to be compared with it and is never put on disk, so an existing target is no reason to skip it.
    if job.get("trigger") in ("retranslate", "variant"):
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


def job_id_for(subtitle_path: str, output_path: str, target_code: str) -> str:
    return job_key(subtitle_path, output_path, target_code)


# --- reasons shown in the event log ---------------------------------------------------------------------------------
def _claim_reason(job: dict[str, Any]) -> str:
    if job.get("trigger") == "retranslate":
        return "an explicit retranslation runs ahead of automatic work"
    if job.get("tier") == "strong" or job.get("escalated_from"):
        return "second attempt on the strong model"
    return f"the best ready job ({policy.priority_class(int(job.get('prio') or 0))} class)"


def _model_reason(model: str | None, models: tuple[str, ...], paused: set[str], job: dict[str, Any]) -> str:
    if not model:
        return "no model is configured"
    if len(models) > 1 and model == models[1]:
        if job.get("escalated_from"):
            return f"the job is hard: {job['escalated_from']} could not produce valid output"
        if models[0] in paused:
            return f"{models[0]} cannot be called now (quota or breaker); the strong model takes over"
        return f"{models[0]} was unavailable; the strong model takes over"
    return "the default model takes every job it can"


def _reset_retry(job: dict[str, Any]) -> None:
    for key in ("provider_retry_count", "retry_at", "deferred_reason", "last_error", "use_fallback_model", "gap_since",
                "missing_units", "missing_unit_ids"):
        job.pop(key, None)


class _Heartbeat(threading.Thread):
    """Renews a running job's lease while the worker thread is inside a long model call (FR-15.9)."""

    def __init__(self, jobs: JobStore, job_id: str, lease_seconds: float) -> None:
        super().__init__(daemon=True, name=f"lease-{job_id[:8]}")
        self._jobs, self._job_id, self._lease = jobs, job_id, lease_seconds
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(max(1.0, self._lease / 3)):
            try:
                self._jobs.heartbeat(self._job_id, self._lease)
            except Exception:  # noqa: BLE001 - a missed beat only shortens the lease
                logging.debug("heartbeat of %s failed", self._job_id, exc_info=True)

    def stop(self) -> None:
        self._stop_event.set()


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class JobQueue:
    """Makes jobs, picks the next one, runs it and decides what each ending means."""

    def __init__(
        self,
        store: Store,
        telemetry: Telemetry | None = None,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        key_ok: Callable[[], bool] | None = None,
    ) -> None:
        self.store = store
        self.jobs = JobStore(store, clock=clock, monotonic=monotonic)
        self.telemetry: Telemetry = telemetry or NULL_TELEMETRY
        # Whether the provider has a key that was not refused: a refused key clears itself once the admin fixes it.
        self.key_ok = key_ok
        self.provider = "gemini"
        self.models: tuple[str, ...] = ()
        self.settings: dict[str, Any] = {}

    # -- configuration --------------------------------------------------------------------------------------------
    @property
    def clock(self) -> Callable[[], float]:
        return self.jobs.clock

    def configure(self, settings: dict[str, Any], models: tuple[str, ...], provider: str = "") -> None:
        """Adopt the settings and models in force. A model the admin changed to is a fresh start: what was learnt
        about a model name that is no longer configured (a wrong name above all) is forgotten."""
        previous = set(self.models)
        self.settings = settings or {}
        self.models = tuple(models)
        self.provider = provider or str(self.settings.get("llm_provider") or "") or self.provider
        if previous and set(self.models) != previous:
            for (prov, model), health in self.jobs.health_map().items():
                if model != policy.ALL_MODELS and health.state == policy.INVALID and model not in self.models:
                    self.jobs.set_health(prov, model, Health())

    def _setting(self, key: str, default: Any) -> Any:
        value = self.settings.get(key)
        return default if value is None or value == "" else value

    def _emit(self, kind: str, **data: Any) -> None:
        """Telemetry never decides a job's outcome (NFR-11): whatever it does, the queue goes on."""
        try:
            self.telemetry.event(kind, **data)
        except Exception:  # noqa: BLE001
            logging.debug("telemetry event %s failed", kind, exc_info=True)

    def _event(self, kind: str, job: dict[str, Any] | None, reason: str, *, level: str = "INFO", **data: Any) -> None:
        """One decision of the queue, with the job it concerns (FR-19.1)."""
        job = job or {}
        self._emit(
            kind, level=level, job_id=str(job.get("job_id") or ""), unit_id=job.get("unit_id") or None,
            reason=reason, subtitle=Path(str(job.get("subtitle_path") or "")).name or None,
            target=job.get("target_code") or None, **data,
        )

    # -- making jobs -----------------------------------------------------------------------------------------------
    def enqueue(
        self, base_job: dict[str, Any], targets: list[dict[str, Any]], *, origin: str = "manual",
        prio: int = policy.INTERACTIVE,
    ) -> list[str]:
        """One job per enabled target language that is still missing; returns the ids queued (the admin's own
        requests are the interactive class). A day's quota being spent does not stop a request: it waits (NFR-1)."""
        created: list[str] = []
        subtitle_path = str(base_job.get("subtitle_path", ""))
        name = Path(subtitle_path).name

        def skipped(reason: str, target_code: str = "", job_id: str = "", level: str = "INFO") -> None:
            self._emit("enqueue.skipped", level=level, job_id=job_id, reason=reason, subtitle=name,
                                 target=target_code or None, unit_id=base_job.get("unit_id") or None)

        if not subtitle_path or not Path(subtitle_path).exists():
            skipped("the source subtitle does not exist")
            return created
        source_code = str(base_job.get("source_code") or base_job.get("language") or "en").split(":", 1)[0].strip()
        source_language = str(base_job.get("source_language") or source_code or "English")
        if outside_allowed_paths(subtitle_path):
            logging.warning("Not queueing %s: outside GST_ONLY_PATHS", subtitle_path)
            skipped("the subtitle is outside GST_ONLY_PATHS", level="WARNING")
            return created

        for target in enabled_target_languages({"target_languages": targets}):
            target_code = str(target["code"])
            if source_code.lower() == target_code.lower():
                skipped("the source and the target language are the same", target_code)
                continue
            output_path = str(base_job.get("output_path") or target_output_path(subtitle_path, target_code, source_code=source_code))
            key = job_key(subtitle_path, output_path, target_code)
            # Only a language that is missing gets translated: any sibling subtitle already in it counts.
            if Path(output_path).exists():
                skipped("the target subtitle already exists", target_code, key)
                continue
            sibling = existing_target_subtitle(subtitle_path, target_code, source_code)
            if sibling:
                skipped(f"a {target_code} subtitle is already present: {Path(sibling).name}", target_code, key)
                continue
            if self.jobs.open_for(key) is not None:
                self.jobs.add({}, key=key, prio=prio)  # a higher class lifts the job already in line
                skipped("the same job is already waiting or running", target_code, key)
                continue
            # The same request under another name: the files were renamed after the first job was made (FR-14.3).
            same = self.jobs.open_for_unit(int(base_job["unit_id"]), target_code) if base_job.get("unit_id") else None
            if same is not None:
                self.jobs.lift(same["job_id"], prio)
                skipped("the same request is already waiting or running", target_code, same["job_id"])
                continue
            if origin == "policy":
                # The policy never revives or repeats what failed or what the admin cancelled: only the admin retries (D4).
                ended = self.jobs.last_ended_state(key, base_job.get("unit_id") or None, target_code)
                if ended in (FAILED, CANCELLED):
                    skipped(f"an earlier job for this file {ended}; only the admin asks again", target_code, key)
                    continue
            else:
                failed = self.jobs.latest_for(key, FAILED)
                if failed is not None and self.retry(failed["job_id"]):
                    created.append(failed["job_id"])
                    continue
            job = {
                **base_job, "created_at": int(time.time()), "source_code": source_code, "source_language": source_language,
                "target_code": target_code, "target_language": str(target["language"]), "output_path": output_path,
            }
            added = self.jobs.add(job, key=key, origin=origin, prio=prio)
            if added.job_id:
                created.append(added.job_id)
                self._emit(
                    "job.created", job_id=added.job_id, unit_id=base_job.get("unit_id") or None, subtitle=name,
                    reason=("the auto_request policy asked for it: the target language is missing" if origin == "policy"
                            else "queued from the console: the target language is missing"),
                    target=target_code, origin=origin,
                )
        return created

    def enqueue_retranslation(self, job: dict[str, Any]) -> str:
        """One explicit retranslation ahead of automatic work. A request for a target that already has an open
        ``fill`` job turns that job into the retranslation (FR-15.3); a second retranslation is refused."""
        subtitle_path, output_path = str(job.get("subtitle_path", "")), str(job.get("output_path", ""))
        target_code = str(job.get("target_code", ""))
        key = job_key(subtitle_path, output_path, target_code)
        payload = {**job, "trigger": "retranslate", "priority": RETRANSLATE_PRIORITY, "created_at": int(time.time())}
        window = (job.get("retranslate") or {}).get("range") if isinstance(job.get("retranslate"), dict) else None
        open_job = self.jobs.open_for(key)
        if open_job is not None:
            if open_job["mode"] == REPLACE:
                raise ValueError("A retranslation of this subtitle is already queued")
            if not self.jobs.merge_retranslation(open_job["job_id"], payload, RETRANSLATE_PRIORITY):
                raise ValueError("This subtitle is being translated right now; retranslate it when the job ends")
            job_id = open_job["job_id"]
        else:
            added = self.jobs.add(
                payload, key=key, mode=REPLACE, origin="retranslate", prio=RETRANSLATE_PRIORITY,
                job_id=f"{retranslation_key(subtitle_path, output_path, target_code)}-{time.time_ns()}",
            )
            if not added.job_id:  # raced with another request between the check and the insert
                raise ValueError("A retranslation of this subtitle is already queued")
            job_id = added.job_id
        self._emit(
            "job.created", job_id=job_id, unit_id=job.get("unit_id") or None, subtitle=Path(subtitle_path).name,
            reason="an explicit retranslation: it runs ahead of automatic work and may replace the target",
            target=target_code, origin="retranslate", range=window,
        )
        return job_id

    def retranslation_blocker(self, job: dict[str, Any]) -> str | None:
        """Why ``enqueue_retranslation`` would refuse this job now, or None. Lets the Estimate box say so before
        the admin confirms; the two must agree, so a change to one reads the other."""
        key = job_key(str(job.get("subtitle_path", "")), str(job.get("output_path", "")), str(job.get("target_code", "")))
        open_job = self.jobs.open_for(key)
        if open_job is None:
            return None
        if open_job["mode"] == REPLACE:
            return "A retranslation of this subtitle is already queued"
        if open_job["state"] not in (QUEUED, WAITING):
            return "This subtitle is being translated right now; retranslate it when the job ends"
        return None

    def enqueue_variant(self, job: dict[str, Any]) -> str:
        """One variant job (FR-18.8): the whole episode translated with another style and kept as a candidate, never
        put on disk. At most one is open per episode and language (FR-15.3); a second request is refused."""
        subtitle_path, output_path = str(job.get("subtitle_path", "")), str(job.get("output_path", ""))
        target_code = str(job.get("target_code", ""))
        key = variant_key(subtitle_path, output_path, target_code)
        payload = {**job, "trigger": "variant", "created_at": int(time.time())}
        added = self.jobs.add(
            payload, key=key, kind=VARIANT, mode=VARIANT, origin="variant", prio=policy.INTERACTIVE,
            job_id=f"{key}-{time.time_ns()}",
        )
        if not added.job_id:
            raise ValueError("A variant of this episode is already queued")
        self._emit(
            "job.created", job_id=added.job_id, unit_id=job.get("unit_id") or None, subtitle=Path(subtitle_path).name,
            reason="a variant: the episode is translated with another style and kept as a candidate to compare",
            target=target_code, origin="variant", label=(job.get("variant") or {}).get("label"),
        )
        return added.job_id

    def enqueue_extract(self, units: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        """One ``extract`` job per episode, in the order given (story order, so the facts of the early episodes come
        first): the admin's own request, the interactive class. Each dict carries ``unit_id``, ``series_id`` (what
        a series' jobs are told apart by), ``label`` (``S01E02``) and ``title``. An episode that already has an open
        job is not made twice: that job is lifted to this class. Returns ``(created ids, ids of the jobs already open)``."""
        created: list[str] = []
        existing: list[str] = []
        for unit in units:
            unit_id = int(unit["unit_id"])
            payload = {**unit, "unit_id": unit_id, "created_at": int(time.time())}
            added = self.jobs.add(payload, key=extract_key(unit_id), kind=EXTRACT, mode=EXTRACT, origin="manual",
                                  prio=policy.INTERACTIVE, job_id=f"{extract_key(unit_id)}-{time.time_ns()}")
            if added.job_id:
                created.append(added.job_id)
                self._emit(
                    "job.created", job_id=added.job_id, unit_id=unit_id,
                    reason=f"the admin asked for the facts of {unit.get('label') or 'an episode'} to be read",
                    origin="manual", job_kind=EXTRACT,
                )
            elif added.existing:
                existing.append(added.existing)
        return created, existing

    def enqueue_suggest(
        self, series_id: int, lang: str, *, title: str, bazarr_id: str = "", origin: str = "manual",
        prio: int = policy.INTERACTIVE,
    ) -> tuple[str, bool]:
        """One ``suggest`` job for this series and language: it looks for proposals in the stored facts (at most three
        model calls). Returns ``(job id, whether it is new)``. At most one is open per series and language: a second
        request is that job, lifted to the higher class. The admin's own request is the interactive class; the one a
        translation leaves behind (origin ``facts``) is the lowest, and is not the policy's, so it never counts against
        ``auto_request_daily_cap``."""
        payload = {
            "stored_series_id": int(series_id), "series_id": bazarr_id, "title": title, "target_code": lang,
            "created_at": int(time.time()),
        }
        key = suggest_key(series_id, lang)
        added = self.jobs.add(payload, key=key, kind=SUGGEST, mode=SUGGEST, origin=origin, prio=prio,
                              job_id=f"{key}-{time.time_ns()}")
        if added.job_id:
            self._emit(
                "job.created", job_id=added.job_id, unit_id=None, target=lang, origin=origin, job_kind=SUGGEST,
                reason=("the admin asked for proposals from the facts of " if origin == "manual"
                        else "new facts were read: look for proposals in ") + (title or "the series"),
            )
            return added.job_id, True
        return str(added.existing), False

    def retry(self, job_id: str) -> bool:
        """Put a failed job back in line with a fresh retry cycle (an admin's request)."""
        job = self.jobs.retry(job_id)
        if job is None:
            return False
        self._event("job.created", job, "a failed job for the same file was put back in line", origin="retry")
        return True

    def cancel(self, job_id: str) -> bool:
        """Remove a failed job for good."""
        return self.jobs.delete((FAILED,), job_id)

    def delete(self, state: str, job_id: str) -> bool:
        """Remove a job from the list it is in. A running one is stopped, not deleted."""
        states = FROM_LEGACY.get(state)
        if not states or state == "processing" or not job_id:
            return False
        if state in ("pending", "deferred"):  # waiting to run: cancelling keeps a record, it does not erase one
            return self.jobs.state_of(job_id) in states and self.jobs.request_stop(job_id) == "cancelled"
        return self.jobs.delete(states, job_id)

    def stop(self, job_id: str) -> str | None:
        """Stop a job: at once when it has not started, at the next batch boundary when it runs (FR-15.11)."""
        outcome = self.jobs.request_stop(job_id)
        if outcome:
            self._event("job.stop_requested", self.jobs.get(job_id), f"the admin stopped the job ({outcome})")
        return outcome

    # -- looking at the queue ------------------------------------------------------------------------------------------
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
            if job.get("escalated_from"):
                escalated["total"] += 1
                escalated[state if state in (DONE, FAILED) else "waiting"] += 1
            if state in (DONE, FAILED) and job.get("gst_model") and (state == FAILED or job.get("result") == "translated"):
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

    # -- usability of models ---------------------------------------------------------------------------------------------
    def _usable(self, model: str, health: dict[tuple[str, str], Health], now: float) -> bool:
        whole = health.get((self.provider, policy.ALL_MODELS), Health())
        return policy.health_usable(whole, now) and policy.health_usable(health.get((self.provider, model), Health()), now)

    def _paused(self, health: dict[tuple[str, str], Health], now: float) -> set[str]:
        return {model for model in self.models if not self._usable(model, health, now)}

    # -- start-up ---------------------------------------------------------------------------------------------------------
    def recover(self) -> int:
        """Give back the jobs a stopped worker was running (FR-15.9). Runs once at startup, explicitly: making a
        ``JobQueue`` recovers nothing."""
        recovered = self.jobs.recover(int(self._setting("max_crashes", policy.MAX_CRASHES)))
        for job, state in recovered:
            if state == FAILED:
                self._event("job.failed", job, "the worker stopped while the job ran, too many times", level="ERROR",
                            cause="crashed", attempts=job.get("attempts"))
            else:
                self._event("job.recovered", job, "the worker stopped while the job ran; it goes back in line",
                            level="WARNING", attempts=job.get("attempts"))
        return len(recovered)

    # -- one tick of the scheduler -----------------------------------------------------------------------------------------
    def _settled_from(self, payload: dict[str, Any], created_at: float, now: float) -> float:
        """The settle window protects an actively written subtitle, not the queue event: a manually queued,
        already-stable subtitle starts at once."""
        if not payload.get("subtitle_path"):
            return float("-inf")  # a job that reads no subtitle (extract, suggest) has nothing to settle
        subtitle = Path(str(payload["subtitle_path"]))
        try:
            return subtitle.stat().st_mtime if subtitle.exists() else float(payload.get("created_at") or created_at)
        except (OSError, TypeError, ValueError):
            return float("-inf")  # unreadable: let the run fail it visibly

    def sweep(self, now: float, settle_seconds: int = 0) -> None:
        """Before each claim: take back jobs whose lease ran out, let waiting jobs go when their wait is over, and
        make the jobs that cannot run now (provider, quota, settle window) wait visibly instead of sitting in line."""
        for job, state in self.jobs.recover(int(self._setting("max_crashes", policy.MAX_CRASHES)), expired_only=True):
            self._event("job.recovered" if state != FAILED else "job.failed", job,
                        "the lease of the job ran out: the worker stopped renewing it", level="WARNING", cause="lease")
        health = self.jobs.health_map()
        self._clear_fixed(health, now)
        health = self.jobs.health_map()
        paused = self._paused(health, now)
        day_cap_until = self._day_cap_until(now)
        for job in self.jobs.by_state(WAITING):
            reason = job["wait_reason"]
            if reason in ("quota", "provider") and self.models:
                # These wait for the provider, not for a time: the health rows already hold the time (an open breaker or a
                # spent quota is usable again at `until`), and a fixed key or a new model lifts them at once.
                if day_cap_until is not None or policy.pick_model(self.models, paused, job["tier"]) is None:
                    continue
            elif job["not_before"] > now or (reason == "quota" and day_cap_until is not None):
                continue  # a time to wait for, and with no model configured there is no health to ask
            if self.jobs.transition(job["job_id"], QUEUED, only_from=(WAITING,)):
                self._event("job.requeued", job, "the wait is over; the job is in line again", waited_for=reason or None)
        for job in self.jobs.by_state(QUEUED):
            blocked = self._blocked(job, health, paused, now, day_cap_until)
            if blocked is None and settle_seconds > 0:
                settled_from = self._settled_from(job, float(job["created_at"] or now), now)
                if now - settled_from < settle_seconds:
                    blocked = ("settle", settled_from + settle_seconds)
            if blocked is not None:
                reason, until = blocked
                if self.jobs.transition(job["job_id"], WAITING, wait_reason=reason, not_before=until, only_from=(QUEUED,)):
                    self._event("job.waiting", job, self._wait_text(reason, until), wait=reason, until=until or None)

    @staticmethod
    def _wait_text(reason: str, until: float) -> str:
        return {
            "quota": "the daily quota is spent; the job waits for the reset",
            "provider": "the provider cannot be called now (a refused key, an unknown model or an open breaker)",
            "settle": "the source subtitle changed a moment ago; the job waits for it to settle",
            "rate": "the provider was busy; the job tries again shortly",
            "context": "an earlier episode is not ready; the job waits for its facts",
        }.get(reason, reason)

    def _day_cap_until(self, now: float) -> float | None:
        cap = int(self._setting("max_calls_per_day", 0))
        if cap <= 0:
            return None
        midnight = policy.next_utc_midnight(now)
        return midnight if self.store.calls_since(midnight - 86_400) >= cap else None

    def _blocked(
        self, job: dict[str, Any], health: dict[tuple[str, str], Health], paused: set[str], now: float,
        day_cap_until: float | None,
    ) -> tuple[str, float] | None:
        """``(wait reason, until)`` when the job cannot run now because of the provider, else None."""
        if day_cap_until is not None:
            return "quota", day_cap_until
        if not self.models or policy.pick_model(self.models, paused, job["tier"]) is not None:
            return None
        relevant = self.models[1:2] if job["tier"] == "strong" else self.models
        states = [health.get((self.provider, m), Health()) for m in relevant]
        whole = health.get((self.provider, policy.ALL_MODELS), Health())
        if whole.state == policy.INVALID or any(h.state == policy.INVALID for h in states):
            return "provider", 0.0
        if any(h.state == policy.HALF_OPEN for h in states):
            return None  # the one trial call is out: the jobs stay in line until it answers
        if all(h.state == policy.EXHAUSTED for h in states):
            return "quota", min(h.until for h in states)
        return "provider", min((h.until for h in states if h.state in (policy.OPEN, policy.EXHAUSTED)), default=0.0)

    def _clear_fixed(self, health: dict[tuple[str, str], Health], now: float) -> None:
        """A refused key lifts when the admin has put in one the provider has not refused (FR-17.5); a quota that
        has reset is forgotten."""
        for (provider, model), h in health.items():
            if provider != self.provider:
                continue
            if h.state == policy.INVALID and model == policy.ALL_MODELS and self.key_ok is not None and self.key_ok():
                self.jobs.set_health(provider, model, Health())
                self._emit("provider.resumed", reason="a key the provider has not refused is in place; the jobs run again")
            elif h.state == policy.EXHAUSTED and h.until <= now:
                self.jobs.set_health(provider, model, Health())

    def resume_provider(self) -> None:
        """The admin's "test key" passed: the provider is open again (FR-17.5)."""
        self.jobs.set_health(self.provider, policy.ALL_MODELS, Health())

    # -- claiming and running ------------------------------------------------------------------------------------------------
    def _decide(self, settle_seconds: int, now: float):
        def decide(candidate, payload, health):
            paused = self._paused(health, now)
            model: str | None = None
            if self.models:
                model = policy.pick_model(self.models, paused, candidate.tier)
                if model is None:
                    return None  # needs a model that cannot be called now: it waits, the others go on (FR-15.7)
            if settle_seconds > 0 and now - self._settled_from(payload, candidate.created_at, now) < settle_seconds:
                return None
            stamp: dict[str, Any] = {}
            changes: dict[tuple[str, str], Health] = {}
            if model:
                stamp["gst_model"] = model
                current = health.get((self.provider, model), Health())
                tried = policy.begin_trial(current, now)  # an open breaker whose rest is over lets exactly one call through
                if tried != current:
                    changes[(self.provider, model)] = tried
            return stamp, changes
        return decide

    def process_once(
        self,
        execute: Callable[..., str],
        *,
        settle_seconds: int = 0,
        now: float | None = None,
        models: tuple[str, ...] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> bool:
        """Run one ready job; ``models`` is the primary model then an optional fallback. Returns False when
        nothing was ready."""
        if models is not None or settings is not None:
            self.configure(settings if settings is not None else self.settings, self.models if models is None else models)
        previous_clock = self.jobs.clock
        if now is not None:
            fixed = now
            self.jobs.clock = lambda: fixed
        try:
            current = self.clock()
            self.sweep(current, settle_seconds)
            job = self.jobs.claim(
                self._decide(settle_seconds, current), lease_seconds=float(self._setting("lease_seconds", policy.LEASE_SECONDS)),
            )
            if job is None:
                return False
            self._run(job, execute, current)
            return True
        finally:
            self.jobs.clock = previous_clock

    def _run(self, job: dict[str, Any], execute: Callable[..., str], now: float) -> None:
        job_id = job["job_id"]
        model = job.get("gst_model")
        models = self.models
        health = self.jobs.health_map()
        paused = self._paused(health, now)
        lease = float(self._setting("lease_seconds", policy.LEASE_SECONDS))
        job["snapshot"] = {
            "model": model, "tier": policy.tier_of(models, model), "provider": self.provider, "claimed_at": now,
            "settings": {key: self.settings.get(key) for key in SETTINGS_SNAPSHOT_KEYS if key in self.settings},
        }
        job.pop("error", None)
        self._event(
            "job.claimed", job, f"the job is the next ready one ({_claim_reason(job)})",
            priority=int(job.get("prio") or 0), trigger=str(job.get("trigger") or "auto"),
            origin=str(job.get("origin") or ""), klass=policy.priority_class(int(job.get("prio") or 0)),
        )
        self._event(
            "model.selected", job, _model_reason(model, models, paused, job), model=model or None,
            layer=policy.tier_of(models, model), escalated_from=job.get("escalated_from"),
        )

        def update_status(stage: str, **fields: Any) -> None:
            job["started_at"] = float(job.get("started_at") or time.time())
            job["updated_at"] = time.time()
            job["stage"] = stage
            job.update(fields)
            # Saving is the heartbeat; it also says whether the job is still ours and whether the admin stopped it.
            if not self.jobs.save_payload(job_id, job, lease_seconds=lease):
                raise JobStopped("the job was taken away from the worker")
            if self.jobs.cancel_requested(job_id):
                raise JobStopped("the admin stopped the job")

        started = time.monotonic()
        beat = _Heartbeat(self.jobs, job_id, lease)
        beat.start()
        logging.info("Job %s started | %s | %s -> %s", job_id, Path(str(job.get("subtitle_path", ""))).name,
                     job.get("source_code", "?"), job.get("target_code", "?"))
        try:
            update_status("Starting translation" if job.get("kind", PUBLISH) in TRANSLATING_KINDS else "Starting")
            status = execute(job, update_status)
            self._finished(job, status, started)
        except JobStopped as exc:
            self.jobs.transition(job_id, CANCELLED, payload=job, error=str(exc), only_from=(RUNNING,))
            self._event("job.cancelled", job, str(exc))
        except DailyQuotaExceededError as exc:
            self._quota_spent(job, exc, now)
        except ProviderUnavailableError as exc:
            self._provider_unavailable(job, exc, now)
        except ProviderAuthError as exc:
            self._key_refused(job, exc, now)
        except ModelNotFoundError as exc:
            self._model_unknown(job, exc, now)
        except CallBudgetError as exc:
            self._over_budget(job, exc, now)
        except ContextGapError as exc:
            self._context_gap(job, exc)
        except HardJobError as exc:
            self._hard_job(job, exc, now)
        except Exception as exc:  # noqa: BLE001 - whatever else went wrong is this job's fault
            text = str(exc)
            self.jobs.transition(job_id, FAILED, payload=job, error=redact(text), snapshot=job.get("snapshot"),
                                 only_from=(RUNNING,))
            logging.exception("Job %s failed after %.1fs: %s", job_id, time.monotonic() - started,
                              text.splitlines()[0][:240] if text else type(exc).__name__)
            self._event("job.failed", job, f"{type(exc).__name__}: {text.splitlines()[0][:200] if text else ''}",
                        level="ERROR", cause="error", model=model)
        finally:
            beat.stop()
            self._release_trial(model)

    def _release_trial(self, model: str | None) -> None:
        """A job that ended without telling whether the provider works (skipped, stopped, failed for its own
        reason) must not leave a half-open breaker locked: the next job gets the trial call."""
        if not model:
            return
        current = self.jobs.health_map().get((self.provider, model), Health())
        if current.state == policy.HALF_OPEN:
            self.jobs.set_health(self.provider, model, Health(policy.OPEN, current.failures, current.level, 0.0, current.reason))

    # -- the endings ----------------------------------------------------------------------------------------------------------
    def _finished(self, job: dict[str, Any], status: str, started: float) -> None:
        model = job.get("gst_model")
        _reset_retry(job)
        for key in ("progress_done", "progress_total"):
            job.pop(key, None)
        job["result"] = status
        state = SKIPPED if str(status).startswith("skipped") else DONE
        self.jobs.transition(job["job_id"], state, payload=job, result=str(status), snapshot=job.get("snapshot"),
                             only_from=(RUNNING,))
        if model and state == DONE:
            self.jobs.set_health(self.provider, model, policy.after_success(self.jobs.health_map().get((self.provider, model), Health())))
        logging.info("Job %s finished | status=%s | duration=%.1fs", job["job_id"], status, time.monotonic() - started)
        self._event("job.done", job, f"the job ended with status {status}", status=status,
                    seconds=round(time.monotonic() - started, 1), model=model)

    def _other_model(self, job: dict[str, Any], now: float) -> str | None:
        """The other layer, when the job may and can go there now."""
        model = job.get("gst_model")
        if len(self.models) < 2 or not model:
            return None
        other = self.models[1] if model == self.models[0] else self.models[0]
        return other if self._usable(other, self.jobs.health_map(), now) else None

    def _requeue_on(self, job: dict[str, Any], other: str, kind: str, reason: str, *, tier: str = "", **data: Any) -> None:
        """Back in line, to run on ``other``. Only a hard job is tied to the strong model (``tier``); a job sent
        elsewhere because its model is resting takes whichever model can be called when its turn comes."""
        self.jobs.transition(job["job_id"], QUEUED, payload=job, tier=tier, only_from=(RUNNING,))
        self._event(kind, job, reason, level="WARNING", from_model=job.get("gst_model"), to_model=other, **data)

    def _quota_spent(self, job: dict[str, Any], exc: Exception, now: float) -> None:
        model = str(job.get("gst_model") or "")
        zone = policy.zone_for(str(self._setting("quota_reset_tz", QUOTA_RESET_TZ)))
        reset = policy.daily_quota_reset_at(now, zone)
        job["last_error"] = redact(str(exc))
        if model:
            current = self.jobs.health_map().get((self.provider, model), Health())
            self.jobs.set_health(self.provider, model, policy.after_quota(current, reset))
        other = self._other_model(job, now)
        if other:
            # Quota is per model: keep going on the model that still has some.
            logging.warning("Job %s: daily quota exhausted for %s until %s; continuing on %s.", job["job_id"], model,
                            time.strftime("%Y-%m-%d %H:%M", time.gmtime(reset)), other)
            self._event("quota.model_paused", job, f"the daily quota of {model} is spent; the job continues on {other}",
                        level="WARNING", model=model, until=reset, continues_on=other)
            self._requeue_on(job, other, "job.escalated", f"the daily quota of {model} is spent; the job continues on {other}",
                             cause="quota")
            return
        until = self.quota_pause_until() or reset
        self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="quota", not_before=until,
                             error=job["last_error"], only_from=(RUNNING,))
        logging.warning("Job %s waits: daily quota exhausted until %s.", job["job_id"], time.strftime("%Y-%m-%d %H:%M", time.gmtime(until)))
        self._event("quota.paused", job, "the daily quota is spent on every configured model; the job waits for the reset",
                    level="WARNING", until=until, models=list(self.models))
        self._event("job.waiting", job, self._wait_text("quota", until), wait="quota", until=until)

    def _provider_unavailable(self, job: dict[str, Any], exc: Exception, now: float) -> None:
        model = str(job.get("gst_model") or "")
        job["last_error"] = redact(str(exc))
        current = self.jobs.health_map().get((self.provider, model), Health())
        updated = policy.after_failure(current, now, int(self._setting("breaker_threshold", policy.BREAKER_THRESHOLD)))
        if model:
            self.jobs.set_health(self.provider, model, updated)
        if updated.state == policy.OPEN:
            self._event("breaker.opened", job, f"{model} failed {updated.failures} times in a row; it rests until the time given",
                        level="WARNING", model=model, until=updated.until, failures=updated.failures)
            other = self._other_model(job, now)
            if other:
                self._requeue_on(job, other, "job.escalated", f"{model} is resting after repeated failures; the job continues on {other}",
                                 cause="provider-unavailable")
                return
            self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="provider", not_before=updated.until,
                                 error=job["last_error"], only_from=(RUNNING,))
            logging.warning("Provider unavailable; job %s waits until %s", job["job_id"], time.strftime("%H:%M:%S", time.gmtime(updated.until)))
            self._event("job.waiting", job, self._wait_text("provider", updated.until), level="WARNING", wait="provider",
                        until=updated.until)
            return
        retry_at = now + int(self._setting("transient_retry_seconds", policy.TRANSIENT_RETRY_SECONDS))
        self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="rate", not_before=retry_at,
                             error=job["last_error"], only_from=(RUNNING,))
        self._event("job.waiting", job, f"the provider is unavailable (failure {updated.failures} of {self._setting('breaker_threshold', policy.BREAKER_THRESHOLD)} before the breaker opens)",
                    level="WARNING", wait="rate", until=retry_at, failures=updated.failures)

    def _key_refused(self, job: dict[str, Any], exc: Exception, now: float) -> None:
        job["last_error"] = redact(str(exc))
        current = self.jobs.health_map().get((self.provider, policy.ALL_MODELS), Health())
        self.jobs.set_health(self.provider, policy.ALL_MODELS, policy.after_invalid(current, "auth"))
        self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="provider", not_before=0.0,
                             error=job["last_error"], only_from=(RUNNING,))
        self._event("provider.key_invalid", job, "the provider refused the API key; jobs wait until a working key is set",
                    level="ERROR", provider=self.provider)
        self._event("job.waiting", job, self._wait_text("provider", 0.0), level="WARNING", wait="provider")

    def _model_unknown(self, job: dict[str, Any], exc: ModelNotFoundError, now: float) -> None:
        model = exc.model or str(job.get("gst_model") or "")
        job["last_error"] = redact(str(exc))
        if model:
            current = self.jobs.health_map().get((self.provider, model), Health())
            self.jobs.set_health(self.provider, model, policy.after_invalid(current, "model"))
        self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="provider", not_before=0.0,
                             error=job["last_error"], only_from=(RUNNING,))
        self._event("provider.model_unknown", job, f"the provider does not know the model {model!r}; check the model in Settings",
                    level="ERROR", model=model)
        self._event("job.waiting", job, self._wait_text("provider", 0.0), level="WARNING", wait="provider")

    def _over_budget(self, job: dict[str, Any], exc: CallBudgetError, now: float) -> None:
        job["last_error"] = redact(str(exc))
        if exc.scope == "day":
            until = policy.next_utc_midnight(now)
            self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="quota", not_before=until,
                                 error=job["last_error"], only_from=(RUNNING,))
            self._event("job.waiting", job, f"max_calls_per_day was reached; the job waits for tomorrow: {exc}",
                        level="WARNING", wait="quota", until=until)
            return
        self.jobs.transition(job["job_id"], FAILED, payload=job, error=job["last_error"], snapshot=job.get("snapshot"),
                             only_from=(RUNNING,))
        self._event("job.failed", job, f"the job reached max_calls_per_job: {exc}", level="ERROR", cause="max_calls_per_job")

    def _context_gap(self, job: dict[str, Any], exc: ContextGapError) -> None:
        # An earlier episode has no source subtitle yet (FR-5.4): wait for it without holding the queue.
        job["gap_since"] = exc.since
        job["missing_units"] = list(exc.missing)
        job["missing_unit_ids"] = list(exc.missing_ids)
        job["last_error"] = redact(str(exc))
        self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="context", not_before=exc.retry_at,
                             error=job["last_error"], only_from=(RUNNING,))
        logging.info("Job %s waits until %s: %s", job["job_id"], time.strftime("%H:%M:%S", time.gmtime(exc.retry_at)), exc)
        self._event("job.waiting", job, f"an earlier episode has no source subtitle yet: {exc}", wait="context",
                    until=exc.retry_at, missing=list(exc.missing))

    def _hard_job(self, job: dict[str, Any], exc: HardJobError, now: float) -> None:
        # Only a translation has a stronger model to go to; an extract or suggest job that fails this way just fails.
        other = self._other_model(job, now) if job.get("kind", PUBLISH) in TRANSLATING_KINDS else None
        model = job.get("gst_model")
        blocked = int(getattr(exc, "blocked", 0) or 0)
        if model:  # the provider answered (badly): it works, so a trial call has passed
            self.jobs.set_health(self.provider, model, policy.after_success(Health()))
        if other and len(self.models) > 1 and other == self.models[1]:
            # A hard job: the default model could not produce valid output. Try the strong model once.
            job["escalated_from"] = model
            job["escalation_reason"] = str(exc)[-300:]
            job["last_error"] = redact(str(exc))
            logging.warning("Job %s: %s could not produce valid output; continuing on strong model %s",
                            job["job_id"], model, other)
            self._requeue_on(job, other, "job.escalated",
                             f"{model} could not produce valid output after repair and splitting; the job goes to {other}",
                             tier="strong", cause="blocked" if blocked else "hard-job", detail=str(exc)[-300:])
            return
        why = (f"the provider refused {blocked} call(s) for their content (blocked), and splitting did not help: "
               if blocked else "") + str(exc)
        self.jobs.transition(job["job_id"], FAILED, payload=job, error=redact(why), snapshot=job.get("snapshot"),
                             only_from=(RUNNING,))
        logging.error("Job %s failed on %s: %s", job["job_id"], model, why.splitlines()[0][:240] if why else "")
        self._event("job.failed", job, ("blocked by the provider's content filter" if blocked else
                                        "the output stayed invalid after repair and splitting and no stronger model is left to try"),
                    level="ERROR", cause="blocked" if blocked else "hard-job", detail=str(exc)[-300:], model=model)
