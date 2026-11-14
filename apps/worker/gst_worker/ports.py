"""Ports of the worker core (SPEC ADR-8).

A port is a narrow ``Protocol`` that the core defines for something it needs from outside: storage, the
queue, Bazarr, TMDB, the provider. Adapters (``store.py`` for SQLite today) implement it; the core only
ever sees these types. Ports return named, immutable types, never ``dict`` or ``sqlite3.Row``, so column
names exist only inside the adapter. Wiring happens in ``RuntimeContext`` and ``worker.py``.

While ``Store`` still implements several ports at once, a port's method names carry the port's prefix
(``auth_account``) so they do not collide there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Account:
    """The one console account. The hash stays out of ``repr`` so a traceback or log line cannot leak it."""

    user: str
    password_hash: str = field(repr=False)


@dataclass(frozen=True)
class LlmCall:
    """One recorded provider call (NFR-7). ``at`` is epoch seconds."""

    id: int
    job_id: str
    provider: str
    model: str
    task: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    outcome: str
    at: float


@dataclass(frozen=True)
class TokenTotals:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


@runtime_checkable
class AuthRepo(Protocol):
    """The single console account and the key its sessions are signed with."""

    def auth_account(self) -> Account | None:
        """The account, or None before the first run has created one."""

    def save_auth_account(self, user: str, password_hash: str) -> None:
        """Create or overwrite the account. Every write also rotates the session secret, ending all sessions."""

    def auth_session_secret(self) -> str | None:
        """Hex session secret; None without an account. Stable until the account is next written."""


@runtime_checkable
class UsageLog(Protocol):
    """Record of every provider call, for the call history and token totals."""

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
    ) -> None:
        """Append one call; ``at`` defaults to now."""

    def llm_calls(self, job_id: str | None = None) -> list[LlmCall]:
        """Calls in the order recorded, for one job or (without ``job_id``) all of them."""

    def token_totals(self, since: float = 0.0) -> TokenTotals:
        """Number of calls and token sums for calls at or after ``since`` (epoch seconds)."""
