"""Rate limiting, retry with backoff and key failover around any provider (NFR-1, NFR-2, FR-9.4).

``RateLimiter`` keeps sliding windows (60 s for requests and tokens, 24 h for the daily
request count) so a caller never exceeds the configured RPM/TPM/RPD. ``ResilientProvider``
wraps a provider with that limiter and retries transient errors with exponential backoff
plus jitter, honouring ``retry_after``. ``FailoverProvider`` moves to the next API key when
one key's quota is gone. Clocks and sleeps are injectable so tests never wait.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .base import (
    AuthError,
    BadOutput,
    Capabilities,
    DeadlineExceeded,
    LLMRequest,
    LLMResponse,
    Provider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)

__all__ = ["CallLog", "FailoverProvider", "RateLimiter", "ResilientProvider", "estimate_tokens"]

MINUTE = 60.0
DAY = 86_400.0


def estimate_tokens(req: LLMRequest) -> int:
    """Cheap input-token estimate (no network); good enough to pace TPM."""
    return max(1, (len(req.system) + len(req.user)) // 4)


class RateLimiter:
    """Blocks in :meth:`acquire` until a call fits RPM and TPM; raises when RPD is spent.

    ``None`` or ``0`` disables a limit. A single request larger than the whole TPM budget
    is let through once the window is empty rather than waiting forever.
    """

    def __init__(
        self,
        rpm: int | None = None,
        tpm: int | None = None,
        rpd: int | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rpm = rpm or None
        self.tpm = tpm or None
        self.rpd = rpd or None
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._day: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._requests and self._requests[0] <= now - MINUTE:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] <= now - MINUTE:
            self._tokens.popleft()
        while self._day and self._day[0] <= now - DAY:
            self._day.popleft()

    def _wait_needed(self, now: float, tokens: int) -> float:
        wait = 0.0
        if self.rpm and len(self._requests) >= self.rpm:
            wait = max(wait, self._requests[0] + MINUTE - now)
        if self.tpm and self._tokens and sum(t for _, t in self._tokens) + tokens > self.tpm:
            used = sum(t for _, t in self._tokens)
            for stamp, count in self._tokens:
                used -= count
                if used + tokens <= self.tpm:
                    wait = max(wait, stamp + MINUTE - now)
                    break
            else:
                wait = max(wait, self._tokens[-1][0] + MINUTE - now)
        return wait

    def acquire(self, tokens: int = 0) -> float:
        """Reserve one request (and ``tokens``); return the seconds spent waiting."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._prune(now)
                if self.rpd and len(self._day) >= self.rpd:
                    raise QuotaExhausted(
                        f"local daily request budget ({self.rpd}) used up",
                        retry_after=max(0.0, self._day[0] + DAY - now),
                    )
                wait = self._wait_needed(now, tokens)
                if wait <= 0:
                    self._requests.append(now)
                    self._day.append(now)
                    if tokens:
                        self._tokens.append((now, tokens))
                    return waited
            self._sleep(wait)
            waited += wait

    def adjust(self, extra_tokens: int) -> None:
        """Account tokens the estimate missed (actual usage above the reservation)."""
        if extra_tokens > 0:
            with self._lock:
                self._tokens.append((self._clock(), extra_tokens))


@dataclass
class CallLog:
    """One attempt at a model call, for the ``llm_calls`` record (NFR-7)."""

    provider: str
    model: str
    task: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    outcome: str  # ok | rate_limited | quota_exhausted | unavailable | bad_output | auth_error | error


_OUTCOMES: tuple[tuple[type[ProviderError], str], ...] = (
    (RateLimited, "rate_limited"),
    (QuotaExhausted, "quota_exhausted"),
    (Unavailable, "unavailable"),
    (BadOutput, "bad_output"),
    (AuthError, "auth_error"),
)


def _outcome(exc: BaseException) -> str:
    for kind, name in _OUTCOMES:
        if isinstance(exc, kind):
            return name
    return "error"


class ResilientProvider:
    """Limiter + backoff around one provider. ``generate`` raises only typed errors."""

    def __init__(
        self,
        inner: Provider,
        limiter: RateLimiter | None = None,
        *,
        max_attempts: int = 5,
        base_delay: float = 5.0,
        max_delay: float = 300.0,
        deadline: float | None = None,
        on_call: Callable[[CallLog], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.inner = inner
        self.limiter = limiter
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.deadline = deadline  # absolute, in ``clock`` units
        self.on_call = on_call
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def caps(self) -> Capabilities:
        return self.inner.caps

    def count_tokens(self, req: LLMRequest) -> int:
        return self.inner.count_tokens(req)

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(0.0, retry_after) * (1.0 + 0.1 * self._rng.random())
        return self._rng.uniform(0.5, 1.0) * min(self.max_delay, self.base_delay * 2**attempt)

    def _check_deadline(self, extra: float = 0.0) -> None:
        if self.deadline is not None and self._clock() + extra >= self.deadline:
            raise DeadlineExceeded("job time budget exhausted")

    def _log(self, req: LLMRequest, started: float, outcome: str, response: LLMResponse | None) -> None:
        if self.on_call is None:
            return
        self.on_call(
            CallLog(
                provider=self.inner.name,
                model=(response.model if response and response.model else self.inner.model),
                task=req.task,
                tokens_in=response.tokens_in if response else 0,
                tokens_out=response.tokens_out if response else 0,
                latency_ms=int((self._clock() - started) * 1000),
                outcome=outcome,
            )
        )

    def generate(self, req: LLMRequest) -> LLMResponse:
        estimate = estimate_tokens(req)
        last: ProviderError | None = None
        for attempt in range(self.max_attempts):
            self._check_deadline()
            if self.limiter is not None:
                self.limiter.acquire(estimate)
                self._check_deadline()
            started = self._clock()
            try:
                response = self.inner.generate(req)
            except (RateLimited, Unavailable) as exc:
                self._log(req, started, _outcome(exc), None)
                last = exc
                if attempt + 1 >= self.max_attempts:
                    break
                delay = self._delay(attempt, exc.retry_after)
                self._check_deadline(delay)
                self._sleep(delay)
                continue
            except ProviderError as exc:
                self._log(req, started, _outcome(exc), None)
                raise
            self._log(req, started, "ok", response)
            if self.limiter is not None:
                self.limiter.adjust(response.tokens_in - estimate)
            return response
        assert last is not None
        raise last


class FailoverProvider:
    """Several providers for one model (one per API key); advance when a key is spent."""

    def __init__(self, providers: list[Provider]) -> None:
        if not providers:
            raise ValueError("FailoverProvider needs at least one provider")
        self.providers = providers
        self.index = 0

    @property
    def current(self) -> Provider:
        return self.providers[self.index]

    @property
    def name(self) -> str:
        return self.current.name

    @property
    def model(self) -> str:
        return self.current.model

    @property
    def caps(self) -> Capabilities:
        return self.current.caps

    def count_tokens(self, req: LLMRequest) -> int:
        return self.current.count_tokens(req)

    def generate(self, req: LLMRequest) -> LLMResponse:
        while True:
            try:
                return self.current.generate(req)
            except (QuotaExhausted, AuthError):
                if self.index + 1 >= len(self.providers):
                    raise
                self.index += 1
