"""How a run ends: ``EndingsMixin`` holds what ``JobQueue`` (``queue.py``) does with each way a job can end: done,
a quota that is spent, a provider that cannot be called, a refused key, an unknown model, a call budget, an earlier
episode that is not ready, a hard job (FR-15.8, FR-17).

A failure of the provider never fails a job: the job waits with a reason (NFR-1); ``failed`` is for the job's own faults.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..domain import queue_policy as policy
from ..domain.config import QUOTA_RESET_TZ
from .jobs import DONE, FAILED, PUBLISH, QUEUED, RUNNING, SKIPPED, WAITING, TRANSLATING_KINDS
from ..domain.queue_policy import Health
from ..domain.translation import (
    CallBudgetError, ContextGapError, DailyQuotaExceededError, HardJobError, ModelNotFoundError, redact,
)


def _reset_retry(job: dict[str, Any]) -> None:
    for key in ("provider_retry_count", "retry_at", "deferred_reason", "last_error", "use_fallback_model", "gap_since",
                "missing_units", "missing_unit_ids"):
        job.pop(key, None)


class EndingsMixin:
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

    def _research_waits(self, job: dict[str, Any], exc: Exception, now: float) -> None:
        """A research job whose search ran out of quota or could not be reached waits by itself (FR-7.5): the grounded search
        has limits of its own, so what it spends must never rest the model translations run on, and the breaker of that model
        is left as it is. The job goes back in line when its wait is over (``rate``: a time, not a health)."""
        job["last_error"] = redact(str(exc))
        if isinstance(exc, DailyQuotaExceededError):
            until = policy.daily_quota_reset_at(now, policy.zone_for(str(self._setting("quota_reset_tz", QUOTA_RESET_TZ))))
            text = "the daily quota of the web search is spent; the research waits for the reset"
        else:
            until = now + policy.PROVIDER_RETRY_DELAYS[0]
            text = "the provider could not be reached for the research; it tries again later"
        self.jobs.transition(job["job_id"], WAITING, payload=job, wait_reason="rate", not_before=until,
                             error=job["last_error"], only_from=(RUNNING,))
        self._event("job.waiting", job, text, level="WARNING", wait="rate", until=until)

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
