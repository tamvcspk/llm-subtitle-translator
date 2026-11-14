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


@runtime_checkable
class AuthRepo(Protocol):
    """The single console account and the key its sessions are signed with."""

    def auth_account(self) -> Account | None:
        """The account, or None before the first run has created one."""

    def save_auth_account(self, user: str, password_hash: str) -> None:
        """Create or overwrite the account. Every write also rotates the session secret, ending all sessions."""

    def auth_session_secret(self) -> str | None:
        """Hex session secret; None without an account. Stable until the account is next written."""
