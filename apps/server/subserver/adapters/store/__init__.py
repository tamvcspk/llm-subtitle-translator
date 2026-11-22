"""SQLite store for machine-owned state (ADR-5, ADR-12): the facade over the repositories of one database.

``Store`` is what the rest of the worker holds; tests build it on ``Store(":memory:")`` with the real migrations.
Its methods live in one mixin per group of tables (``auth``, ``bundles``, ``settings``, ``records``, ``insights``,
``versions``, ``library``, ``glossary``, ``suggestions``, ``catalog``, ``memory``); the connection, the lock and the
migrations are in ``base`` and ``migrations``. A method belongs in the file of the table it reads or writes, and
returns a named type from ``domain.ports``, never a ``dict`` or a ``sqlite3.Row`` (criterion 10c).
Jobs and provider health are in ``jobs.jobs`` (``JobStore``) and run their own SQL through ``query`` and
``transaction``.
"""

from __future__ import annotations

from .base import StoreBase
from .migrations import MIGRATIONS
from .auth import AuthRepo
from .bundles import BundleRepo
from .settings import SettingsRepo
from .records import RecordsRepo
from .insights import InsightsRepo
from .versions import VersionRepo
from .library import LibraryRepo
from .glossary import GlossaryRepo
from .suggestions import SuggestionRepo
from .catalog import CatalogRepo
from .memory import MemoryRepo

# The schema scripts, under the name the tests have always used to replay one of them.
_MIGRATIONS = MIGRATIONS


class Store(
    StoreBase,
    AuthRepo,
    BundleRepo,
    SettingsRepo,
    RecordsRepo,
    InsightsRepo,
    VersionRepo,
    LibraryRepo,
    GlossaryRepo,
    SuggestionRepo,
    CatalogRepo,
    MemoryRepo,
):
    """One connection guarded by a lock: the worker thread and HTTP threads share it."""


__all__ = ["MIGRATIONS", "Store", "_MIGRATIONS"]
