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
    JobBundle,
    Account, ApiKey, SettingRow, BodyRef, CallDay, CardRow, JobPrompt, MetricRow, CatalogApplied, CatalogEntry, CatalogState, CatalogSubtitle,
    Correction, CueEdit, CuePair, EventRecord, GlossaryRevision, LlmCall, Output, QualitySignal, Reclaimed, Series, StoredFacts,
    SeriesSeed, StoredGlossary, StyleRow, Suggestion, SuggestionCount, TokenTotals, TranslationVersion, Unit,
    SUGGESTION_ACCEPTED, SUGGESTION_PENDING, SUGGESTION_REJECTED,
)
from .suggestion_keys import identity_key

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
    # Migration 11 (P11, FR-21.2): the bundle of a job's latest attempt. A table of its own, because a bundle is
    # hundreds of KB and many queue queries read whole `jobs` rows. The job's deletes sweep it (see JobStore).
    """
    CREATE TABLE job_bundles (
        job_id TEXT PRIMARY KEY,
        bundle_hash TEXT NOT NULL,
        bundle_json TEXT NOT NULL
    );
    """,
    # Migration 12 (P12, FR-18, ADR-14): glossaries, style profiles and character cards in the database. An owner is
    # a series or a movie, named by (owner_kind, owner_key) like the folders the YAML used to live in (``series/<key>``,
    # ``movies/<key>``), so a movie has a glossary too and no row needs a series id. ``glossary_history`` keeps what
    # each save replaced (the old ``backups/glossary/``). ``style_profiles.scope`` is preset | series | unit.
    # ``translations.params_json`` says how a version was made when its other fields do not (a range, a variant).
    """
    ALTER TABLE translations ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}';
    CREATE TABLE glossaries (
        id INTEGER PRIMARY KEY,
        owner_kind TEXT NOT NULL,
        owner_key TEXT NOT NULL,
        lang TEXT NOT NULL,
        doc_json TEXT NOT NULL,
        rules_hash TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL,
        UNIQUE (owner_kind, owner_key, lang)
    );
    CREATE TABLE glossary_history (
        id INTEGER PRIMARY KEY,
        glossary_id INTEGER NOT NULL REFERENCES glossaries(id),
        version INTEGER NOT NULL,
        doc_json TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        at REAL NOT NULL,
        UNIQUE (glossary_id, version)
    );
    CREATE TABLE style_profiles (
        id INTEGER PRIMARY KEY,
        scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        doc_json TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL,
        UNIQUE (scope, scope_id)
    );
    CREATE TABLE character_cards (
        id INTEGER PRIMARY KEY,
        owner_kind TEXT NOT NULL,
        owner_key TEXT NOT NULL,
        name TEXT NOT NULL COLLATE NOCASE,
        doc_json TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL,
        UNIQUE (owner_kind, owner_key, name)
    );
    """,
    # Migration 13 (P13, FR-4.4, FR-11.1.5): what the generators propose, one table for every source. The
    # fingerprint is the hash of the normalised content, so the same content is never stored twice for a series
    # and language: a rejected suggestion stays as a row and is why it is not proposed again. ``applied_json``
    # is what accepting changed, kept so it can be undone.
    """
    CREATE TABLE suggestions (
        id INTEGER PRIMARY KEY,
        series_id INTEGER NOT NULL REFERENCES series(id),
        target_lang TEXT NOT NULL,
        kind TEXT NOT NULL,
        source TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        sources_json TEXT NOT NULL DEFAULT '[]',
        auto_accepted INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        applied_json TEXT NOT NULL DEFAULT '{}',
        decided_at REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE (series_id, target_lang, fingerprint)
    );
    CREATE INDEX suggestions_status ON suggestions (status, series_id);
    ALTER TABLE series ADD COLUMN bootstrap_released INTEGER NOT NULL DEFAULT 0;
    """,
    # Migration 14 (review wave W3, R13-6, R13-5): ``identity`` is what a suggestion is about (its kind, name or pair,
    # and where it starts), blind to the wording a model gave it; the start-up fills it for every older row, decided
    # ones included, so a rejection still holds. The fingerprint stays. ``suggestion_skips`` remembers a candidate the
    # model could not answer, for a while, so it does not head the list of every run.
    """
    ALTER TABLE suggestions ADD COLUMN identity TEXT NOT NULL DEFAULT '';
    CREATE INDEX suggestions_identity ON suggestions (series_id, target_lang, identity);
    CREATE TABLE suggestion_skips (
        series_id INTEGER NOT NULL REFERENCES series(id),
        target_lang TEXT NOT NULL,
        identity TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        at REAL NOT NULL,
        PRIMARY KEY (series_id, target_lang, identity)
    );
    """,
    # Migration 15 (P15, FR-19.5, ADR-15): ``metrics_daily`` keeps the figures of Insights per UTC day, split by labels
    # (model, prompt version...), so the long-term numbers outlive the events that are cleaned up. Rolled up from the
    # raw tables by ``Metrics.rollup``; the rows of day '0000-00-00' are its markers, not figures.
    """
    CREATE TABLE metrics_daily (
        day TEXT NOT NULL,
        name TEXT NOT NULL,
        labels_json TEXT NOT NULL DEFAULT '{}',
        value REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (day, name, labels_json)
    );
    CREATE INDEX quality_signals_at ON quality_signals (at);
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
        params=json.loads(row["params_json"] or "{}"),
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
        bootstrap_released=bool(row["bootstrap_released"]),
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


def _glossary_from_row(row: sqlite3.Row) -> StoredGlossary:
    return StoredGlossary(
        owner_kind=row["owner_kind"], owner_key=row["owner_key"], lang=row["lang"], doc=json.loads(row["doc_json"]),
        rules_hash=row["rules_hash"], version=row["version"], updated_at=row["updated_at"],
    )


def _style_from_row(row: sqlite3.Row) -> StyleRow:
    return StyleRow(
        scope=row["scope"], scope_id=row["scope_id"], doc=json.loads(row["doc_json"]), version=row["version"],
        updated_at=row["updated_at"],
    )


def _card_from_row(row: sqlite3.Row) -> CardRow:
    return CardRow(
        owner_kind=row["owner_kind"], owner_key=row["owner_key"], name=row["name"], doc=json.loads(row["doc_json"]),
        version=row["version"], updated_at=row["updated_at"],
    )


def _suggestion_from_row(row: sqlite3.Row) -> Suggestion:
    return Suggestion(
        id=row["id"], series_id=row["series_id"], target_lang=row["target_lang"], kind=row["kind"],
        source=row["source"], fingerprint=row["fingerprint"], payload=json.loads(row["payload_json"]),
        evidence=json.loads(row["evidence_json"] or "[]"), sources=json.loads(row["sources_json"] or "[]"),
        auto_accepted=bool(row["auto_accepted"]), status=row["status"], applied=json.loads(row["applied_json"] or "{}"),
        decided_at=row["decided_at"], created_at=row["created_at"], identity=row["identity"],
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

    # -- auth --------------------------------------------------------------
    def auth_account(self) -> Account | None:
        with self._lock:
            row = self._db.execute("SELECT user, password_hash FROM auth WHERE id = 1").fetchone()
        return Account(user=row["user"], password_hash=row["password_hash"]) if row else None

    # -- job bundles (P11, FR-21.2) ------------------------------------------------
    def save_job_bundle(self, job_id: str, bundle_hash: str, bundle_json: str) -> None:
        """Keep the bundle an attempt of this job translated from. One short write; the caller decides what a failure
        means (the translation itself does not depend on it)."""
        with self.transaction() as db:
            # Only for a job that has a row: a bundle without a job would never be swept.
            db.execute(
                "INSERT OR REPLACE INTO job_bundles (job_id, bundle_hash, bundle_json)"
                " SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM jobs WHERE job_id = ?)",
                (job_id, bundle_hash, bundle_json, job_id),
            )

    def job_bundle(self, job_id: str) -> JobBundle | None:
        rows = self.query("SELECT bundle_hash, bundle_json FROM job_bundles WHERE job_id = ?", (job_id,))
        if not rows:
            return None
        return JobBundle(job_id=job_id, bundle_hash=rows[0]["bundle_hash"], bundle_json=rows[0]["bundle_json"])

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

    def signals(
        self, *, kind: str | None = None, unit_id: int | None = None, since: float | None = None,
        before_id: int | None = None, limit: int = 100,
    ) -> list[QualitySignal]:
        """The newest signals first; ``before_id`` pages backwards the way ``events`` does."""
        clauses: list[str] = []
        args: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        if unit_id is not None:
            clauses.append("unit_id = ?")
            args.append(unit_id)
        if since is not None:
            clauses.append("at >= ?")
            args.append(since)
        if before_id is not None:
            clauses.append("id < ?")
            args.append(before_id)
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

    # -- figures for Insights (P15, FR-19.5) -------------------------------------------------------
    def call_days(self, since: float) -> list[CallDay]:
        """Model calls since ``since`` grouped by UTC day, model, prompt version, task, kind and outcome."""
        rows = self.query(
            "SELECT strftime('%Y-%m-%d', at, 'unixepoch') AS day, model, prompt_version, task, kind, outcome,"
            " COUNT(*) AS calls, COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(tokens_out), 0) AS tokens_out,"
            " COALESCE(SUM(cost_est), 0) AS cost, COALESCE(SUM(latency_ms), 0) AS latency_ms,"
            " COALESCE(SUM(repairs), 0) AS repairs FROM llm_calls WHERE at >= ?"
            " GROUP BY day, model, prompt_version, task, kind, outcome ORDER BY day", (since,),
        )
        return [
            CallDay(
                day=r["day"], model=r["model"], prompt_version=r["prompt_version"], task=r["task"], kind=r["kind"],
                outcome=r["outcome"], calls=int(r["calls"]), tokens_in=int(r["tokens_in"]), tokens_out=int(r["tokens_out"]),
                cost=float(r["cost"]), latency_ms=int(r["latency_ms"]), repairs=int(r["repairs"]),
            )
            for r in rows
        ]

    def job_prompts(self, job_ids: Iterable[str]) -> list[JobPrompt]:
        """The prompt version each of these jobs made its calls with (an event or a signal names only the job)."""
        wanted = sorted({j for j in job_ids if j})
        out: list[JobPrompt] = []
        for start in range(0, len(wanted), self._PATH_CHUNK):
            chunk = wanted[start:start + self._PATH_CHUNK]
            rows = self.query(
                "SELECT job_id, MIN(prompt_version) AS prompt_version FROM llm_calls"
                f" WHERE job_id IN ({','.join('?' * len(chunk))}) AND prompt_version <> '' GROUP BY job_id", chunk,
            )
            out.extend(JobPrompt(job_id=r["job_id"], prompt_version=r["prompt_version"]) for r in rows)
        return out

    def latencies(self, since: float, limit: int = 20_000) -> list[int]:
        """Milliseconds the answered calls took since ``since``, the newest ``limit`` of them."""
        rows = self.query(
            "SELECT latency_ms FROM llm_calls WHERE at >= ? AND outcome = 'ok' AND latency_ms > 0"
            " ORDER BY id DESC LIMIT ?", (since, int(limit)),
        )
        return [int(r["latency_ms"]) for r in rows]

    def job_waits(self, since: float, kinds: Iterable[str]) -> list[float]:
        """Seconds each job of these kinds waited between being asked for and starting, for jobs started since ``since``."""
        names = list(kinds)
        if not names:
            return []
        marks = ",".join("?" * len(names))
        rows = self.query(
            f"SELECT started_at - created_at AS waited FROM jobs WHERE started_at >= ? AND kind IN ({marks})"
            " AND started_at >= created_at", (since, *names),
        )
        return [float(r["waited"]) for r in rows]

    def metric_rows(self, since_day: str) -> list[MetricRow]:
        rows = self.query(
            "SELECT day, name, labels_json, value FROM metrics_daily WHERE day >= ? ORDER BY day, name, labels_json",
            (since_day,),
        )
        return [
            MetricRow(
                day=r["day"], name=r["name"], labels=tuple(sorted(json.loads(r["labels_json"] or "{}").items())),
                value=float(r["value"]),
            )
            for r in rows
        ]

    def metrics_replace(self, first_day: str, names: Iterable[str], rows: Iterable[MetricRow]) -> None:
        """Replace the figures called ``names`` from ``first_day`` on with ``rows``, in one transaction: a half-rolled day is
        never seen."""
        wanted = list(names)
        marks = ",".join("?" * len(wanted))
        with self.transaction() as db:
            db.execute(f"DELETE FROM metrics_daily WHERE day >= ? AND name IN ({marks})", (first_day, *wanted))
            db.executemany(
                "INSERT OR REPLACE INTO metrics_daily (day, name, labels_json, value) VALUES (?, ?, ?, ?)",
                [(r.day, r.name, json.dumps(dict(r.labels), sort_keys=True, ensure_ascii=False), r.value) for r in rows],
            )

    def metric_marker(self, name: str) -> float:
        """How far a roll-up has got (epoch seconds), 0 before the first one."""
        rows = self.query("SELECT value FROM metrics_daily WHERE day = '0000-00-00' AND name = ?", (name,))
        return float(rows[0]["value"]) if rows else 0.0

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

    _PATH_CHUNK = 400  # well under SQLite's limit on bound variables

    def current_versions_for(self, output_paths: Iterable[str]) -> dict[str, TranslationVersion]:
        """The current version of each of these files that has one: one query per few hundred paths, for a page."""
        wanted = list(dict.fromkeys(output_paths))
        found: dict[str, TranslationVersion] = {}
        for start in range(0, len(wanted), self._PATH_CHUNK):
            chunk = wanted[start:start + self._PATH_CHUNK]
            with self._lock:
                rows = self._db.execute(
                    f"SELECT * FROM translations WHERE status = 'current' AND output_path IN ({','.join('?' * len(chunk))})",
                    chunk,
                ).fetchall()
            found.update((str(row["output_path"]), _version_from_row(row)) for row in rows)
        return found

    def latest_version_statuses(self, output_paths: Iterable[str]) -> dict[str, str]:
        """``latest_version_status`` of each of these files that has a history (the others are left out)."""
        wanted = list(dict.fromkeys(output_paths))
        found: dict[str, str] = {}
        for start in range(0, len(wanted), self._PATH_CHUNK):
            chunk = wanted[start:start + self._PATH_CHUNK]
            with self._lock:
                rows = self._db.execute(
                    "SELECT t.output_path, t.status FROM translations t WHERE t.output_path IN"
                    f" ({','.join('?' * len(chunk))}) AND t.version = ("
                    "SELECT MAX(v.version) FROM translations v WHERE v.output_path = t.output_path)",
                    chunk,
                ).fetchall()
            found.update((str(row["output_path"]), str(row["status"])) for row in rows)
        return found

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
        params: dict[str, Any] | None = None,
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
                    " missing_units_json, origin, publishing, job_id, edited_at, params_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        output_path, source_path, source_sha256, target_lang, version, stored_path, sha256,
                        context_hash, trigger, parent_version, status,
                        model, tokens_in, tokens_out, now, unit_id, glossary_hash, int(context_complete),
                        memory_hash, json.dumps(list(missing_units), ensure_ascii=False), origin, int(publishing),
                        job_id, edited_at, json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    _VERSION_FIELDS = frozenset({"status", "publishing", "stored_path", "sha256", "edited_at", "glossary_hash"})

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

    def series_cue_edits(self, series_id: int, target_lang: str, *, exclude_unit: int | None = None) -> list[CueEdit]:
        """The cues admins corrected in any episode of a series, as (source line, fixed text) pairs: the candidates
        for few-shot examples (FR-18.5). Only edits that still say what they describe (they carry their source)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.unit_id, c.payload_json FROM corrections c JOIN units u ON u.id = c.unit_id"
                " WHERE u.series_id = ? AND c.target_lang = ? AND c.kind = 'cue_edit' AND c.active = 1"
                " AND (? IS NULL OR c.unit_id <> ?) ORDER BY c.id",
                (series_id, target_lang, exclude_unit, exclude_unit),
            ).fetchall()
        out: list[CueEdit] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            source, text = str(payload.get("source_text") or ""), str(payload.get("text") or "")
            if source.strip() and text.strip():
                out.append(CueEdit(unit_id=row["unit_id"], cue=int(payload.get("cue") or 0), source_text=source, text=text))
        return out

    def series_cue_edit_pairs(self, series_id: int, target_lang: str) -> list[CuePair]:
        """Every cue of a series that an admin corrected and whose correction still stands, in story order. ``before`` is
        what the machine wrote (kept with the correction since P15; empty for an older one)."""
        rows = self.query(
            "SELECT c.unit_id, u.season, u.episode, c.payload_json FROM corrections c JOIN units u ON u.id = c.unit_id"
            " WHERE u.series_id = ? AND c.target_lang = ? AND c.kind = 'cue_edit' AND c.active = 1"
            " ORDER BY u.season, u.episode, c.id", (series_id, target_lang),
        )
        out: list[CuePair] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            source, text = str(payload.get("source_text") or ""), str(payload.get("text") or "")
            if not (source.strip() and text.strip()):
                continue
            out.append(CuePair(
                unit_id=row["unit_id"], season=row["season"], episode=row["episode"], cue=int(payload.get("cue") or 0),
                source=source, before=str(payload.get("before_text") or ""), after=text,
            ))
        return out

    # -- glossaries, style profiles, character cards (P12, ADR-14) -----------------------------------
    def glossary(self, owner_kind: str, owner_key: str, lang: str) -> StoredGlossary | None:
        rows = self.query(
            "SELECT * FROM glossaries WHERE owner_kind = ? AND owner_key = ? AND lang = ?", (owner_kind, owner_key, lang)
        )
        return _glossary_from_row(rows[0]) if rows else None

    def glossary_languages(self, owner_kind: str, owner_key: str) -> list[str]:
        rows = self.query(
            "SELECT lang FROM glossaries WHERE owner_kind = ? AND owner_key = ? ORDER BY lang", (owner_kind, owner_key)
        )
        return [r["lang"] for r in rows]

    def glossary_save(
        self, owner_kind: str, owner_key: str, lang: str, doc: dict[str, Any], rules_hash: str, *, note: str = "console",
    ) -> StoredGlossary:
        """Store a glossary document. What it replaces goes to the history first; saving the same document again
        changes nothing (no new version, no history line)."""
        with self.transaction() as db:
            saved = self._glossary_write(db, owner_kind, owner_key, lang, doc, rules_hash, note)
        return _glossary_from_row(saved)

    @staticmethod
    def _glossary_write(
        db: sqlite3.Connection, owner_kind: str, owner_key: str, lang: str, doc: dict[str, Any], rules_hash: str, note: str,
    ) -> sqlite3.Row:
        """The body of ``glossary_save`` inside a transaction the caller holds."""
        text = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        now = time.time()
        row = db.execute(
            "SELECT * FROM glossaries WHERE owner_kind = ? AND owner_key = ? AND lang = ?",
            (owner_kind, owner_key, lang),
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO glossaries (owner_kind, owner_key, lang, doc_json, rules_hash, version, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 1, ?)", (owner_kind, owner_key, lang, text, rules_hash, now),
            )
        elif json.dumps(json.loads(row["doc_json"]), ensure_ascii=False, sort_keys=True) != text:
            db.execute(
                "INSERT INTO glossary_history (glossary_id, version, doc_json, note, at) VALUES (?, ?, ?, ?, ?)",
                (row["id"], row["version"], row["doc_json"], note, now),
            )
            db.execute(
                "UPDATE glossaries SET doc_json = ?, rules_hash = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (text, rules_hash, now, row["id"]),
            )
        return db.execute(
            "SELECT * FROM glossaries WHERE owner_kind = ? AND owner_key = ? AND lang = ?",
            (owner_kind, owner_key, lang),
        ).fetchone()

    def glossary_history(self, owner_kind: str, owner_key: str, lang: str, *, limit: int = 50) -> list[GlossaryRevision]:
        """Earlier states of a glossary, newest first (the current one is not in the list)."""
        rows = self.query(
            "SELECT h.version, h.doc_json, h.note, h.at FROM glossary_history h JOIN glossaries g ON g.id = h.glossary_id"
            " WHERE g.owner_kind = ? AND g.owner_key = ? AND g.lang = ? ORDER BY h.version DESC LIMIT ?",
            (owner_kind, owner_key, lang, limit),
        )
        return [
            GlossaryRevision(version=r["version"], doc=json.loads(r["doc_json"]), at=r["at"], note=r["note"]) for r in rows
        ]

    def glossary_prune_history(self, keep: int, *, dry_run: bool = False) -> Reclaimed:
        """Keep the ``keep`` newest earlier states of each glossary and delete the older ones (FR-13.1). The current
        document is never in the history, so it cannot be touched."""
        if keep < 1:
            return Reclaimed()
        select = (
            "SELECT id, length(doc_json) AS size FROM ("
            " SELECT id, doc_json, row_number() OVER (PARTITION BY glossary_id ORDER BY version DESC) AS n"
            " FROM glossary_history) WHERE n > ?"
        )
        with self.transaction() as db:
            doomed = db.execute(select, (keep,)).fetchall()
            if doomed and not dry_run:
                db.executemany("DELETE FROM glossary_history WHERE id = ?", [(r["id"],) for r in doomed])
        return Reclaimed(rows=len(doomed), bytes=sum(r["size"] for r in doomed))

    def style_row(self, scope: str, scope_id: str) -> StyleRow | None:
        rows = self.query("SELECT * FROM style_profiles WHERE scope = ? AND scope_id = ?", (scope, scope_id))
        return _style_from_row(rows[0]) if rows else None

    def style_rows(self, scope: str) -> list[StyleRow]:
        return [_style_from_row(r) for r in self.query("SELECT * FROM style_profiles WHERE scope = ? ORDER BY scope_id", (scope,))]

    def style_save(self, scope: str, scope_id: str, doc: dict[str, Any]) -> StyleRow:
        text = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        now = time.time()
        with self.transaction() as db:
            db.execute(
                "INSERT INTO style_profiles (scope, scope_id, doc_json, version, updated_at) VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT (scope, scope_id) DO UPDATE SET doc_json = excluded.doc_json, updated_at = excluded.updated_at,"
                " version = style_profiles.version + (style_profiles.doc_json <> excluded.doc_json)",
                (scope, scope_id, text, now),
            )
            row = db.execute("SELECT * FROM style_profiles WHERE scope = ? AND scope_id = ?", (scope, scope_id)).fetchone()
        return _style_from_row(row)

    def style_delete(self, scope: str, scope_id: str) -> bool:
        with self.transaction() as db:
            return db.execute("DELETE FROM style_profiles WHERE scope = ? AND scope_id = ?", (scope, scope_id)).rowcount > 0

    def cards(self, owner_kind: str, owner_key: str) -> list[CardRow]:
        rows = self.query(
            "SELECT * FROM character_cards WHERE owner_kind = ? AND owner_key = ? ORDER BY name", (owner_kind, owner_key)
        )
        return [_card_from_row(r) for r in rows]

    def card_save(
        self, owner_kind: str, owner_key: str, name: str, doc: dict[str, Any], *, replaces: str | None = None,
    ) -> CardRow:
        """Create or update a card. ``replaces`` is the old name when the card is renamed."""
        with self.transaction() as db:
            row = self._card_write(db, owner_kind, owner_key, name, doc, replaces)
        return _card_from_row(row)

    @staticmethod
    def _card_write(
        db: sqlite3.Connection, owner_kind: str, owner_key: str, name: str, doc: dict[str, Any], replaces: str | None,
    ) -> sqlite3.Row:
        """The body of ``card_save`` inside a transaction the caller holds."""
        text = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        now = time.time()
        old = db.execute(
            "SELECT id FROM character_cards WHERE owner_kind = ? AND owner_key = ? AND name = ?",
            (owner_kind, owner_key, replaces or name),
        ).fetchone()
        # Names are unique whatever their case: a rename onto another card is refused, not left to the constraint.
        clash = db.execute(
            "SELECT name FROM character_cards WHERE owner_kind = ? AND owner_key = ? AND name = ?"
            + (" AND id <> ?" if old is not None else ""),
            (owner_kind, owner_key, name, *((old["id"],) if old is not None else ())),
        ).fetchone()
        if clash is not None:
            raise ValueError(f"there is already a card for {clash['name']!r}; rename or delete that one first")
        if old is None:
            db.execute(
                "INSERT INTO character_cards (owner_kind, owner_key, name, doc_json, version, updated_at)"
                " VALUES (?, ?, ?, ?, 1, ?)", (owner_kind, owner_key, name, text, now),
            )
        else:
            db.execute(
                "UPDATE character_cards SET name = ?, doc_json = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (name, text, now, old["id"]),
            )
        return db.execute(
            "SELECT * FROM character_cards WHERE owner_kind = ? AND owner_key = ? AND name = ?",
            (owner_kind, owner_key, name),
        ).fetchone()

    def card_delete(self, owner_kind: str, owner_key: str, name: str) -> bool:
        with self.transaction() as db:
            return db.execute(
                "DELETE FROM character_cards WHERE owner_kind = ? AND owner_key = ? AND name = ?",
                (owner_kind, owner_key, name),
            ).rowcount > 0

    def character_write(
        self,
        owner_kind: str,
        owner_key: str,
        lang: str,
        *,
        glossary: tuple[dict[str, Any], str] | None = None,
        card: tuple[str, dict[str, Any], str | None] | None = None,
        delete_card: str | None = None,
        note: str = "console",
    ) -> None:
        """One character is two rows: its Name in the glossary and its Voice in a card. They are written (or the card is
        deleted) in ONE transaction, so a failure leaves neither half behind. ``glossary`` is ``(document, rules hash)``,
        ``card`` is ``(name, document, replaces)``. A card to delete that is not there is an error and rolls back.
        ``note`` is what the glossary history says made the change."""
        with self.transaction() as db:
            if delete_card is not None:
                removed = db.execute(
                    "DELETE FROM character_cards WHERE owner_kind = ? AND owner_key = ? AND name = ?",
                    (owner_kind, owner_key, delete_card),
                ).rowcount
                if removed == 0:
                    raise LookupError(f"no card for {delete_card!r}")
            if glossary is not None:
                self._glossary_write(db, owner_kind, owner_key, lang, glossary[0], glossary[1], note)
            if card is not None:
                self._card_write(db, owner_kind, owner_key, card[0], card[1], card[2])

    # -- suggestions (P13, FR-4.4, FR-11.1) ---------------------------------------------------------------
    def suggestion_add(
        self, series_id: int, target_lang: str, kind: str, source: str, fingerprint: str, payload: dict[str, Any],
        evidence: list[Any], sources: list[str], *, identity: str = "",
    ) -> Suggestion | None:
        """Store a pending suggestion; ``None`` when the series already has one with that fingerprint, or about the same
        thing (``identity``), in that language (pending, accepted or rejected: a rejected one is exactly what must not
        come back, however the model worded it this time)."""
        with self.transaction() as db:
            if identity and db.execute(
                "SELECT 1 FROM suggestions WHERE series_id = ? AND target_lang = ? AND identity = ?",
                (series_id, target_lang, identity),
            ).fetchone():
                return None
            cursor = db.execute(
                "INSERT INTO suggestions (series_id, target_lang, kind, source, fingerprint, payload_json, evidence_json,"
                " sources_json, created_at, identity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (series_id, target_lang, fingerprint) DO NOTHING",
                (
                    series_id, target_lang, kind, source, fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence, ensure_ascii=False), json.dumps(sources, ensure_ascii=False), time.time(), identity,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = db.execute("SELECT * FROM suggestions WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _suggestion_from_row(row)

    def suggestion(self, suggestion_id: int) -> Suggestion | None:
        rows = self.query("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,))
        return _suggestion_from_row(rows[0]) if rows else None

    def suggestion_by_fingerprint(self, series_id: int, target_lang: str, fingerprint: str) -> Suggestion | None:
        rows = self.query(
            "SELECT * FROM suggestions WHERE series_id = ? AND target_lang = ? AND fingerprint = ?",
            (series_id, target_lang, fingerprint),
        )
        return _suggestion_from_row(rows[0]) if rows else None

    def suggestion_by_identity(self, series_id: int, target_lang: str, identity: str) -> Suggestion | None:
        """The newest suggestion of the series about this identity, whatever its status and wording."""
        if not identity:
            return None
        rows = self.query(
            "SELECT * FROM suggestions WHERE series_id = ? AND target_lang = ? AND identity = ? ORDER BY id DESC LIMIT 1",
            (series_id, target_lang, identity),
        )
        return _suggestion_from_row(rows[0]) if rows else None

    def suggestion_skip(
        self, series_id: int, target_lang: str, identities: Iterable[str], prompt_version: str, at: float | None = None,
    ) -> None:
        """Remember candidates the model had no answer for (R13-5), so the next run asks about others."""
        now = time.time() if at is None else at
        rows = [(series_id, target_lang, key, prompt_version, now) for key in identities if key]
        if not rows:
            return
        with self.transaction() as db:
            db.executemany(
                "INSERT INTO suggestion_skips (series_id, target_lang, identity, prompt_version, at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (series_id, target_lang, identity) DO UPDATE SET"
                " prompt_version = excluded.prompt_version, at = excluded.at",
                rows,
            )

    def suggestion_skips(self, series_id: int, target_lang: str, prompt_version: str, since: float) -> frozenset[str]:
        """The identities skipped since ``since`` under this prompt version (an older skip, or one made under another
        prompt, lets the candidate be asked about again)."""
        rows = self.query(
            "SELECT identity FROM suggestion_skips WHERE series_id = ? AND target_lang = ? AND prompt_version = ? AND at >= ?",
            (series_id, target_lang, prompt_version, since),
        )
        return frozenset(r["identity"] for r in rows)

    def suggestions(
        self, *, series_id: int | None = None, status: str | None = None, source: str | None = None,
        target_lang: str | None = None, limit: int = 500,
    ) -> list[Suggestion]:
        """Suggestions grouped for display: by series, pending ones oldest first, decided ones newest first."""
        where, args = [], []
        for column, value in (("series_id", series_id), ("status", status), ("source", source), ("target_lang", target_lang)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        order = "id DESC" if status in (SUGGESTION_ACCEPTED, SUGGESTION_REJECTED) else "id"
        rows = self.query(
            f"SELECT * FROM suggestions{' WHERE ' + ' AND '.join(where) if where else ''} ORDER BY series_id, {order} LIMIT ?",
            (*args, limit),
        )
        return [_suggestion_from_row(r) for r in rows]

    def suggestion_counts(self) -> list[SuggestionCount]:
        """How many suggestions each series has in each status: the badge on the nav and the groups of the Review page."""
        rows = self.query(
            "SELECT status, series_id, COUNT(*) AS n FROM suggestions GROUP BY status, series_id ORDER BY series_id"
        )
        return [SuggestionCount(status=r["status"], series_id=int(r["series_id"]), count=int(r["n"])) for r in rows]

    def suggestion_decide(
        self, suggestion_id: int, status: str, *, payload: dict[str, Any] | None = None,
        applied: dict[str, Any] | None = None, auto: bool = False,
    ) -> Suggestion:
        """Record a decision. ``payload`` replaces the stored one (accepted after an edit); ``applied`` is what the
        acceptance changed. Going back to ``pending`` (an undo) forgets both the decision and the auto mark."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
            if row is None:
                raise LookupError(f"no suggestion {suggestion_id}")
            pending = status == SUGGESTION_PENDING
            db.execute(
                "UPDATE suggestions SET status = ?, payload_json = ?, applied_json = ?, auto_accepted = ?, decided_at = ?"
                " WHERE id = ?",
                (
                    status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else row["payload_json"],
                    json.dumps(applied or {}, ensure_ascii=False), 0 if pending else int(auto),
                    0 if pending else time.time(), suggestion_id,
                ),
            )
            saved = db.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        return _suggestion_from_row(saved)

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

    def set_series_bootstrap_released(self, series_id: int, released: bool) -> None:
        """**Continue** (FR-15.12): ``auto_request`` stops holding this series back until a glossary is reviewed."""
        with self._lock:
            self._db.execute("UPDATE series SET bootstrap_released = ? WHERE id = ?", (int(released), series_id))

    def series_seed(self, owner_key: str, series_id: int | None) -> SeriesSeed:
        """What the series has had approved: a glossary with something in it (``owner_key`` is the folder-safe
        series key the glossaries are stored under), character cards, and its suggestions waiting and decided."""
        glossary = False
        for row in self.query("SELECT doc_json FROM glossaries WHERE owner_kind = 'series' AND owner_key = ?", (owner_key,)):
            doc = json.loads(row["doc_json"])
            if any(doc.get(part) for part in ("terms", "characters", "address_rules", "style")):
                glossary = True
        cards = int(self.query(
            "SELECT COUNT(*) AS n FROM character_cards WHERE owner_kind = 'series' AND owner_key = ?", (owner_key,)
        )[0]["n"])
        pending = decided = 0
        if series_id is not None:
            for row in self.query("SELECT status, COUNT(*) AS n FROM suggestions WHERE series_id = ? GROUP BY status", (series_id,)):
                if row["status"] == SUGGESTION_PENDING:
                    pending += int(row["n"])
                else:
                    decided += int(row["n"])
        return SeriesSeed(glossary=glossary, cards=cards, pending=pending, decided=decided)

    def translated_episodes(self, series_key: str) -> frozenset[tuple[int | None, int | None]]:
        """``(season, episode)`` of the episodes of a series that have a translation this worker made and that is in force.
        The bootstrap counts them among a series' first episodes, so it does not walk on through the whole series."""
        rows = self.query(
            "SELECT DISTINCT u.season, u.episode FROM units u JOIN series s ON s.id = u.series_id"
            " JOIN translations t ON t.unit_id = u.id WHERE s.key = ? AND t.status = 'current' AND t.origin = 'machine'",
            (series_key,),
        )
        return frozenset((r["season"], r["episode"]) for r in rows)

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
            # One read of what is stored, then a write only for a row that is new or differs (R10-9): a sync that finds
            # nothing new used to SELECT and UPDATE every row of the library every few minutes.
            stored = {(r["kind"], r["bazarr_id"]): r for r in db.execute("SELECT * FROM catalog_items")}
            seen: set[int] = set()
            parents: dict[str, int] = {}
            for entry in sorted(entries, key=lambda e: e.kind != "series"):  # a series row before its episodes
                parent_id = parents.get(entry.parent_bazarr_id) if entry.parent_bazarr_id else None
                if entry.parent_bazarr_id and parent_id is None:
                    found = stored.get(("series", entry.parent_bazarr_id))
                    parent_id = int(found["id"]) if found else None
                fields = (
                    parent_id, entry.series_key, entry.season, entry.episode, entry.title, entry.video_path,
                    entry.tvdb_id, json.dumps([asdict(s) for s in entry.subtitles], ensure_ascii=False),
                    json.dumps(list(entry.missing)),
                )
                row = stored.get((entry.kind, entry.bazarr_id))
                if row is None:
                    cursor = db.execute(
                        "INSERT INTO catalog_items (kind, bazarr_id, parent_id, series_key, season, episode, title,"
                        " video_path, tvdb_id, subtitles_json, missing_json, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (entry.kind, entry.bazarr_id, *fields, at),
                    )
                    item_id, added = int(cursor.lastrowid), added + 1
                else:
                    item_id, updated = int(row["id"]), updated + 1
                    unchanged = row["gone_at"] is None and tuple(row[c] for c in (
                        "parent_id", "series_key", "season", "episode", "title", "video_path", "tvdb_id", "subtitles_json",
                        "missing_json",
                    )) == fields
                    if not unchanged:
                        db.execute(
                            "UPDATE catalog_items SET parent_id = ?, series_key = ?, season = ?, episode = ?, title = ?,"
                            " video_path = ?, tvdb_id = ?, subtitles_json = ?, missing_json = ?, synced_at = ?, gone_at = NULL"
                            " WHERE id = ?", (*fields, at, item_id),
                        )
                seen.add(item_id)
                if entry.kind == "series":
                    parents[entry.bazarr_id] = item_id
            vanished = [(at, int(r["id"])) for r in stored.values() if r["gone_at"] is None and int(r["id"]) not in seen]
            db.executemany("UPDATE catalog_items SET gone_at = ? WHERE id = ?", vanished)
        return CatalogApplied(added=added, updated=updated, gone=len(vanished))

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
