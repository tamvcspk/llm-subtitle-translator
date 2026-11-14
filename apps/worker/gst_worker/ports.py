"""Ports of the worker core (SPEC ADR-8).

A port is a narrow ``Protocol`` that the core defines for something it needs from outside: storage, the
queue, Bazarr, TMDB, the provider. Adapters (``store.py`` for SQLite today) implement it; the core only
ever sees these types. Ports return named, immutable types, never ``dict`` or ``sqlite3.Row``, so column
names exist only inside the adapter. Wiring happens in ``RuntimeContext`` and ``worker.py``.

While ``Store`` still implements several ports at once, a port's method names carry the port's prefix
(``auth_account``) so they do not collide there.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from subcore.memory import Facts


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


CURRENT = "current"
SUPERSEDED = "superseded"


@dataclass(frozen=True)
class TranslationVersion:
    """One stored version of a target subtitle file (FR-8.1). ``status`` is ``current`` or ``superseded``;
    ``trigger`` is auto | retranslate | revert | external | cue_edit. ``missing_units`` lists the earlier
    units that had no usable facts when this was made (``context_complete`` is then False)."""

    output_path: str
    version: int
    stored_path: str
    sha256: str
    trigger: str
    status: str
    created_at: float
    source_path: str = ""
    source_sha256: str = ""
    target_lang: str = ""
    context_hash: str = ""
    parent_version: int | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    unit_id: int | None = None
    glossary_hash: str = ""
    context_complete: bool = True
    memory_hash: str = ""
    missing_units: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredFacts:
    """Story facts extracted from one source subtitle (FR-5.1). Identified by unit, source hash and
    extraction prompt version; ``created_at`` is when they were saved or last used (``touch``)."""

    unit_id: int
    source_sha256: str
    prompt_version: str
    model: str
    facts: Facts
    facts_hash: str
    tokens_in: int
    tokens_out: int
    created_at: float


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


@runtime_checkable
class VersionRepo(Protocol):
    """History of every target subtitle file, keyed by output path, plus the result cache (NFR-3)."""

    def current_version(self, output_path: str) -> TranslationVersion | None:
        """The version whose ``status`` is ``current``, if any."""

    def versions(self, output_path: str) -> list[TranslationVersion]:
        """All versions of one file, oldest first."""

    def get_version(self, output_path: str, version: int) -> TranslationVersion | None:
        """One version by number."""

    def next_version(self, output_path: str) -> int:
        """Number the next version of this file should get (1 for a new file)."""

    def add_version(
        self,
        output_path: str,
        *,
        version: int,
        stored_path: str,
        sha256: str,
        trigger: str,
        make_current: bool = True,
        source_path: str = "",
        source_sha256: str = "",
        target_lang: str = "",
        context_hash: str = "",
        parent_version: int | None = None,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        unit_id: int | None = None,
        glossary_hash: str = "",
        context_complete: bool = True,
        memory_hash: str = "",
        missing_units: Iterable[str] = (),
    ) -> None:
        """Record a version. With ``make_current`` it atomically supersedes the file's previous current one."""

    def cached_versions(self, source_sha256: str, target_lang: str, context_hash: str) -> list[TranslationVersion]:
        """Auto and retranslate versions made from the same input and context, newest first."""


@runtime_checkable
class MemoryRepo(Protocol):
    """Story memory (FR-5): facts per unit, the cache of compressed arcs, and the snapshots of context bundles."""

    def get_facts(
        self, unit_id: int, source_sha256: str, prompt_version: str, *, touch: bool = False
    ) -> StoredFacts | None:
        """Facts for exactly this source and prompt version. ``touch`` marks them as the newest in use."""

    def save_facts(
        self,
        unit_id: int,
        source_sha256: str,
        prompt_version: str,
        *,
        model: str,
        facts: Facts,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        """Store facts, replacing any for the same unit, source and prompt version; they become the newest."""

    def latest_facts(self, unit_ids: Iterable[int], prompt_version: str) -> dict[int, StoredFacts]:
        """For each unit with any, the facts most recently saved or used under ``prompt_version``."""

    def get_summary(self, input_hash: str) -> str | None:
        """A compressed arc by the hash of the entries it summarizes."""

    def put_summary(self, series_id: int, up_to_unit: int, input_hash: str, text: str) -> None:
        """Cache a compressed arc."""

    def put_snapshot(self, memory_hash: str, sections: Iterable[tuple[str, str]]) -> None:
        """Keep the (title, body) sections of the context a fingerprint stands for."""

    def get_snapshot(self, memory_hash: str) -> list[tuple[str, str]] | None:
        """The sections kept for a fingerprint, or None."""
