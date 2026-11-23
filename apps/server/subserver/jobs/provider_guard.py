"""What every job that calls the model shares: its time limit, its call budget, and what a provider's typed
failure means to the queue (PLAN W5 / F7, step 7a).

``TranslationAttempt`` was the only place that did this; an ``extract`` or ``suggest`` job (7b, 7c) runs through the
same code, so the daily quota, the call caps and the breaker apply to all four kinds of job and nothing is classified
twice. The mapping is by exception *type*: nothing here parses error text.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Callable

from subcore.judge import JUDGE_PROMPT_VERSION
from subcore.memory.facts import EXTRACT_PROMPT_VERSION
from subcore.providers import (
    AuthError,
    CallLog,
    DeadlineExceeded,
    ModelNotFound,
    Provider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from subcore.research import RESEARCH_PROMPT_VERSION
from subcore.suggest import SUGGEST_PROMPT_VERSION
from subcore.translate import TranslationError
from subcore.translate.prompt import PROMPT_VERSION

from ..domain.config import model_for_task
from ..domain.ports import MissingApiKey, ProviderFactory
from ..domain.queue_policy import next_utc_midnight
from ..domain.translation import (
    ERROR_TAIL_LENGTH, CallBudgetError, DailyQuotaExceededError, HardJobError, ModelNotFoundError, ProviderAuthError,
    ProviderUnavailableError, redact,
)

DEFAULT_JOB_TIMEOUT_SECONDS = 1800  # NFR-2 watchdog


def job_timeout_seconds() -> float:
    try:
        return max(60.0, float(os.getenv("JOB_TIMEOUT_SECONDS", DEFAULT_JOB_TIMEOUT_SECONDS)))
    except ValueError:
        return float(DEFAULT_JOB_TIMEOUT_SECONDS)


def int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(settings.get(key) if settings.get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def prompt_version_of(task: str, kind: str = "") -> str:
    """The version of the prompt a call of this task used, for the call record (FR-19.1). A proposal generator (P14)
    runs on the ``extract`` model but has a prompt of its own, told apart by the call's ``kind``."""
    if kind == "suggest":
        return f"suggest-{SUGGEST_PROMPT_VERSION}"
    if task == "research":  # both calls of a research, the search and the one that reads it
        return f"research-{RESEARCH_PROMPT_VERSION}"
    if task == "judge":
        return f"judge-{JUDGE_PROMPT_VERSION}"
    if task == "translate":
        return PROMPT_VERSION
    if task in ("extract", "summarize"):
        return f"memory-{EXTRACT_PROMPT_VERSION}"
    return ""


class Budget:
    """The model calls one job may make, and the day's calls all jobs may make (FR-17.6)."""

    def __init__(self, per_job: int, per_day: int, used_today: Callable[[float], int] | None, clock=time.time) -> None:
        self.per_job, self.per_day, self.used_today, self.clock = per_job, per_day, used_today, clock
        self.calls = 0

    @classmethod
    def from_settings(cls, settings: dict[str, Any], usage: Any) -> "Budget":
        return cls(
            int_setting(settings, "max_calls_per_job", 0), int_setting(settings, "max_calls_per_day", 0),
            getattr(usage, "calls_since", None),
        )

    def spend(self) -> None:
        if self.per_job > 0 and self.calls >= self.per_job:
            raise CallBudgetError(f"the job already made {self.calls} model calls (max_calls_per_job={self.per_job})", "job")
        if self.per_day > 0 and self.used_today is not None:
            now = self.clock()
            if self.used_today(next_utc_midnight(now) - 86_400) >= self.per_day:
                raise CallBudgetError(f"the day's {self.per_day} model calls are spent (max_calls_per_day)", "day")
        self.calls += 1


class BudgetedProvider:
    """A provider that counts its calls against a budget before making them."""

    def __init__(self, inner: Provider, budget: Budget) -> None:
        self.inner, self.budget = inner, budget

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def caps(self):
        return self.inner.caps

    def count_tokens(self, req):
        return self.inner.count_tokens(req)

    def generate(self, req):
        self.budget.spend()
        return self.inner.generate(req)


class JobProviders:
    """The providers one job uses: one (and its rate limiter) per model, all under the job's deadline and budget.

    ``model`` is what the queue picked for the job; every task other than translating has a model setting of its own
    (FR-9.3), so ``for_task`` asks ``model_for_task``.
    """

    def __init__(
        self, factory: ProviderFactory | None, settings: dict[str, Any], model: str, *, usage: Any = None,
        on_call: Callable[[CallLog], None] | None = None,
    ) -> None:
        self.factory, self.settings, self.model, self.on_call = factory, settings, model, on_call
        self.deadline = time.monotonic() + job_timeout_seconds()
        self.budget = Budget.from_settings(settings, usage)
        self._providers: dict[str, Provider] = {}

    def for_task(self, task: str) -> Provider:
        name = self.model if task == "translate" else model_for_task(self.settings, task, self.model)
        if name not in self._providers:
            if self.factory is None:
                raise RuntimeError("no provider factory is wired into this job")
            self._providers[name] = BudgetedProvider(
                self.factory(self.settings, name, deadline=self.deadline, on_call=self.on_call), self.budget
            )
        return self._providers[name]


def error_message(prefix: str, exc: BaseException, settings: dict[str, Any]) -> str:
    detail = redact(f"{type(exc).__name__}: {exc}", settings)
    return f"{prefix}: {detail}"[-ERROR_TAIL_LENGTH:]


@contextmanager
def mapped_errors(settings: dict[str, Any], model: str = ""):
    """Turn typed provider failures into the queue's failure classes (never by parsing text). ``model`` is the one
    the work runs on when it is not ``gst_model`` (a task with a model of its own); the queue marks that name unknown."""
    try:
        yield
    except QuotaExhausted as exc:
        raise DailyQuotaExceededError(error_message("Daily quota exhausted", exc, settings)) from None
    except (RateLimited, Unavailable) as exc:
        raise ProviderUnavailableError(error_message("Provider unavailable", exc, settings)) from None
    except DeadlineExceeded:
        raise RuntimeError(
            f"The job exceeded the {int(job_timeout_seconds() // 60)} minute job limit"
        ) from None
    except AuthError as exc:
        raise ProviderAuthError(error_message("The provider rejected the API key", exc, settings)) from None
    except ModelNotFound as exc:
        raise ModelNotFoundError(
            error_message("The provider does not know the model (check the model in Settings)", exc, settings),
            model=model or str(settings.get("gst_model") or ""),
        ) from None
    except MissingApiKey as exc:
        raise RuntimeError(str(exc)) from None
    except TranslationError as exc:
        error = HardJobError(error_message("Translation failed", exc, settings))
        error.issues = list(getattr(exc, "issues", ()))  # type: ignore[attr-defined]
        error.blocked = int(getattr(exc, "blocked", 0) or 0)  # type: ignore[attr-defined]
        raise error from None
    except ProviderError as exc:
        raise RuntimeError(error_message("Translation failed", exc, settings)) from None
