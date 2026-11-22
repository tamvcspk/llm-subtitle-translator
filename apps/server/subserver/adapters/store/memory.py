"""Story memory: facts, summaries and snapshots (P4)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Iterable

from subcore.memory import Facts

from ...domain.ports import StoredFacts
from .base import Repo


def _facts_from_row(row: sqlite3.Row) -> StoredFacts:
    return StoredFacts(
        unit_id=row["unit_id"], source_sha256=row["source_sha256"], prompt_version=row["prompt_version"],
        model=row["model"], facts=Facts.from_dict(json.loads(row["json"])), facts_hash=row["facts_hash"],
        tokens_in=row["tokens_in"], tokens_out=row["tokens_out"], created_at=row["created_at"],
    )


class MemoryRepo(Repo):
    """Story memory: facts, summaries and snapshots (P4)."""

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
