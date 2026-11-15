"""SQLite store for machine-owned state (ADR-5): subtitle versions, LLM call records, and the library.

The schema is a subset of docs/design/ARCHITECTURE.md section 4. ``translations`` is still keyed by
``output_path`` (one history per target file) and carries a nullable ``unit_id`` linking it to the
library (``series`` / ``units``, with ``unit_outputs`` mapping a unit and target language to its file).
Jobs and provider health live here too (ADR-11, schema 8); version history carries origin, candidates and removals (FR-16, schema 9).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from subcore.memory import Facts

from dataclasses import asdict

from .ports import (
    Account, ApiKey, SettingRow, BodyRef, CatalogApplied, CatalogEntry, CatalogState, CatalogSubtitle, Correction,
    EventRecord, LlmCall, Output, QualitySignal, Series, StoredFacts, TokenTotals, TranslationVersion, Unit,
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
    # Schema 8 (P8, ADR-11): jobs and provider health. A job is a row, not a file. ``job_id`` is the public id
    # (the old sha1 for the first job of a file, so imported and script jobs keep theirs); ``key`` is that sha1,
    # the thing at most one open job may hold per ``kind`` (FR-15.3). ``payload_json`` is the job as the worker
    # passes it around (paths, languages, retranslate range, ...); the columns are what the scheduler queries.
    # ``prio`` is the class (interactive 200, retranslate 100, auto 0); ``tier`` is '' (any model) or 'strong'.
    # ``lease_until`` is on the monotonic clock of the running process and means nothing after a restart, which
    # recovers every running row at startup; ``not_before`` is wall-clock time.
    """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL UNIQUE,
        key TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'publish',
        mode TEXT NOT NULL DEFAULT 'fill',
        origin TEXT NOT NULL DEFAULT 'manual',
        prio INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        wait_reason TEXT NOT NULL DEFAULT '',
        series_key TEXT NOT NULL DEFAULT '',
        unit_id INTEGER,
        target_lang TEXT NOT NULL DEFAULT '',
        tier TEXT NOT NULL DEFAULT '',
        not_before REAL NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_until REAL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        base_version INTEGER,
        source_sha256 TEXT NOT NULL DEFAULT '',
        snapshot_json TEXT NOT NULL DEFAULT '{}',
        payload_json TEXT NOT NULL DEFAULT '{}',
        result TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL
    );
    CREATE UNIQUE INDEX jobs_open ON jobs (key, kind) WHERE state IN ('queued', 'running', 'waiting', 'held');
    CREATE INDEX jobs_state ON jobs (state, not_before);
    CREATE INDEX jobs_series ON jobs (series_key, started_at);
    CREATE INDEX jobs_key ON jobs (key);
    CREATE TABLE provider_health (
        provider TEXT NOT NULL,
        model TEXT NOT NULL,              -- '*' is the provider as a whole (a refused key)
        state TEXT NOT NULL DEFAULT 'closed',   -- closed | open | half_open | exhausted | invalid
        failures INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL DEFAULT 0,
        until REAL NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL,
        PRIMARY KEY (provider, model)
    );
    """,
    # Schema 9 (P8, FR-16, ADR-11): versions carry an origin (machine | edit | revert | external | import), may be
    # candidate / removed / pruned, and a publish is announced (publishing = 1) before the file is renamed into
    # place, so a crash in between is settled at start-up. edited_at is the last cue edit folded into a version
    # (FR-16.7). At most one current version per file.
    """
    ALTER TABLE translations ADD COLUMN origin TEXT NOT NULL DEFAULT '';
    ALTER TABLE translations ADD COLUMN publishing INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE translations ADD COLUMN edited_at REAL NOT NULL DEFAULT 0;
    ALTER TABLE translations ADD COLUMN job_id TEXT NOT NULL DEFAULT '';
    UPDATE translations SET origin = CASE trigger
        WHEN 'cue_edit' THEN 'edit' WHEN 'revert' THEN 'revert' WHEN 'external' THEN 'external' ELSE 'machine' END;
    CREATE UNIQUE INDEX translations_one_current ON translations (output_path) WHERE status = 'current';
    """,
    # Schema 10 (P10, FR-14, FR-15.4, FR-15.6, ADR-10): the library as Bazarr lists it (``catalog_items``, a cache
    # with an age), what the last attempts to read it did (``catalog_state``, one row), the admin's "do not
    # translate this" (``unit_decisions``) and the per-series override of ``auto_request``. An item is identified by
    # (kind, bazarr_id), never by a path; ``gone_at`` is set when a full, plausible read no longer lists it. The
    # ``held`` state is gone from the queue: the index of open jobs forgets it.
    """
    CREATE TABLE catalog_items (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,                  -- series | movie | episode
        bazarr_id TEXT NOT NULL,
        parent_id INTEGER,                   -- the series row an episode belongs to
        series_key TEXT NOT NULL DEFAULT '',
        season INTEGER,
        episode INTEGER,
        title TEXT NOT NULL DEFAULT '',
        video_path TEXT NOT NULL DEFAULT '',
        tvdb_id TEXT NOT NULL DEFAULT '',
        subtitles_json TEXT NOT NULL DEFAULT '[]',
        missing_json TEXT NOT NULL DEFAULT '[]',
        synced_at REAL NOT NULL,
        gone_at REAL,
        UNIQUE (kind, bazarr_id)
    );
    CREATE INDEX catalog_parent ON catalog_items (parent_id);
    CREATE TABLE catalog_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        synced_at REAL NOT NULL DEFAULT 0,
        attempted_at REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT '',
        warning TEXT NOT NULL DEFAULT '',
        items INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE unit_decisions (
        unit_id INTEGER NOT NULL REFERENCES units(id),
        target_lang TEXT NOT NULL,
        decision TEXT NOT NULL,              -- ignored
        at REAL NOT NULL,
        PRIMARY KEY (unit_id, target_lang)
    );
    ALTER TABLE series ADD COLUMN auto_request TEXT NOT NULL DEFAULT '';
    DROP INDEX jobs_open;
    CREATE UNIQUE INDEX jobs_open ON jobs (key, kind) WHERE state IN ('queued', 'running', 'waiting');
    CREATE INDEX jobs_unit ON jobs (unit_id, target_lang);
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
        origin=row["origin"], publishing=bool(row["publishing"]), edited_at=row["edited_at"], job_id=row["job_id"],
    )


