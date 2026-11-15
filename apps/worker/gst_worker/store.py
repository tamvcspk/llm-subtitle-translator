"""SQLite store for machine-owned state (ADR-5): subtitle versions, LLM call records, and the library.

The schema is a subset of docs/design/ARCHITECTURE.md section 4. ``translations`` is still keyed by
``output_path`` (one history per target file) and carries a nullable ``unit_id`` linking it to the
library (``series`` / ``units``, with ``unit_outputs`` mapping a unit and target language to its file).
Queue job files stay on disk for now, so there is no ``jobs`` table yet.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from subcore.memory import Facts

from .ports import (
    Account, ApiKey, SettingRow, BodyRef, Correction, EventRecord, LlmCall, Output, QualitySignal, Series, StoredFacts, TokenTotals,
    TranslationVersion, Unit,
)

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
    # P3: library (series, units), corrections, and the link from a version to its unit.
    """
    CREATE TABLE series (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL DEFAULT '',
        bazarr_id TEXT NOT NULL DEFAULT '',
        stale_policy TEXT NOT NULL DEFAULT 'flag',   -- flag | ignore
        created_at REAL NOT NULL
    );
    CREATE TABLE units (
        id INTEGER PRIMARY KEY,
        series_id INTEGER REFERENCES series(id),
        movie_key TEXT,
        season INTEGER,
        episode INTEGER,
        title TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT '',
        bazarr_id TEXT NOT NULL DEFAULT '',
        video_path TEXT NOT NULL DEFAULT '',
        source_path TEXT NOT NULL DEFAULT '',
        source_code TEXT NOT NULL DEFAULT '',
        source_language TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX units_episode ON units (series_id, season, episode) WHERE series_id IS NOT NULL;
    CREATE UNIQUE INDEX units_movie ON units (movie_key) WHERE movie_key IS NOT NULL;
    CREATE TABLE unit_outputs (
        unit_id INTEGER NOT NULL REFERENCES units(id),
        target_lang TEXT NOT NULL,
        target_language TEXT NOT NULL DEFAULT '',
        output_path TEXT NOT NULL,
        PRIMARY KEY (unit_id, target_lang)
    );
    CREATE TABLE corrections (
        id INTEGER PRIMARY KEY,
        unit_id INTEGER NOT NULL REFERENCES units(id),
        target_lang TEXT NOT NULL,
        kind TEXT NOT NULL,                -- term | address | note | cue_edit (scope: episode only)
        payload_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX corrections_unit ON corrections (unit_id, target_lang);
    ALTER TABLE translations ADD COLUMN unit_id INTEGER;
    ALTER TABLE translations ADD COLUMN glossary_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE translations ADD COLUMN context_complete INTEGER NOT NULL DEFAULT 1;
    CREATE INDEX translations_unit ON translations (unit_id);
    """,
    # P4: story memory. Facts are immutable per (unit, source sha256, extraction prompt version).
    # series.memory is '' (follow the global setting), 'on' or 'off'; specials_after anchors season 0.
    # translations.memory_hash is the fingerprint of the facts a version was made with ('' = none used);
    # missing_units_json lists the earlier units that had no usable facts at the time (context_complete = 0).
    """
    ALTER TABLE series ADD COLUMN memory TEXT NOT NULL DEFAULT '';
    ALTER TABLE series ADD COLUMN specials_after TEXT NOT NULL DEFAULT '';
    CREATE TABLE facts (
        id INTEGER PRIMARY KEY,
        unit_id INTEGER NOT NULL REFERENCES units(id),
        source_sha256 TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        json TEXT NOT NULL,
        facts_hash TEXT NOT NULL,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE (unit_id, source_sha256, prompt_version)
    );
    CREATE TABLE summaries_cache (
        id INTEGER PRIMARY KEY,
        series_id INTEGER NOT NULL REFERENCES series(id),
        up_to_unit INTEGER NOT NULL,
        input_hash TEXT NOT NULL UNIQUE,
        text TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE context_snapshots (
        memory_hash TEXT PRIMARY KEY,
        sections_json TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    ALTER TABLE translations ADD COLUMN memory_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE translations ADD COLUMN missing_units_json TEXT NOT NULL DEFAULT '[]';
    """,
    # Schema 4: the single console account (P0c); password_hash is "scrypt$N$r$p$salt$hash".
    """
    CREATE TABLE auth (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        user TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    # Schema 5: the key console sessions are signed with; rotated whenever the account is written.
    """
    ALTER TABLE auth ADD COLUMN session_secret TEXT NOT NULL DEFAULT '';
    """,
    # Schema 6 (P5, ADR-15): the event log, the quality signals, the index of kept prompt bodies, and the
    # fuller record of a model call. bundle_hash and style_hash stay '' until P11 and P12 compute them.
    """
    ALTER TABLE llm_calls ADD COLUMN kind TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN prompt_version TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN bundle_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN style_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN glossary_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN batch INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE llm_calls ADD COLUMN http_status INTEGER;
    ALTER TABLE llm_calls ADD COLUMN finish_reason TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN retries INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE llm_calls ADD COLUMN repairs INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE llm_calls ADD COLUMN cost_est REAL NOT NULL DEFAULT 0;
    CREATE INDEX llm_calls_job ON llm_calls (job_id);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        level TEXT NOT NULL,
        kind TEXT NOT NULL,
        job_id TEXT NOT NULL DEFAULT '',
        unit_id INTEGER,
        reason TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX events_job ON events (job_id, id);
    CREATE INDEX events_unit ON events (unit_id, id);
    CREATE INDEX events_kind ON events (kind, id);
    CREATE INDEX events_at ON events (at);
    CREATE TABLE llm_bodies (
        call_id INTEGER PRIMARY KEY REFERENCES llm_calls(id),
        file TEXT NOT NULL,
        byte_offset INTEGER NOT NULL,
        byte_length INTEGER NOT NULL,
        rotated INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX llm_bodies_file ON llm_bodies (file);
    CREATE TABLE quality_signals (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        unit_id INTEGER,
        version INTEGER,
        cue INTEGER,
        before_text TEXT NOT NULL DEFAULT '',
        after_text TEXT NOT NULL DEFAULT '',
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX quality_signals_kind ON quality_signals (kind, id);
    CREATE INDEX quality_signals_unit ON quality_signals (unit_id, id);
    """,
    # Schema 7 (P6, ADR-13): console settings and API keys. Keys of `settings` that start with "_" belong to the
    # worker (the one-time config.json import and the env key seeding leave their marker there).
    # api_keys.value is stored as typed: the worker has to read it back to call the provider (FR-20.3).
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE api_keys (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        value TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ok',     -- ok | invalid | exhausted
        last_used_at REAL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX api_keys_provider ON api_keys (provider, id);
    """,
)


def _version_from_row(row: sqlite3.Row) -> TranslationVersion:
    return TranslationVersion(
        output_path=row["output_path"], version=row["version"], stored_path=row["stored_path"],
        sha256=row["sha256"], trigger=row["trigger"], status=row["status"], created_at=row["created_at"],
        source_path=row["source_path"], source_sha256=row["source_sha256"], target_lang=row["target_lang"],
        context_hash=row["context_hash"], parent_version=row["parent_version"], model=row["model"],
        tokens_in=row["tokens_in"], tokens_out=row["tokens_out"], unit_id=row["unit_id"],
        glossary_hash=row["glossary_hash"], context_complete=bool(row["context_complete"]),
        memory_hash=row["memory_hash"], missing_units=tuple(json.loads(row["missing_units_json"] or "[]")),
    )


def _key_from_row(row: sqlite3.Row) -> ApiKey:
    return ApiKey(
        id=row["id"], provider=row["provider"], label=row["label"], value=row["value"], status=row["status"],
        last_used_at=row["last_used_at"], updated_at=row["updated_at"],
    )


def _series_from_row(row: sqlite3.Row) -> Series:
    return Series(
        id=row["id"], key=row["key"], title=row["title"], bazarr_id=row["bazarr_id"],
        stale_policy=row["stale_policy"], memory=row["memory"], specials_after=row["specials_after"],
        created_at=row["created_at"],
    )


def _unit_from_row(row: sqlite3.Row) -> Unit:
    return Unit(
        id=row["id"], series_id=row["series_id"], movie_key=row["movie_key"], season=row["season"],
        episode=row["episode"], title=row["title"], media_type=row["media_type"], bazarr_id=row["bazarr_id"],
        video_path=row["video_path"], source_path=row["source_path"], source_code=row["source_code"],
        source_language=row["source_language"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _output_from_row(row: sqlite3.Row) -> Output:
    return Output(
        unit_id=row["unit_id"], target_lang=row["target_lang"], output_path=row["output_path"],
        target_language=row["target_language"],
    )


def _correction_from_row(row: sqlite3.Row) -> Correction:
    return Correction(
        id=row["id"], unit_id=row["unit_id"], target_lang=row["target_lang"], kind=row["kind"],
        payload=json.loads(row["payload_json"]), created_at=row["created_at"], active=bool(row["active"]),
    )


def _facts_from_row(row: sqlite3.Row) -> StoredFacts:
    return StoredFacts(
        unit_id=row["unit_id"], source_sha256=row["source_sha256"], prompt_version=row["prompt_version"],
        model=row["model"], facts=Facts.from_dict(json.loads(row["json"])), facts_hash=row["facts_hash"],
        tokens_in=row["tokens_in"], tokens_out=row["tokens_out"], created_at=row["created_at"],
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
        self._restrict_files()

    def close(self) -> None:
        with self._lock:
            self._db.close()

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

    # -- auth --------------------------------------------------------------
    def auth_account(self) -> Account | None:
        with self._lock:
            row = self._db.execute("SELECT user, password_hash FROM auth WHERE id = 1").fetchone()
        return Account(user=row["user"], password_hash=row["password_hash"]) if row else None

    def save_auth_account(self, user: str, password_hash: str) -> None:
        """Create or overwrite the account. A fresh session secret goes with it, so every session ends."""
        with self._lock:
            self._db.execute(
                "INSERT INTO auth (id, user, password_hash, session_secret, updated_at) VALUES (1, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET user = excluded.user,"
                " password_hash = excluded.password_hash, session_secret = excluded.session_secret,"
                " updated_at = excluded.updated_at",
                (user, password_hash, secrets.token_hex(32), time.time()),
            )

    def auth_session_secret(self) -> str | None:
        """Hex session secret of the account; None without an account (a row from before schema 5 gets one)."""
        with self._lock:
            row = self._db.execute("SELECT session_secret FROM auth WHERE id = 1").fetchone()
            if row is None:
                return None
            if not row["session_secret"]:
                self._db.execute("UPDATE auth SET session_secret = ? WHERE id = 1", (secrets.token_hex(32),))
                row = self._db.execute("SELECT session_secret FROM auth WHERE id = 1").fetchone()
            return row["session_secret"]

    # -- settings and API keys (P6) -----------------------------------------
    def settings_rows(self) -> list[SettingRow]:
        with self._lock:
            rows = self._db.execute("SELECT key, value_json, updated_at FROM settings ORDER BY key").fetchall()
        return [SettingRow(key=r["key"], value=json.loads(r["value_json"]), updated_at=r["updated_at"]) for r in rows]

    def setting_save(self, values: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            self._db.execute("BEGIN")
            try:
                for key, value in values.items():
                    self._db.execute(
                        "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,"
                        " updated_at = excluded.updated_at",
                        (key, json.dumps(value, ensure_ascii=False), now),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def api_keys(self, provider: str | None = None) -> list[ApiKey]:
        with self._lock:
            if provider is None:
                rows = self._db.execute("SELECT * FROM api_keys ORDER BY id").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM api_keys WHERE provider = ? ORDER BY id", (provider,)).fetchall()
        return [_key_from_row(row) for row in rows]

    def api_key_add(self, provider: str, label: str, value: str, status: str = "ok") -> ApiKey:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO api_keys (provider, label, value, status, updated_at) VALUES (?, ?, ?, ?, ?)",
                (provider, label, value, status, time.time()),
            )
            row = self._db.execute("SELECT * FROM api_keys WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _key_from_row(row)

    def api_key_update(
        self, key_id: int, *, label: str | None = None, value: str | None = None, status: str | None = None,
    ) -> ApiKey | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
            if row is None:
                return None
            if value is not None and value != row["value"] and status is None:
                status = "ok"  # a new key has not been refused yet
            self._db.execute(
                "UPDATE api_keys SET label = ?, value = ?, status = ?, updated_at = ? WHERE id = ?",
                (row["label"] if label is None else label, row["value"] if value is None else value,
                 row["status"] if status is None else status, time.time(), key_id),
            )
            row = self._db.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        return _key_from_row(row)

    def api_key_delete(self, key_id: int) -> bool:
        with self._lock:
            return self._db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,)).rowcount > 0

    def api_key_touch(self, key_id: int, at: float | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (at or time.time(), key_id))

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
        kind: str = "",
        prompt_version: str = "",
        bundle_hash: str = "",
        style_hash: str = "",
        glossary_hash: str = "",
        batch: int = 0,
        http_status: int | None = None,
        finish_reason: str = "",
        retries: int = 0,
        repairs: int = 0,
        cost_est: float = 0.0,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO llm_calls (job_id, provider, model, task, tokens_in, tokens_out, latency_ms, outcome, at,"
                " kind, prompt_version, bundle_hash, style_hash, glossary_hash, batch, http_status, finish_reason,"
                " retries, repairs, cost_est)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, provider, model, task, tokens_in, tokens_out, latency_ms, outcome, at or time.time(),
                 kind, prompt_version, bundle_hash, style_hash, glossary_hash, batch, http_status, finish_reason,
                 retries, repairs, cost_est),
            )
            return int(cursor.lastrowid)

    def llm_calls(self, job_id: str | None = None) -> list[LlmCall]:
        with self._lock:
            if job_id is None:
                rows = self._db.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM llm_calls WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return [
            LlmCall(
                id=row["id"], job_id=row["job_id"], provider=row["provider"], model=row["model"], task=row["task"],
                tokens_in=row["tokens_in"], tokens_out=row["tokens_out"], latency_ms=row["latency_ms"],
                outcome=row["outcome"], at=row["at"], kind=row["kind"], prompt_version=row["prompt_version"],
                bundle_hash=row["bundle_hash"], style_hash=row["style_hash"], glossary_hash=row["glossary_hash"],
                batch=row["batch"], http_status=row["http_status"], finish_reason=row["finish_reason"],
                retries=row["retries"], repairs=row["repairs"], cost_est=row["cost_est"],
            )
            for row in rows
        ]

    # -- events, quality signals, body index (P5, ADR-15) ----------------------------
    def add_event(
        self, kind: str, *, level: str = "INFO", job_id: str = "", unit_id: int | None = None, reason: str = "",
        data: dict[str, Any] | None = None, at: float | None = None,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events (at, level, kind, job_id, unit_id, reason, data_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at or time.time(), level, kind, job_id, unit_id, reason,
                 json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str)),
            )
            return int(cursor.lastrowid)

    def events(
        self, *, job_id: str | None = None, unit_id: int | None = None, kind: str | None = None,
        since: float | None = None, before_id: int | None = None, limit: int = 100,
    ) -> list[EventRecord]:
        clauses: list[str] = []
        args: list[Any] = []
        if job_id:
            clauses.append("job_id = ?")
            args.append(job_id)
        if unit_id is not None:
            clauses.append("unit_id = ?")
            args.append(unit_id)
        if kind:
            if kind.endswith("."):  # a family: "job." matches every job.* event
                clauses.append("kind LIKE ? ESCAPE '\\'")
                args.append(kind.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            else:
                clauses.append("kind = ?")
                args.append(kind)
        if since is not None:
            clauses.append("at >= ?")
            args.append(since)
        if before_id is not None:
            clauses.append("id < ?")
            args.append(before_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ?", (*args, max(1, min(int(limit), 1000)))
            ).fetchall()
        return [
            EventRecord(
                id=row["id"], at=row["at"], level=row["level"], kind=row["kind"], job_id=row["job_id"],
                unit_id=row["unit_id"], reason=row["reason"], data=json.loads(row["data_json"] or "{}"),
            )
            for row in rows
        ]

    def prune_events(self, before: float) -> int:
        with self._lock:
            return self._db.execute("DELETE FROM events WHERE at < ?", (before,)).rowcount

    def add_signal(
        self, kind: str, *, unit_id: int | None = None, version: int | None = None, cue: int | None = None,
        before: str = "", after: str = "", meta: dict[str, Any] | None = None, at: float | None = None,
    ) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO quality_signals (at, kind, unit_id, version, cue, before_text, after_text, meta_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (at or time.time(), kind, unit_id, version, cue, before, after,
                 json.dumps(meta or {}, ensure_ascii=False, sort_keys=True, default=str)),
            )
            return int(cursor.lastrowid)

    def signals(self, *, kind: str | None = None, unit_id: int | None = None, limit: int = 100) -> list[QualitySignal]:
        clauses: list[str] = []
        args: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        if unit_id is not None:
            clauses.append("unit_id = ?")
            args.append(unit_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM quality_signals{where} ORDER BY id DESC LIMIT ?", (*args, max(1, min(int(limit), 5000)))
            ).fetchall()
        return [
            QualitySignal(
                id=row["id"], at=row["at"], kind=row["kind"], unit_id=row["unit_id"], version=row["version"],
                cue=row["cue"], before=row["before_text"], after=row["after_text"],
                meta=json.loads(row["meta_json"] or "{}"),
            )
            for row in rows
        ]

    def add_body_ref(self, call_id: int, file: str, offset: int, length: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_bodies (call_id, file, byte_offset, byte_length, rotated) VALUES (?, ?, ?, ?, 0)",
                (call_id, file, offset, length),
            )

    def body_ref(self, call_id: int) -> BodyRef | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM llm_bodies WHERE call_id = ?", (call_id,)).fetchone()
        if row is None:
            return None
        return BodyRef(
            call_id=row["call_id"], file=row["file"], offset=row["byte_offset"], length=row["byte_length"],
            rotated=bool(row["rotated"]),
        )

    def mark_bodies_rotated(self, files: Iterable[str]) -> int:
        names = list(files)
        if not names:
            return 0
        with self._lock:
            total = 0
            for name in names:
                total += self._db.execute("UPDATE llm_bodies SET rotated = 1 WHERE file = ? AND rotated = 0", (name,)).rowcount
            return total

    def token_totals(self, since: float = 0.0) -> TokenTotals:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens_in), 0) AS tokens_in,"
                " COALESCE(SUM(tokens_out), 0) AS tokens_out FROM llm_calls WHERE at >= ?",
                (since,),
            ).fetchone()
        return TokenTotals(calls=int(row["calls"]), tokens_in=int(row["tokens_in"]), tokens_out=int(row["tokens_out"]))

    # -- translations ------------------------------------------------------
    def current_version(self, output_path: str) -> TranslationVersion | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM translations WHERE output_path = ? AND status = 'current'", (output_path,)
            ).fetchone()
        return _version_from_row(row) if row else None

    def versions(self, output_path: str) -> list[TranslationVersion]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM translations WHERE output_path = ? ORDER BY version", (output_path,)
            ).fetchall()
        return [_version_from_row(row) for row in rows]

    def get_version(self, output_path: str, version: int) -> TranslationVersion | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM translations WHERE output_path = ? AND version = ?", (output_path, version)
            ).fetchone()
        return _version_from_row(row) if row else None

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
        unit_id: int | None = None,
        glossary_hash: str = "",
        context_complete: bool = True,
        memory_hash: str = "",
        missing_units: Iterable[str] = (),
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
                    " tokens_out, created_at, unit_id, glossary_hash, context_complete, memory_hash,"
                    " missing_units_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        output_path, source_path, source_sha256, target_lang, version, stored_path, sha256,
                        context_hash, trigger, parent_version, "current" if make_current else "superseded",
                        model, tokens_in, tokens_out, time.time(), unit_id, glossary_hash, int(context_complete),
                        memory_hash, json.dumps(list(missing_units), ensure_ascii=False),
                    ),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    # -- series and units (P3) ---------------------------------------------
    def upsert_series(self, key: str, *, title: str = "", bazarr_id: str = "") -> int:
        with self._lock:
            row = self._db.execute("SELECT * FROM series WHERE key = ?", (key,)).fetchone()
            if row is None:
                cursor = self._db.execute(
                    "INSERT INTO series (key, title, bazarr_id, created_at) VALUES (?, ?, ?, ?)",
                    (key, title, bazarr_id, time.time()),
                )
                return int(cursor.lastrowid)
            if (title and title != row["title"]) or (bazarr_id and bazarr_id != row["bazarr_id"]):
                self._db.execute(
                    "UPDATE series SET title = ?, bazarr_id = ? WHERE id = ?",
                    (title or row["title"], bazarr_id or row["bazarr_id"], row["id"]),
                )
            return int(row["id"])

    def get_series(self, series_id: int) -> Series | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
        return _series_from_row(row) if row else None

    def list_series(self) -> list[Series]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM series ORDER BY title COLLATE NOCASE, id").fetchall()
        return [_series_from_row(row) for row in rows]

    def upsert_unit(
        self,
        *,
        series_id: int | None = None,
        movie_key: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        title: str = "",
        media_type: str = "",
        bazarr_id: str = "",
        video_path: str = "",
        source_path: str = "",
        source_code: str = "",
        source_language: str = "",
    ) -> int:
        """Insert or refresh a unit; identity is (series, season, episode) or the movie key."""
        if (series_id is None) == (movie_key is None):
            raise ValueError("a unit is either an episode of a series or a movie")
        fields = dict(
            title=title, media_type=media_type, bazarr_id=bazarr_id, video_path=video_path,
            source_path=source_path, source_code=source_code, source_language=source_language,
        )
        with self._lock:
            if series_id is not None:
                row = self._db.execute(
                    "SELECT * FROM units WHERE series_id = ? AND season = ? AND episode = ?",
                    (series_id, season, episode),
                ).fetchone()
            else:
                row = self._db.execute("SELECT * FROM units WHERE movie_key = ?", (movie_key,)).fetchone()
            now = time.time()
            if row is None:
                cursor = self._db.execute(
                    "INSERT INTO units (series_id, movie_key, season, episode, title, media_type, bazarr_id,"
                    " video_path, source_path, source_code, source_language, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (series_id, movie_key, season, episode, *fields.values(), now, now),
                )
                return int(cursor.lastrowid)
            merged = {k: (v or row[k]) for k, v in fields.items()}  # blanks never erase known facts
            if any(merged[k] != row[k] for k in merged):
                assignments = ", ".join(f"{k} = ?" for k in merged)
                self._db.execute(
                    f"UPDATE units SET {assignments}, updated_at = ? WHERE id = ?", (*merged.values(), now, row["id"])
                )
            return int(row["id"])

    def get_unit(self, unit_id: int) -> Unit | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
        return _unit_from_row(row) if row else None

    def list_units(self, series_id: int | None = None, *, movies: bool = False) -> list[Unit]:
        with self._lock:
            if movies:
                rows = self._db.execute(
                    "SELECT * FROM units WHERE series_id IS NULL ORDER BY title COLLATE NOCASE, id"
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM units WHERE series_id = ? ORDER BY season, episode, id", (series_id,)
                ).fetchall()
        return [_unit_from_row(row) for row in rows]

    def set_output(self, unit_id: int, target_lang: str, output_path: str, target_language: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO unit_outputs (unit_id, target_lang, target_language, output_path) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (unit_id, target_lang) DO UPDATE SET output_path = excluded.output_path,"
                " target_language = CASE WHEN excluded.target_language <> '' THEN excluded.target_language"
                " ELSE unit_outputs.target_language END",
                (unit_id, target_lang, target_language, output_path),
            )

    def outputs(self, unit_id: int) -> list[Output]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM unit_outputs WHERE unit_id = ? ORDER BY target_lang", (unit_id,)
            ).fetchall()
        return [_output_from_row(row) for row in rows]

    # -- corrections (episode scope; wider scopes live in the glossary YAML) -----
    def add_correction(self, unit_id: int, target_lang: str, kind: str, payload: dict[str, Any]) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO corrections (unit_id, target_lang, kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (unit_id, target_lang, kind, json.dumps(payload, ensure_ascii=False, sort_keys=True), time.time()),
            )
            return int(cursor.lastrowid)

    def corrections(self, unit_id: int, target_lang: str, *, active_only: bool = True) -> list[Correction]:
        query = "SELECT * FROM corrections WHERE unit_id = ? AND target_lang = ?"
        if active_only:
            query += " AND active = 1"
        with self._lock:
            rows = self._db.execute(query + " ORDER BY id", (unit_id, target_lang)).fetchall()
        return [_correction_from_row(row) for row in rows]

    def get_correction(self, correction_id: int) -> Correction | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM corrections WHERE id = ?", (correction_id,)).fetchone()
        return _correction_from_row(row) if row else None

    def deactivate_correction(self, correction_id: int) -> bool:
        with self._lock:
            cursor = self._db.execute("UPDATE corrections SET active = 0 WHERE id = ? AND active = 1", (correction_id,))
            return cursor.rowcount > 0

    # -- story memory (P4) ------------------------------------------------------------
    def find_unit(self, series_id: int, season: int, episode: int) -> Unit | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM units WHERE series_id = ? AND season = ? AND episode = ?", (series_id, season, episode)
            ).fetchone()
        return _unit_from_row(row) if row else None

    def set_series_memory(self, series_id: int, *, memory: str | None = None, specials_after: str | None = None) -> None:
        with self._lock:
            if memory is not None:
                self._db.execute("UPDATE series SET memory = ? WHERE id = ?", (memory, series_id))
            if specials_after is not None:
                self._db.execute("UPDATE series SET specials_after = ? WHERE id = ?", (specials_after, series_id))

    def get_facts(
        self, unit_id: int, source_sha256: str, prompt_version: str, *, touch: bool = False
    ) -> StoredFacts | None:
        """Stored facts for exactly this source and prompt version. ``touch`` marks them as the newest in use."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM facts WHERE unit_id = ? AND source_sha256 = ? AND prompt_version = ?",
                (unit_id, source_sha256, prompt_version),
            ).fetchone()
            if row is not None and touch:
                self._db.execute("UPDATE facts SET created_at = ? WHERE id = ?", (time.time(), row["id"]))
        return _facts_from_row(row) if row else None

    def save_facts(
        self, unit_id: int, source_sha256: str, prompt_version: str, *, model: str, facts: Facts,
        tokens_in: int = 0, tokens_out: int = 0,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO facts (unit_id, source_sha256, prompt_version, model, json, facts_hash, tokens_in,"
                " tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (unit_id, source_sha256, prompt_version) DO UPDATE SET model = excluded.model,"
                " json = excluded.json, facts_hash = excluded.facts_hash, tokens_in = excluded.tokens_in,"
                " tokens_out = excluded.tokens_out, created_at = excluded.created_at",
                (unit_id, source_sha256, prompt_version, model, json.dumps(facts.to_dict(), ensure_ascii=False, sort_keys=True),
                 facts.hash, tokens_in, tokens_out, time.time()),
            )

    def latest_facts(self, unit_ids: Iterable[int], prompt_version: str) -> dict[int, StoredFacts]:
        """The facts most recently saved or used for each unit under ``prompt_version``."""
        ids = list(unit_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM facts WHERE prompt_version = ? AND unit_id IN ({marks}) ORDER BY created_at, id",
                (prompt_version, *ids),
            ).fetchall()
        return {int(row["unit_id"]): _facts_from_row(row) for row in rows}  # later rows overwrite earlier ones

    def get_summary(self, input_hash: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT text FROM summaries_cache WHERE input_hash = ?", (input_hash,)).fetchone()
        return str(row["text"]) if row else None

    def put_summary(self, series_id: int, up_to_unit: int, input_hash: str, text: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO summaries_cache (series_id, up_to_unit, input_hash, text, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (series_id, up_to_unit, input_hash, text, time.time()),
            )

    def put_snapshot(self, memory_hash: str, sections: Iterable[tuple[str, str]]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO context_snapshots (memory_hash, sections_json, created_at) VALUES (?, ?, ?)",
                (memory_hash, json.dumps([list(s) for s in sections], ensure_ascii=False), time.time()),
            )

    def get_snapshot(self, memory_hash: str) -> list[tuple[str, str]] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT sections_json FROM context_snapshots WHERE memory_hash = ?", (memory_hash,)
            ).fetchone()
        return [(str(a), str(b)) for a, b in json.loads(row["sections_json"])] if row else None

    def cached_versions(self, source_sha256: str, target_lang: str, context_hash: str) -> list[TranslationVersion]:
        """Versions made from the same input and context, newest first (NFR-3)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM translations WHERE source_sha256 = ? AND target_lang = ? AND context_hash = ?"
                " AND trigger IN ('auto', 'retranslate') ORDER BY id DESC",
                (source_sha256, target_lang, context_hash),
            ).fetchall()
        return [_version_from_row(row) for row in rows]
