"""Console settings and API keys (P6, ADR-13)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ...domain.ports import ApiKey, SettingRow
from .base import Repo


def _key_from_row(row: sqlite3.Row) -> ApiKey:
    return ApiKey(
        id=row["id"], provider=row["provider"], label=row["label"], value=row["value"], status=row["status"],
        last_used_at=row["last_used_at"], updated_at=row["updated_at"],
    )


class SettingsRepo(Repo):
    """Console settings and API keys (P6, ADR-13)."""

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
