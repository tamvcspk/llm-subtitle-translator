"""Glossaries, style profiles and character cards (P12, ADR-14)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ...domain.ports import CardRow, GlossaryRevision, Reclaimed, StoredGlossary, StyleRow
from .base import Repo


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


class GlossaryRepo(Repo):
    """Glossaries, style profiles and character cards (P12, ADR-14)."""

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