def _origin_of(trigger: str) -> str:
    """Where a version came from (FR-16.1): the machine unless the trigger says the admin or a file did it."""
    return {"cue_edit": "edit", "remove": "edit", "revert": "revert", "external": "external"}.get(trigger, "machine")


def _key_from_row(row: sqlite3.Row) -> ApiKey:
    return ApiKey(
        id=row["id"], provider=row["provider"], label=row["label"], value=row["value"], status=row["status"],
        last_used_at=row["last_used_at"], updated_at=row["updated_at"],
    )


def _series_from_row(row: sqlite3.Row) -> Series:
    return Series(
        id=row["id"], key=row["key"], title=row["title"], bazarr_id=row["bazarr_id"],
        stale_policy=row["stale_policy"], memory=row["memory"], specials_after=row["specials_after"],
        created_at=row["created_at"], auto_request=row["auto_request"],
    )


def _catalog_from_row(row: sqlite3.Row) -> CatalogEntry:
    return CatalogEntry(
        kind=row["kind"], bazarr_id=row["bazarr_id"], series_key=row["series_key"], season=row["season"],
        episode=row["episode"], title=row["title"], video_path=row["video_path"], tvdb_id=row["tvdb_id"],
        subtitles=tuple(CatalogSubtitle(**item) for item in json.loads(row["subtitles_json"] or "[]")),
        missing=tuple(json.loads(row["missing_json"] or "[]")), id=row["id"], parent_id=row["parent_id"],
        synced_at=row["synced_at"], gone_at=row["gone_at"],
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
        self._db.execute("PRAGMA busy_timeout=10000")  # a second connection (a --once run) waits, it does not fail
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._migrate()
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
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

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

    def calls_since(self, at: float) -> int:
        """Model calls recorded since ``at`` (a Unix time): what `max_calls_per_day` counts (FR-17.6)."""
        return int(self.query("SELECT COUNT(*) AS n FROM llm_calls WHERE at >= ?", (at,))[0]["n"])

    def average_call_seconds(self, sample: int = 200) -> float | None:
        """How long a recent model call took, on average, in seconds; None before any call was recorded."""
        row = self.query(
            "SELECT AVG(latency_ms) AS ms, COUNT(*) AS n FROM (SELECT latency_ms FROM llm_calls"
            " WHERE latency_ms > 0 ORDER BY id DESC LIMIT ?)", (int(sample),),
        )[0]
        return float(row["ms"]) / 1000.0 if row["n"] else None

    def policy_jobs_since(self, at: float) -> int:
        """Jobs the ``auto_request`` policy created since ``at``: what ``auto_request_daily_cap`` counts (FR-15.4)."""
        return int(self.query(
            "SELECT COUNT(*) AS n FROM jobs WHERE origin = 'policy' AND created_at >= ?", (at,)
        )[0]["n"])

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

    def latest_version_status(self, output_path: str) -> str:
        """Status of the file's newest version, '' when it has none (one indexed row, not the whole history)."""
        with self._lock:
            row = self._db.execute(
                "SELECT status FROM translations WHERE output_path = ? ORDER BY version DESC LIMIT 1", (output_path,)
            ).fetchone()
        return str(row["status"]) if row else ""

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
        status: str | None = None,
        origin: str = "",
        publishing: bool = False,
        job_id: str = "",
        edited_at: float = 0.0,
    ) -> None:
        if status is None:
            status = "current" if make_current else "superseded"
        if not origin:
            origin = _origin_of(trigger)
        now = time.time()
        with self._lock:
            self._db.execute("BEGIN")
            try:
                if status in ("current", "removed"):  # one current; a tombstone is what the file was last
                    self._db.execute(
                        "UPDATE translations SET status = 'superseded' WHERE output_path = ? AND status = 'current'",
                        (output_path,),
                    )
                self._db.execute(
                    "INSERT INTO translations (output_path, source_path, source_sha256, target_lang, version,"
                    " stored_path, sha256, context_hash, trigger, parent_version, status, model, tokens_in,"
                    " tokens_out, created_at, unit_id, glossary_hash, context_complete, memory_hash,"
                    " missing_units_json, origin, publishing, job_id, edited_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        output_path, source_path, source_sha256, target_lang, version, stored_path, sha256,
                        context_hash, trigger, parent_version, status,
                        model, tokens_in, tokens_out, now, unit_id, glossary_hash, int(context_complete),
                        memory_hash, json.dumps(list(missing_units), ensure_ascii=False), origin, int(publishing),
                        job_id, edited_at,
                    ),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    _VERSION_FIELDS = frozenset({"status", "publishing", "stored_path", "sha256", "edited_at"})

    def set_version(self, output_path: str, version: int, **fields: Any) -> None:
        unknown = set(fields) - self._VERSION_FIELDS
        if unknown:
            raise ValueError(f"not a version field: {', '.join(sorted(unknown))}")
        if "publishing" in fields:
            fields["publishing"] = int(bool(fields["publishing"]))
        with self._lock:
            self._db.execute("BEGIN")
            try:
                if fields.get("status") == "current":
                    self._db.execute(
                        "UPDATE translations SET status = 'superseded' WHERE output_path = ? AND status = 'current'"
                        " AND version <> ?",
                        (output_path, version),
                    )
                if fields:
                    assignments = ", ".join(f"{name} = ?" for name in fields)
                    self._db.execute(
                        f"UPDATE translations SET {assignments} WHERE output_path = ? AND version = ?",
                        (*fields.values(), output_path, version),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def publishing_versions(self) -> list[TranslationVersion]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM translations WHERE publishing = 1 ORDER BY id").fetchall()
        return [_version_from_row(row) for row in rows]

    def current_versions(self) -> list[TranslationVersion]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM translations WHERE status = 'current' ORDER BY output_path").fetchall()
        return [_version_from_row(row) for row in rows]

    def prunable_versions(self, keep: int) -> list[TranslationVersion]:
        """Versions whose stored copy retention may delete (FR-13.1, FR-13.2): not the current one, not the first of
        its file, not one of the ``keep`` newest, not one still being published, and one that still has a copy."""
        with self._lock:
            rows = self._db.execute(
                "SELECT t.* FROM translations t WHERE t.status NOT IN ('current', 'pruned') AND t.publishing = 0"
                " AND t.stored_path <> ''"
                " AND t.version > (SELECT MIN(f.version) FROM translations f WHERE f.output_path = t.output_path)"
                " AND t.version <= (SELECT MAX(f.version) FROM translations f WHERE f.output_path = t.output_path) - ?"
                " ORDER BY t.output_path, t.version",
                (max(0, int(keep)),),
            ).fetchall()
        return [_version_from_row(row) for row in rows]

    def output_paths(self) -> list[str]:
        """Every target file that has a history."""
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT output_path FROM translations ORDER BY output_path").fetchall()
        return [str(row["output_path"]) for row in rows]

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

    def set_series_auto_request(self, series_id: int, value: str) -> None:
        """'' follows the global ``auto_request`` setting; 'on' and 'off' override it for this series (FR-15.4)."""
        with self._lock:
            self._db.execute("UPDATE series SET auto_request = ? WHERE id = ?", (value, series_id))

    def series_by_key(self, key: str) -> Series | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM series WHERE key = ?", (key,)).fetchone()
        return _series_from_row(row) if row else None

    def unit_by_movie_key(self, movie_key: str) -> Unit | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM units WHERE movie_key = ?", (movie_key,)).fetchone()
        return _unit_from_row(row) if row else None

    # -- the library as Bazarr lists it (P10, FR-14, ADR-10) --------------------------------
    def catalog_apply(self, entries: Iterable[CatalogEntry], at: float) -> CatalogApplied:
        """Make the cache say what one full, accepted read said: every entry is written (new or refreshed, no longer
        gone) and every row the read did not list is marked gone (FR-14.2). One transaction: a reader sees the
        old catalog or the new one, never half."""
        added = updated = 0
        with self.transaction() as db:
            parents: dict[str, int] = {}
            for entry in sorted(entries, key=lambda e: e.kind != "series"):  # a series row before its episodes
                parent_id = parents.get(entry.parent_bazarr_id) if entry.parent_bazarr_id else None
                if entry.parent_bazarr_id and parent_id is None:
                    found = db.execute(
                        "SELECT id FROM catalog_items WHERE kind = 'series' AND bazarr_id = ?", (entry.parent_bazarr_id,)
                    ).fetchone()
                    parent_id = int(found["id"]) if found else None
                fields = (
                    parent_id, entry.series_key, entry.season, entry.episode, entry.title, entry.video_path,
                    entry.tvdb_id, json.dumps([asdict(s) for s in entry.subtitles], ensure_ascii=False),
                    json.dumps(list(entry.missing)), at,
                )
                row = db.execute(
                    "SELECT id FROM catalog_items WHERE kind = ? AND bazarr_id = ?", (entry.kind, entry.bazarr_id)
                ).fetchone()
                if row is None:
                    cursor = db.execute(
                        "INSERT INTO catalog_items (kind, bazarr_id, parent_id, series_key, season, episode, title,"
                        " video_path, tvdb_id, subtitles_json, missing_json, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (entry.kind, entry.bazarr_id, *fields),
                    )
                    item_id, added = int(cursor.lastrowid), added + 1
                else:
                    item_id, updated = int(row["id"]), updated + 1
                    db.execute(
                        "UPDATE catalog_items SET parent_id = ?, series_key = ?, season = ?, episode = ?, title = ?,"
                        " video_path = ?, tvdb_id = ?, subtitles_json = ?, missing_json = ?, synced_at = ?, gone_at = NULL"
                        " WHERE id = ?", (*fields, item_id),
                    )
                if entry.kind == "series":
                    parents[entry.bazarr_id] = item_id
            gone = db.execute(
                "UPDATE catalog_items SET gone_at = ? WHERE synced_at < ? AND gone_at IS NULL", (at, at)
            ).rowcount
        return CatalogApplied(added=added, updated=updated, gone=gone)

    def catalog_entry(self, item_id: int) -> CatalogEntry | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM catalog_items WHERE id = ?", (item_id,)).fetchone()
        return _catalog_from_row(row) if row else None

    def catalog_lookup(self, kind: str, bazarr_id: str) -> CatalogEntry | None:
        """The item with the library's own id; the way a job finds its files again after a rename (FR-14.3)."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM catalog_items WHERE kind = ? AND bazarr_id = ?", (kind, str(bazarr_id))
            ).fetchone()
        return _catalog_from_row(row) if row else None

    def catalog_entries(self, kind: str | None = None, *, parent_id: int | None = None, include_gone: bool = False) -> list[CatalogEntry]:
        sql, args = "SELECT * FROM catalog_items WHERE 1 = 1", []
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        if parent_id is not None:
            sql += " AND parent_id = ?"
            args.append(parent_id)
        if not include_gone:
            sql += " AND gone_at IS NULL"
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY title COLLATE NOCASE, season, episode, id", args).fetchall()
        return [_catalog_from_row(row) for row in rows]

    def catalog_count(self) -> int:
        """Items that are still listed (not gone)."""
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM catalog_items WHERE gone_at IS NULL").fetchone()[0])

    def catalog_state(self) -> CatalogState:
        with self._lock:
            row = self._db.execute("SELECT * FROM catalog_state WHERE id = 1").fetchone()
        if row is None:
            return CatalogState()
        return CatalogState(synced_at=row["synced_at"], attempted_at=row["attempted_at"], status=row["status"],
                            warning=row["warning"], items=row["items"])

    def save_catalog_state(self, state: CatalogState) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO catalog_state (id, synced_at, attempted_at, status, warning, items) VALUES (1, ?, ?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET synced_at = excluded.synced_at, attempted_at = excluded.attempted_at,"
                " status = excluded.status, warning = excluded.warning, items = excluded.items",
                (state.synced_at, state.attempted_at, state.status, state.warning, state.items),
            )

    # -- the admin's decisions (FR-15.6) -------------------------------------------------------
    def set_decision(self, unit_id: int, target_lang: str, decision: str | None, at: float | None = None) -> None:
        """Record "ignored" for (unit, language), or forget the decision when ``decision`` is None."""
        with self._lock:
            if decision is None:
                self._db.execute("DELETE FROM unit_decisions WHERE unit_id = ? AND target_lang = ?", (unit_id, target_lang))
            else:
                self._db.execute(
                    "INSERT INTO unit_decisions (unit_id, target_lang, decision, at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (unit_id, target_lang) DO UPDATE SET decision = excluded.decision, at = excluded.at",
                    (unit_id, target_lang, decision, at if at is not None else time.time()),
                )

    def decisions(self, decision: str = "ignored") -> set[tuple[int, str]]:
        """Every (unit id, language) the admin decided about in this way."""
        with self._lock:
            rows = self._db.execute(
                "SELECT unit_id, target_lang FROM unit_decisions WHERE decision = ?", (decision,)
            ).fetchall()
        return {(int(row["unit_id"]), str(row["target_lang"])) for row in rows}

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
                " AND trigger IN ('auto', 'retranslate') AND status <> 'pruned' ORDER BY id DESC",
                (source_sha256, target_lang, context_hash),
            ).fetchall()
        return [_version_from_row(row) for row in rows]
