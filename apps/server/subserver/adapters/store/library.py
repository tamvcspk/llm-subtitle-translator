"""Series, units, their outputs and the admin's corrections (P3, P4)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ...domain.ports import Correction, CueEdit, CuePair, Output, Series, SeriesSeed, Unit, SUGGESTION_PENDING
from .base import Repo


def _series_from_row(row: sqlite3.Row) -> Series:
    return Series(
        id=row["id"], key=row["key"], title=row["title"], bazarr_id=row["bazarr_id"],
        stale_policy=row["stale_policy"], memory=row["memory"], specials_after=row["specials_after"],
        created_at=row["created_at"], auto_request=row["auto_request"],
        bootstrap_released=bool(row["bootstrap_released"]),
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


class LibraryRepo(Repo):
    """Series, units, their outputs and the admin's corrections (P3, P4)."""

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

    # -- series and unit lookups, per-series switches (P4) ------------------------------------------------------------
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
