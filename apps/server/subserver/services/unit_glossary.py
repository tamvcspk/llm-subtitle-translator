"""The glossary of a series or a movie as the pages read and save it; YAML is the exchange format (FR-18.10).

``GlossaryMixin`` is a part of ``UnitService`` (``units.py``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from subcore import glossary as g

from .library import Owner


class GlossaryMixin:
    def _series(self, series_id: int):
        series = self.catalog.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        return series

    def glossary(self, series_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary(self.library.series_owner(series_id), lang)

    def save_glossary(
        self, series_id: int, lang: str, text: str | None = None, *, doc: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.save_owner_glossary(self.library.series_owner(series_id), lang, text, doc=doc, dry_run=dry_run)

    def glossary_history(self, series_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary_history(self.library.series_owner(series_id), lang)

    def restore_glossary(self, series_id: int, lang: str, version: int, *, dry_run: bool = False) -> dict[str, Any]:
        return self.restore_owner_glossary(self.library.series_owner(series_id), lang, version, dry_run=dry_run)

    # The same four for a movie, by the movie's unit id (P12b): the glossary of `movies/<movie_key>`.
    def movie_glossary(self, unit_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary(self.library.movie_owner(unit_id), lang)

    def save_movie_glossary(
        self, unit_id: int, lang: str, text: str | None = None, *, doc: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.save_owner_glossary(self.library.movie_owner(unit_id), lang, text, doc=doc, dry_run=dry_run)

    def movie_glossary_history(self, unit_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary_history(self.library.movie_owner(unit_id), lang)

    def restore_movie_glossary(self, unit_id: int, lang: str, version: int, *, dry_run: bool = False) -> dict[str, Any]:
        return self.restore_owner_glossary(self.library.movie_owner(unit_id), lang, version, dry_run=dry_run)

    def owner_glossary(self, owner: Owner, lang: str) -> dict[str, Any]:
        """The glossary as a document (for the form) and as YAML (to export), with the number of stale translations."""
        stored = self.glossaries.stored(owner.kind, owner.key, lang)
        glossary = self.glossaries.load(owner.kind, owner.key, lang)
        impact = self.library.impact_units(owner.units, lang, glossary=glossary)
        return {
            "lang": lang, "exists": stored is not None,
            "doc": g.to_doc(glossary) if stored else {"version": 1}, "yaml": self.glossaries.read_text(owner.kind, owner.key, lang),
            "version": stored.version if stored else 0, "stale_units": impact["stale_now"],
        }

    def save_owner_glossary(
        self, owner: Owner, lang: str, text: str | None = None, *, doc: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Save a glossary given as a document (the form) or as YAML text (an import). A bad one raises ValueError
        naming the offending path; ``dry_run`` only says how many translations the save would make stale."""
        if doc is not None:
            if not isinstance(doc, dict):
                raise ValueError("doc must be an object")
            parsed, note = g.from_doc(doc), "console"  # GlossaryError is a ValueError
        else:
            text = text or ""
            parsed, note = (g.loads(text) if text.strip() else g.Glossary()), "import"
        impact = self.library.impact_units(owner.units, lang, glossary=parsed)
        saved = None
        if not dry_run:
            saved = self.glossaries.save(owner.kind, owner.key, lang, parsed, note=note)
        return {"ok": True, "saved": not dry_run, "version": saved.version if saved else None, **impact}

    def glossary_export(self, kind: str, owner_id: int, lang: str) -> tuple[str, str]:
        """``(file name, YAML text)`` of a series' (``kind`` ``series``) or a movie's glossary, for a download link.
        A glossary that is not stored exports as an empty text, as ``GET …/glossary`` does."""
        owner = self.library.movie_owner(owner_id) if kind == "movies" else self.library.series_owner(owner_id)
        slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", owner.title or "").encode("ascii", "ignore").decode().lower()).strip("-")
        code = re.sub(r"[^A-Za-z0-9_-]+", "", lang) or "xx"
        return f"glossary-{slug or 'untitled'}-{code}.yaml", self.glossaries.read_text(owner.kind, owner.key, lang)

    def owner_glossary_history(self, owner: Owner, lang: str) -> dict[str, Any]:
        """What each save replaced, newest first (the history that stands in for the old backups)."""
        return {
            "lang": lang,
            "items": [
                {"version": r.version, "at": r.at, "note": r.note, "terms": len(r.doc.get("terms") or ()),
                 "characters": len(r.doc.get("characters") or ()), "address_rules": len(r.doc.get("address_rules") or ())}
                for r in self.glossaries.history(owner.kind, owner.key, lang)
            ],
        }

    def restore_owner_glossary(self, owner: Owner, lang: str, version: int, *, dry_run: bool = False) -> dict[str, Any]:
        """Put an earlier glossary back. It changes the rules like any other save, so the answer counts the
        translations it makes stale and ``dry_run`` only counts them."""
        version = int(version)
        impact = self.library.impact_units(
            owner.units, lang, glossary=self.glossaries.restored(owner.kind, owner.key, lang, version),
        )
        if dry_run:
            return {"ok": True, "saved": False, "version": None, "restored": version, **impact}
        restored = self.glossaries.restore(owner.kind, owner.key, lang, version)
        return {"ok": True, "saved": True, "version": restored.version, "restored": version, **impact}

    def copy_glossary(
        self, series_id: int, lang: str, source_series_id: int, *, merge: bool, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Copy another series' glossary into this one: a copy, not a link (Non-goals, v0.12). The answer counts the
        translations of this series the copy makes stale; ``dry_run`` only counts them."""
        series, source = self._series(series_id), self._series(source_series_id)
        if series.id == source.id:
            raise ValueError("choose another series to copy from")
        owner = self.library.series_owner(series.id)
        candidate = self.glossaries.copied(("series", source.key), ("series", series.key), lang, merge=merge)
        impact = self.library.impact_units(owner.units, lang, glossary=candidate)
        origin = source.title or source.key
        if dry_run:
            return {"ok": True, "saved": False, "version": None, "from": origin, **impact}
        result = self.glossaries.copy(("series", source.key), ("series", series.key), lang, merge=merge)
        return {"ok": True, "saved": True, "version": result.version, "from": origin, **impact}
