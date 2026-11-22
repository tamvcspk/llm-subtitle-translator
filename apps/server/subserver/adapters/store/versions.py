"""Subtitle versions: the history of each target file (FR-16)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Iterable

from ...domain.ports import TranslationVersion
from .base import Repo


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


class VersionRepo(Repo):
    """Subtitle versions: the history of each target file (FR-16)."""

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

    def cached_versions(self, source_sha256: str, target_lang: str, context_hash: str) -> list[TranslationVersion]:
        """Versions made from the same input and context, newest first (NFR-3)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM translations WHERE source_sha256 = ? AND target_lang = ? AND context_hash = ?"
                " AND trigger IN ('auto', 'retranslate') AND status <> 'pruned' ORDER BY id DESC",
                (source_sha256, target_lang, context_hash),
            ).fetchall()
        return [_version_from_row(row) for row in rows]
