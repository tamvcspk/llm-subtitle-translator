"""Style profiles, genre presets and character cards in the database (FR-18, ADR-14).

What is stored, and where (``style_profiles`` and ``character_cards``):

* a **preset** (``scope = preset``, id such as ``sitcom``): ``{"name", "genres", "style"}``. The built-in ones are seeded
  once and then belong to the admin, who edits or clones them; seeding never overwrites an edit.
* a **series** (or movie) style (``scope = series``, id ``series/<key>``): ``{"preset": <preset id or "">, "style": {...}}``.
* an **episode** style (``scope = unit``, id the unit's id): ``{"style": {...}}``, the override for one episode.
* the **cards** of an owner's characters.

The layers are only read and written here; laying them on each other is ``subcore.style.resolve_style`` (pure) and the
hash that makes translations stale is ``rules.build_rules``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from subcore.style import PRESETS, CharacterCard, Style, suggest_presets

from .glossaries import MOVIES, SERIES, safe_name
from .rules import StyleLayers
from .store import Store

PRESET, OWNER, UNIT = "preset", "series", "unit"  # style_profiles.scope ("series" covers a movie too)


@dataclass(frozen=True)
class PresetView:
    id: str
    name: str
    genres: tuple[str, ...]
    style: Style
    builtin: bool


def _owner_id(kind: str, key: str) -> str:
    if kind not in (SERIES, MOVIES):
        raise ValueError(f"style belongs to a series or a movie, not {kind!r}")
    return f"{kind}/{safe_name(key)}"


def _preset_id(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    if not slug:
        raise ValueError("a preset needs a name made of letters or digits")
    return slug


class Styles:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- presets ------------------------------------------------------------------------
    def seed_presets(self) -> int:
        """Store the built-in presets that are not stored yet (an edited one is left as the admin made it)."""
        added = 0
        for preset in PRESETS.values():
            if self.store.style_row(PRESET, preset.id) is None:
                self.store.style_save(
                    PRESET, preset.id,
                    {"name": preset.name, "genres": list(preset.genres), "style": preset.style.to_doc(), "builtin": True},
                )
                added += 1
        return added

    @staticmethod
    def _preset_view(row) -> PresetView:
        doc = row.doc
        return PresetView(
            id=row.scope_id, name=str(doc.get("name") or row.scope_id),
            genres=tuple(str(x) for x in doc.get("genres") or ()), style=Style.from_doc(doc.get("style")),
            builtin=bool(doc.get("builtin")),
        )

    def presets(self) -> list[PresetView]:
        return [self._preset_view(r) for r in self.store.style_rows(PRESET)]

    def preset(self, preset_id: str) -> PresetView | None:
        row = self.store.style_row(PRESET, preset_id)
        return self._preset_view(row) if row else None

    def save_preset(
        self, preset_id: str, name: str, style_doc: dict[str, Any], genres: Iterable[str] = (),
    ) -> PresetView:
        """Create or replace a preset. The style is checked first (:class:`subcore.style.StyleError`)."""
        style = Style.from_doc(style_doc, "style")
        existing = self.store.style_row(PRESET, preset_id)
        doc = {
            "name": name.strip() or preset_id, "genres": [g.strip() for g in genres if g and g.strip()],
            "style": style.to_doc(),
        }
        if existing and existing.doc.get("builtin"):
            doc["builtin"] = True
        return self._preset_view(self.store.style_save(PRESET, preset_id, doc))

    def clone_preset(self, preset_id: str, name: str) -> PresetView:
        """A copy of a preset under a new name, to be edited (FR-18.4)."""
        source = self.preset(preset_id)
        if source is None:
            raise LookupError(f"no preset {preset_id!r}")
        new_id = _preset_id(name)
        if self.store.style_row(PRESET, new_id) is not None:
            raise ValueError(f"a preset called {new_id!r} already exists")
        return self.save_preset(new_id, name, source.style.to_doc(), source.genres)

    def delete_preset(self, preset_id: str) -> bool:
        """Only a preset the admin made: a built-in one can be edited, not deleted. A series that named it just loses
        the layer."""
        existing = self.preset(preset_id)
        if existing is None:
            return False
        if existing.builtin:
            raise ValueError("a built-in preset cannot be deleted; edit it instead")
        return self.store.style_delete(PRESET, preset_id)

    def suggest(self, genres: Iterable[str]) -> list[PresetView]:
        """Stored presets that fit TMDB genre names: the built-in rule first, then any preset naming a genre."""
        wanted = [g for g in genres if g]
        by_id = {p.id: p for p in self.presets()}
        order = [pid for pid in suggest_presets(wanted) if pid in by_id]
        folded = {g.strip().casefold() for g in wanted}
        for preset in by_id.values():
            if preset.id not in order and any(g.casefold() in folded for g in preset.genres):
                order.append(preset.id)
        return [by_id[pid] for pid in order]

    # -- the series and episode layers ---------------------------------------------------------
    def owner_style(self, kind: str, key: str) -> tuple[str, Style]:
        """``(preset id, style)`` of a series or movie; ``("", empty)`` when none is set."""
        row = self.store.style_row(OWNER, _owner_id(kind, key))
        if row is None:
            return "", Style()
        return str(row.doc.get("preset") or ""), Style.from_doc(row.doc.get("style"))

    def save_owner_style(self, kind: str, key: str, preset_id: str, style_doc: dict[str, Any]) -> tuple[str, Style]:
        style = Style.from_doc(style_doc, "style")
        if preset_id and self.store.style_row(PRESET, preset_id) is None:
            raise LookupError(f"no preset {preset_id!r}")
        self.store.style_save(OWNER, _owner_id(kind, key), {"preset": preset_id, "style": style.to_doc()})
        return preset_id, style

    def unit_style(self, unit_id: int) -> Style:
        row = self.store.style_row(UNIT, str(unit_id))
        return Style.from_doc(row.doc.get("style")) if row else Style()

    def save_unit_style(self, unit_id: int, style_doc: dict[str, Any]) -> Style:
        style = Style.from_doc(style_doc, "style")
        if style.is_empty():
            self.store.style_delete(UNIT, str(unit_id))
        else:
            self.store.style_save(UNIT, str(unit_id), {"style": style.to_doc()})
        return style

    # -- character cards --------------------------------------------------------------------------
    def cards(self, kind: str, key: str) -> list[CharacterCard]:
        return [CharacterCard.from_doc(r.doc) for r in self.store.cards(kind, safe_name(key))]

    def save_card(self, kind: str, key: str, doc: dict[str, Any], *, replaces: str | None = None) -> CharacterCard:
        card = CharacterCard.from_doc(doc, "card")
        self.store.card_save(kind, safe_name(key), card.name, card.to_doc(), replaces=replaces)
        return card

    def delete_card(self, kind: str, key: str, name: str) -> bool:
        return self.store.card_delete(kind, safe_name(key), name)

    # -- what applies to an episode -----------------------------------------------------------------
    def layers(self, kind: str, key: str, unit_id: int | None = None) -> StyleLayers:
        preset_id, series = self.owner_style(kind, key)
        preset = self.preset(preset_id) if preset_id else None
        return StyleLayers(
            preset=preset.style if preset else None,
            series=series,
            unit=self.unit_style(unit_id) if unit_id is not None else None,
            cards=tuple(self.cards(kind, key)),
        )
