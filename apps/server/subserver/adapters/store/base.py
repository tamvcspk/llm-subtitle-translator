"""The connection of the store: one SQLite file, one lock, short write transactions."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from ...domain.suggestion_keys import identity_key
from .migrations import MIGRATIONS


class Repo:
    """What a repository mixin may use of the store. ``StoreBase`` provides them; they are named here so a
    reader of one table group can see where ``self._db`` and ``self.transaction()`` come from."""

    _lock: threading.RLock
    _db: sqlite3.Connection


class StoreBase(Repo):
    """One connection guarded by a lock: the worker thread and HTTP threads share it."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=10000")  # a second connection (a --once run) waits, it does not fail
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._fill_suggestion_identities()
        self._restrict_files()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One short write transaction (``BEGIN IMMEDIATE``): the worker and the HTTP threads queue for it, so a
        read-then-write such as claiming a job cannot interleave with another claim (ADR-11).

        The lock is re-entrant, but a transaction inside a transaction is a bug: do not nest.
        """
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                # A COMMIT that failed leaves the transaction open: close it, or the shared connection refuses every
                # later BEGIN ("cannot start a transaction within a transaction") until the process restarts.
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def query(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchall()

    def _restrict_files(self) -> None:
        """The database holds API keys: only the worker's own user may read it (FR-20.3). Best effort, as the
        mode means little on some file systems (a Windows host, a mounted share)."""
        if self.path == ":memory:":
            return
        for suffix in ("", "-wal", "-shm"):
            try:
                os.chmod(self.path + suffix, 0o600)
            except OSError:
                pass

    def _migrate(self) -> None:
        with self._lock:
            current = int(self._db.execute("PRAGMA user_version").fetchone()[0])
            for number, script in enumerate(MIGRATIONS[current:], start=current + 1):
                self._db.execute("BEGIN")
                try:
                    for statement in script.split(";"):
                        if statement.strip():
                            self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {number}")
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise

    def _fill_suggestion_identities(self) -> None:
        """Give every suggestion from before migration 14 its identity (idempotent: rows that have one are left alone)."""
        with self._lock:
            rows = self._db.execute("SELECT id, kind, payload_json FROM suggestions WHERE identity = ''").fetchall()
            if not rows:
                return
            self._db.execute("BEGIN")
            try:
                for row in rows:
                    try:
                        payload = json.loads(row["payload_json"])
                    except ValueError:
                        continue
                    key = identity_key(row["kind"], payload) if isinstance(payload, dict) else ""
                    if key:
                        self._db.execute("UPDATE suggestions SET identity = ? WHERE id = ?", (key, row["id"]))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._db.execute("PRAGMA user_version").fetchone()[0])
