"""The job queue: how jobs are made, claimed, run, waited on and ended (FR-15, FR-17, ADR-11).

Jobs are rows of the ``jobs`` table (``jobs.JobStore``); this module is the lifecycle on top of them. What it
decides comes from ``queue_policy`` (pure): which job goes next, when a provider may be called again, when a
daily quota resets. A failure of the provider never fails a job: the job *waits* with a reason and runs when the
provider can be called again (NFR-1, FR-15.8); ``failed`` is for the job's own faults.

It holds the lifecycle (configure, claim, run, wait, recover); the rest is in four files it uses: ``skip_rules.py``
(what may be translated at all), ``enqueue.py`` (making jobs), ``queue_report.py`` (looking at the queue) and
``endings.py`` (what each way a run ends means).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..domain import queue_policy as policy
from .jobs import (
    CANCELLED, FAILED, PUBLISH, QUEUED, RUNNING, WAITING, RESEARCH, TRANSLATING_KINDS, JobStore, FROM_LEGACY,
)
from ..domain.ports import Telemetry
from ..domain.queue_policy import Health
from ..adapters.store import Store
from ..adapters.telemetry import NULL as NULL_TELEMETRY
from ..domain.translation import (
    CallBudgetError, ContextGapError, DailyQuotaExceededError, HardJobError, JobStopped, ModelNotFoundError,
    ProviderAuthError, ProviderUnavailableError, redact,
)
from .endings import EndingsMixin
from .enqueue import EnqueueMixin
from .queue_report import ReportMixin
# Re-exported: the checks live in ``skip_rules.py`` and callers keep one door.
from .skip_rules import job_id_for, outside_allowed_paths, should_skip_job, skip_reason  # noqa: F401

SETTINGS_SNAPSHOT_KEYS = (
    "llm_provider", "llm_base_url", "gst_model", "gst_fallback_model", "gst_temperature", "gst_thinking_budget",
    "gst_no_thinking", "memory_enabled", "memory_model", "summarize_model", "research_model",
    "judge_model", "max_calls_per_job", "max_calls_per_day", "breaker_threshold",
)


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


class JobQueue(EnqueueMixin, ReportMixin, EndingsMixin):
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
        except (DailyQuotaExceededError, ProviderUnavailableError) as exc:
            if job.get("kind") == RESEARCH:
                self._research_waits(job, exc, now)
            elif isinstance(exc, DailyQuotaExceededError):
                self._quota_spent(job, exc, now)
            else:
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
