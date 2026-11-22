"""Answers of the Style page's routes (``api/style_api.py``): a style, genre presets, character cards, the trial of a style."""
from __future__ import annotations

from typing import Literal

from . import Answer, maybe
from .common import StyleImpact


class Style(Answer):
    """Every field is optional: an axis left out inherits from the layer below (language < preset < series < episode < card)."""

    formality: Literal["casual", "neutral", "formal"] = maybe()
    tone: str = maybe()
    profanity: Literal["keep", "soften", "remove"] = maybe()
    localization: Literal["literal", "balanced", "adapt"] = maybe()
    dialect: Literal["north", "south", "neutral"] = maybe()
    honorifics: Literal["keep", "translate"] = maybe()
    max_cps: int = maybe()
    max_line_length: int = maybe()
    max_lines: int = maybe()
    allow_condense: bool = maybe()
    notes: str = maybe()


class Preset(Answer):
    id: str
    name: str
    genres: list[str]
    style: Style
    builtin: bool


class CardAddress(Answer):
    to: str
    form: str


class CharacterCard(Answer):
    """The Voice of a character (UX §6.3): how they speak and where their style differs from the series'."""

    name: str
    aliases: list[str] = maybe()
    speech: str = maybe()
    address: list[CardAddress] = maybe()
    """Older cards may hold ``(to, form)`` pairs; the form never writes them (the glossary's Forms of address do)."""
    catchphrases: list[str] = maybe()
    samples: list[str] = maybe()
    style: Style = maybe()
    always: bool = maybe()


class OwnerHeader(Answer):
    id: int
    title: str


class StyleOverview(Answer):
    """``GET /api/series/{id}/style`` (names the owner under ``series``) and ``GET /api/movies/{id}/style`` (under ``movie``)."""

    series: OwnerHeader = maybe()
    movie: OwnerHeader = maybe()
    lang: str
    preset: str
    style: Style
    effective: Style
    """What is in force once the language and the preset are laid under the owner's own style."""
    presets: list[Preset]
    cards: list[CharacterCard]
    """The voice of each character that has one (``PUT …/cards``)."""


class StyleSuggestions(Answer):
    """The genres TMDB gives the series, and the presets that fit them, best first."""

    genres: list[str]
    presets: list[Preset]


class PresetsAnswer(Answer):
    items: list[Preset]


class PresetSaved(Preset):
    """An edited preset, with what the edit flags stale (``owners`` is how many series and movies use it). With
    ``dry_run`` only the counts come back (``PresetImpact``)."""

    ok: bool
    saved: bool
    stale_now: int
    stale_after: int
    would_stale: int
    owners: int


class PresetImpact(StyleImpact):
    """A dry run, or a delete of a preset: the counts only."""

    owners: int


class CardSaved(StyleImpact):
    card: CharacterCard


class CardDeleted(Answer):
    ok: bool
    stale_now: int
    stale_after: int
    would_stale: int


class CharacterSaved(Answer):
    ok: bool
    saved: bool


class UnitStyleAnswer(Answer):
    """``GET /api/units/{id}/style``: the episode's own override, the layer above the series'."""

    style: Style


class UnitStyleSaved(StyleImpact):
    style: Style


class PreviewRow(Answer):
    n: int
    source: str
    current: str | None
    """What the current translation says; ``null`` when there is none."""
    preview: str


class StylePreview(Answer):
    """The trial of a style: sample cues translated with a style that need not be saved. Writes nothing."""

    ok: bool
    unit_id: int
    lang: str
    model: str
    rows: list[PreviewRow]
    effective: Style
    voices: list[str]
    tokens_in: int
    tokens_out: int
    bundle_hash: str
    style_hash: str
