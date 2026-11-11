"""SQLite store for machine-owned state (ADR-5): subtitle versions and LLM call records.

The schema is a subset of docs/design/ARCHITECTURE.md section 4. ``translations`` is keyed by
``output_path`` until the ``units`` table exists (P3/P4); a later migration adds ``unit_id``.
Queue job files stay on disk for now, so there is no ``jobs`` table yet.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE translations (
        id INTEGER PRIMARY KEY,
        output_path TEXT NOT NULL,
        source_path TEXT NOT NULL DEFAULT '',
        source_sha256 TEXT NOT NULL DEFAULT '',
        target_lang TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL,
        stored_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        context_hash TEXT NOT NULL DEFAULT '',
        trigger TEXT NOT NULL,            -- auto | retranslate | revert | external
        parent_version INTEGER,
        status TEXT NOT NULL,             -- current | superseded
        model TEXT NOT NULL DEFAULT '',
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE (output_path, version)
    );
    CREATE INDEX translations_cache ON translations (source_sha256, target_lang, context_hash);
    CREATE TABLE llm_calls (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        task TEXT NOT NULL,
        tokens_in INTEGER NOT NULL,
        tokens_out INTEGER NOT NULL,
        latency_ms INTEGER NOT NULL,
        outcome TEXT NOT NULL,
        at REAL NOT NULL
    );
    CREATE INDEX llm_calls_at ON llm_calls (at);
    """,
)


class Store:
    """One connection guarded by a lock: the worker thread and HTTP threads share it."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _migrate(self) -> None:
        with self._lock:
            current = int(self._db.execute("PRAGMA user_version").fetchone()[0])
            for number, script in enumerate(_MIGRATIONS[current:], start=current + 1):
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

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._db.execute("PRAGMA user_version").fetchone()[0])

    # -- llm_calls ---------------------------------------------------------
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
        with self._lock:
            self._db.execute(
                "INSERT INTO llm_calls (job_id, provider, model, task, tokens_in, tokens_out, latency_ms, outcome, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, provider, model, task, tokens_in, tokens_out, latency_ms, outcome, at or time.time()),
            )

    def llm_calls(self, job_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if job_id is None:
                rows = self._db.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM llm_calls WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return [dict(row) for row in rows]

    def token_totals(self, since: float = 0.0) -> dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens_in), 0) AS tokens_in,"
                " COALESCE(SUM(tokens_out), 0) AS tokens_out FROM llm_calls WHERE at >= ?",
                (since,),
            ).fetchone()
        return {"calls": int(row["calls"]), "tokens_in": int(row["tokens_in"]), "tokens_out": int(row["tokens_out"])}

    # -- translations ------------------------------------------------------
    def current_version(self, output_path: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM translations WHERE output_path = ? AND status = 'current'", (output_path,)
            ).fetchone()
        return dict(row) if row else None

    def versions(self, output_path: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM translations WHERE output_path = ? ORDER BY version", (output_path,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_version(self, output_path: str, version: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM translations WHERE output_path = ? AND version = ?", (output_path, version)
            ).fetchone()
        return dict(row) if row else None

    def next_version(self, output_path: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM translations WHERE output_path = ?", (output_path,)
            ).fetchone()
        return int(row[0])

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
    ) -> None:
        with self._lock:
            self._db.execute("BEGIN")
            try:
                if make_current:
                    self._db.execute(
                        "UPDATE translations SET status = 'superseded' WHERE output_path = ? AND status = 'current'",
                        (output_path,),
                    )
                self._db.execute(
                    "INSERT INTO translations (output_path, source_path, source_sha256, target_lang, version,"
                    " stored_path, sha256, context_hash, trigger, parent_version, status, model, tokens_in,"
                    " tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        output_path, source_path, source_sha256, target_lang, version, stored_path, sha256,
                        context_hash, trigger, parent_version, "current" if make_current else "superseded",
                        model, tokens_in, tokens_out, time.time(),
                    ),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def cached_versions(self, source_sha256: str, target_lang: str, context_hash: str) -> Iterable[dict[str, Any]]:
        """Versions made from the same input and context, newest first (NFR-3)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM translations WHERE source_sha256 = ? AND target_lang = ? AND context_hash = ?"
                " AND trigger IN ('auto', 'retranslate') ORDER BY id DESC",
                (source_sha256, target_lang, context_hash),
            ).fetchall()
        return [dict(row) for row in rows]
