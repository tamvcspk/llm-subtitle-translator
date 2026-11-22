"""The library as Bazarr lists it and the admin's decisions about it (P10, FR-14, FR-15.6)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from typing import Iterable

from ...domain.ports import CatalogApplied, CatalogEntry, CatalogState, CatalogSubtitle
from .base import Repo


def _catalog_from_row(row: sqlite3.Row) -> CatalogEntry:
    return CatalogEntry(
        kind=row["kind"], bazarr_id=row["bazarr_id"], series_key=row["series_key"], season=row["season"],
        episode=row["episode"], title=row["title"], video_path=row["video_path"], tvdb_id=row["tvdb_id"],
        subtitles=tuple(CatalogSubtitle(**item) for item in json.loads(row["subtitles_json"] or "[]")),
        missing=tuple(json.loads(row["missing_json"] or "[]")), id=row["id"], parent_id=row["parent_id"],
        synced_at=row["synced_at"], gone_at=row["gone_at"],
    )


class CatalogRepo(Repo):
    """The library as Bazarr lists it and the admin's decisions about it (P10, FR-14, FR-15.6)."""

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
