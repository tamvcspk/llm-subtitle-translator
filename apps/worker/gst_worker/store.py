"""SQLite store for machine-owned state (ADR-5): subtitle versions, LLM call records, and the library.

The schema is a subset of docs/design/ARCHITECTURE.md section 4. ``translations`` is still keyed by
``output_path`` (one history per target file) and carries a nullable ``unit_id`` linking it to the
library (``series`` / ``units``, with ``unit_outputs`` mapping a unit and target language to its file).
Queue job files stay on disk for now, so there is no ``jobs`` table yet.
"""

from __future__ import annotations

import json
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

    def get_series(self, series_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
        return dict(row) if row else None

    def list_series(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM series ORDER BY title COLLATE NOCASE, id").fetchall()
        return [dict(row) for row in rows]

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

    def get_unit(self, unit_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
        return dict(row) if row else None

    def list_units(self, series_id: int | None = None, *, movies: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            if movies:
                rows = self._db.execute(
                    "SELECT * FROM units WHERE series_id IS NULL ORDER BY title COLLATE NOCASE, id"
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM units WHERE series_id = ? ORDER BY season, episode, id", (series_id,)
                ).fetchall()
        return [dict(row) for row in rows]

    def set_output(self, unit_id: int, target_lang: str, output_path: str, target_language: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO unit_outputs (unit_id, target_lang, target_language, output_path) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (unit_id, target_lang) DO UPDATE SET output_path = excluded.output_path,"
                " target_language = CASE WHEN excluded.target_language <> '' THEN excluded.target_language"
                " ELSE unit_outputs.target_language END",
                (unit_id, target_lang, target_language, output_path),
            )

    def outputs(self, unit_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM unit_outputs WHERE unit_id = ? ORDER BY target_lang", (unit_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # -- corrections (episode scope; wider scopes live in the glossary YAML) -----
    def add_correction(self, unit_id: int, target_lang: str, kind: str, payload: dict[str, Any]) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO corrections (unit_id, target_lang, kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (unit_id, target_lang, kind, json.dumps(payload, ensure_ascii=False, sort_keys=True), time.time()),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _correction(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        item["active"] = bool(item["active"])
        return item

    def corrections(self, unit_id: int, target_lang: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM corrections WHERE unit_id = ? AND target_lang = ?"
        if active_only:
            query += " AND active = 1"
        with self._lock:
            rows = self._db.execute(query + " ORDER BY id", (unit_id, target_lang)).fetchall()
        return [self._correction(row) for row in rows]

    def get_correction(self, correction_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM corrections WHERE id = ?", (correction_id,)).fetchone()
        return self._correction(row) if row else None

    def deactivate_correction(self, correction_id: int) -> bool:
        with self._lock:
            cursor = self._db.execute("UPDATE corrections SET active = 0 WHERE id = ? AND active = 1", (correction_id,))
            return cursor.rowcount > 0

    # -- story memory (P4) ------------------------------------------------------------
    def find_unit(self, series_id: int, season: int, episode: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM units WHERE series_id = ? AND season = ? AND episode = ?", (series_id, season, episode)
            ).fetchone()
        return dict(row) if row else None

    def set_series_memory(self, series_id: int, *, memory: str | None = None, specials_after: str | None = None) -> None:
        with self._lock:
            if memory is not None:
                self._db.execute("UPDATE series SET memory = ? WHERE id = ?", (memory, series_id))
            if specials_after is not None:
                self._db.execute("UPDATE series SET specials_after = ? WHERE id = ?", (specials_after, series_id))

    def facts_row(self, unit_id: int, source_sha256: str, prompt_version: str, *, touch: bool = False) -> dict[str, Any] | None:
        """Stored facts for exactly this source and prompt version. ``touch`` marks them as the newest in use."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM facts WHERE unit_id = ? AND source_sha256 = ? AND prompt_version = ?",
                (unit_id, source_sha256, prompt_version),
            ).fetchone()
            if row is not None and touch:
                self._db.execute("UPDATE facts SET created_at = ? WHERE id = ?", (time.time(), row["id"]))
        return dict(row) if row else None

    def save_facts(
        self, unit_id: int, source_sha256: str, prompt_version: str, *, model: str, facts: dict[str, Any],
        facts_hash: str, tokens_in: int = 0, tokens_out: int = 0,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO facts (unit_id, source_sha256, prompt_version, model, json, facts_hash, tokens_in,"
                " tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (unit_id, source_sha256, prompt_version) DO UPDATE SET model = excluded.model,"
                " json = excluded.json, facts_hash = excluded.facts_hash, tokens_in = excluded.tokens_in,"
                " tokens_out = excluded.tokens_out, created_at = excluded.created_at",
                (unit_id, source_sha256, prompt_version, model, json.dumps(facts, ensure_ascii=False, sort_keys=True),
                 facts_hash, tokens_in, tokens_out, time.time()),
            )

    def latest_facts(self, unit_ids: Iterable[int], prompt_version: str) -> dict[int, dict[str, Any]]:
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
        return {int(row["unit_id"]): dict(row) for row in rows}  # later rows overwrite earlier ones

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

    def put_snapshot(self, memory_hash: str, sections: list[tuple[str, str]]) -> None:
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

    def cached_versions(self, source_sha256: str, target_lang: str, context_hash: str) -> Iterable[dict[str, Any]]:
        """Versions made from the same input and context, newest first (NFR-3)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM translations WHERE source_sha256 = ? AND target_lang = ? AND context_hash = ?"
                " AND trigger IN ('auto', 'retranslate') ORDER BY id DESC",
                (source_sha256, target_lang, context_hash),
            ).fetchall()
        return [dict(row) for row in rows]
