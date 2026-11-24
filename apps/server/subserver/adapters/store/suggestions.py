"""What the generators propose and what the admin decided (P13, FR-4.4)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Iterable

from ...domain.ports import SearchDisplay, Suggestion, SuggestionCount, SUGGESTION_ACCEPTED, SUGGESTION_PENDING, SUGGESTION_REJECTED
from .base import Repo


def _suggestion_from_row(row: sqlite3.Row) -> Suggestion:
    return Suggestion(
        id=row["id"], series_id=row["series_id"], target_lang=row["target_lang"], kind=row["kind"],
        source=row["source"], fingerprint=row["fingerprint"], payload=json.loads(row["payload_json"]),
        evidence=json.loads(row["evidence_json"] or "[]"), sources=json.loads(row["sources_json"] or "[]"),
        auto_accepted=bool(row["auto_accepted"]), status=row["status"], applied=json.loads(row["applied_json"] or "{}"),
        decided_at=row["decided_at"], created_at=row["created_at"], identity=row["identity"],
        display_id=int(row["display_id"] or 0),
    )


DISPLAY_KEEP_SECONDS = 2 * 365 * 86400  # the longest Google lets Search Suggestions and grounded results be stored


class SuggestionRepo(Repo):
    """What the generators propose and what the admin decided (P13, FR-4.4)."""

    # -- suggestions (P13, FR-4.4, FR-11.1) ---------------------------------------------------------------
    def suggestion_add(
        self, series_id: int, target_lang: str, kind: str, source: str, fingerprint: str, payload: dict[str, Any],
        evidence: list[Any], sources: list[str], *, identity: str = "", display_id: int = 0,
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
                " sources_json, created_at, identity, display_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (series_id, target_lang, fingerprint) DO NOTHING",
                (
                    series_id, target_lang, kind, source, fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence, ensure_ascii=False), json.dumps(sources, ensure_ascii=False), time.time(), identity,
                    display_id or None,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = db.execute("SELECT * FROM suggestions WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _suggestion_from_row(row)

    def search_display_add(
        self, series_id: int, target_lang: str, html: str, queries: Iterable[str], *, now: float | None = None,
    ) -> int:
        """Keep the Search Suggestions of one search call and return its id. Rows older than two years are dropped first
        (``DISPLAY_KEEP_SECONDS``), and the suggestions that pointed at them lose the link."""
        at = time.time() if now is None else now
        with self.transaction() as db:
            old = [r["id"] for r in db.execute("SELECT id FROM search_displays WHERE created_at < ?", (at - DISPLAY_KEEP_SECONDS,))]
            for display_id in old:
                db.execute("UPDATE suggestions SET display_id = NULL WHERE display_id = ?", (display_id,))
                db.execute("DELETE FROM search_displays WHERE id = ?", (display_id,))
            cursor = db.execute(
                "INSERT INTO search_displays (series_id, target_lang, html, queries_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (series_id, target_lang, html, json.dumps(list(queries), ensure_ascii=False), at),
            )
            return int(cursor.lastrowid)

    def search_display_drop(self, display_id: int) -> None:
        """Forget one display (the search brought nothing that kept it), and the link of any suggestion to it."""
        with self.transaction() as db:
            db.execute("UPDATE suggestions SET display_id = NULL WHERE display_id = ?", (display_id,))
            db.execute("DELETE FROM search_displays WHERE id = ?", (display_id,))

    def search_display(self, display_id: int, *, now: float | None = None) -> SearchDisplay | None:
        """The display, unless it is older than the two years it may be kept: it is not served after that, whether or not a
        later search has come to prune it."""
        rows = self.query("SELECT * FROM search_displays WHERE id = ?", (display_id,))
        if not rows:
            return None
        row = rows[0]
        if row["created_at"] < (time.time() if now is None else now) - DISPLAY_KEEP_SECONDS:
            return None
        return SearchDisplay(
            id=row["id"], series_id=row["series_id"], target_lang=row["target_lang"], html=row["html"],
            queries=json.loads(row["queries_json"] or "[]"), created_at=row["created_at"],
        )

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
