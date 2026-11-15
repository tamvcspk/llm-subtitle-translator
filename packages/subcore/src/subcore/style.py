"""Style profile: how a translation should sound, as data (FR-18, ADR-14).

A :class:`Style` is a set of axes (formality, tone, profanity, localisation, dialect, anime honorifics), subtitle
constraints (characters per second, line length, number of lines) and a free note. Every field may be unset, so a
style is one *layer*; :func:`resolve_style` lays several layers on top of each other, the highest winning field by
field (FR-18.1). The glossary is not a layer: it always wins in the prompt, whatever the style says.

A :class:`CharacterCard` is the voice of one character: how they speak, how they address others, catchphrases,
a few sample lines, and the axes where they differ from the series (the card is the highest layer, FR-18.1).

Everything here is pure: plain data in, plain data out, no I/O (ADR-2). The worker keeps the layers in its
database; the engine only ever sees the result in a bundle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any, Iterable, Mapping

__all__ = [
    "AXES",
    "CharacterCard",
    "PRESETS",
    "Preset",
    "Style",
    "StyleError",
    "cards_present",
    "language_style",
    "render_style",
    "render_voices",
    "resolve_style",
    "suggest_presets",
]


class StyleError(ValueError):
    """A style or character card document is malformed. The message names the offending field."""


# axis -> allowed values. ``tone`` takes any short text: the useful ones are open-ended ("dry", "warm", ...).
AXES: dict[str, tuple[str, ...]] = {
    "formality": ("casual", "neutral", "formal"),
    "profanity": ("keep", "soften", "remove"),
    "localization": ("literal", "balanced", "adapt"),
    "dialect": ("north", "south", "neutral"),
    "honorifics": ("keep", "translate"),
}
_TEXT_FIELDS = ("tone", "notes")
_INT_FIELDS = {"max_cps": (1, 60), "max_line_length": (10, 120), "max_lines": (1, 4)}
_MAX_TEXT = 2000


def _clean_text(value: Any, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise StyleError(f"{where}: expected text, got {type(value).__name__}")
    text = str(value).strip()
    if len(text) > _MAX_TEXT:
        raise StyleError(f"{where}: longer than {_MAX_TEXT} characters")
    return text


@dataclass(frozen=True)
class Style:
    """One layer of style. ``None`` (or empty text) means "this layer says nothing about it"."""

    formality: str | None = None
    tone: str | None = None
    profanity: str | None = None
    localization: str | None = None
    dialect: str | None = None
    honorifics: str | None = None
    max_cps: int | None = None
    max_line_length: int | None = None
    max_lines: int | None = None
    allow_condense: bool | None = None
    notes: str = ""

    # -- document ------------------------------------------------------------
    @classmethod
    def from_doc(cls, data: Any, where: str = "style") -> "Style":
        """Read a style document (a mapping). Unknown keys are refused so a typo cannot silently do nothing."""
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise StyleError(f"{where}: expected a mapping")
        known = {f.name for f in fields(cls)}
        extra = sorted(set(data) - known)
        if extra:
            raise StyleError(f"{where}: unknown field {extra[0]!r}")
        values: dict[str, Any] = {}
        for axis, allowed in AXES.items():
            raw = data.get(axis)
            if raw in (None, ""):
                continue
            if raw not in allowed:
                raise StyleError(f"{where}.{axis}: must be one of {', '.join(allowed)}, got {raw!r}")
            values[axis] = raw
        tone = _clean_text(data.get("tone"), f"{where}.tone")
        if tone:
            values["tone"] = tone
        for name, (low, high) in _INT_FIELDS.items():
            raw = data.get(name)
            if raw in (None, ""):
                continue
            if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
                raise StyleError(f"{where}.{name}: expected a whole number")
            try:
                number = int(raw)
            except (TypeError, ValueError):
                raise StyleError(f"{where}.{name}: expected a whole number, got {raw!r}") from None
            if not low <= number <= high:
                raise StyleError(f"{where}.{name}: must be between {low} and {high}, got {number}")
            values[name] = number
        condense = data.get("allow_condense")
        if condense not in (None, ""):
            if not isinstance(condense, bool):
                raise StyleError(f"{where}.allow_condense: expected true or false")
            values["allow_condense"] = condense
        return cls(notes=_clean_text(data.get("notes"), f"{where}.notes"), **values)

    def to_doc(self) -> dict[str, Any]:
        """Only the fields this layer sets, in a stable order."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None or value == "":
                continue
            out[f.name] = value
        return out

    def is_empty(self) -> bool:
        return not self.to_doc()


def resolve_style(layers: Iterable[Style | None]) -> Style:
    """Lay ``layers`` (lowest first) on top of each other: for each field the highest layer that sets it wins.

    Notes are the one exception, since a note of the series ("a family sitcom") and one of an episode ("a flashback")
    both stay true: they are joined in layer order, each distinct note once."""
    merged: dict[str, Any] = {}
    notes: list[str] = []
    for layer in layers:
        if layer is None:
            continue
        for name, value in layer.to_doc().items():
            if name == "notes":
                if value not in notes:
                    notes.append(value)
            else:
                merged[name] = value
    return Style(notes="\n".join(notes), **merged)


# ---------------------------------------------------------------- language layer


# The lowest layer (FR-18.1): what the target language itself asks for. It is part of the prompt version, not of the
# rules hash, so a change here is a new prompt (a new ``PROMPT_VERSION``), never a flag on every translation.
_LANGUAGE_STYLES: dict[str, Style] = {
    "vi": Style(dialect="neutral", localization="balanced", max_lines=2),
}


def language_style(lang: str) -> Style:
    return _LANGUAGE_STYLES.get((lang or "").lower().split("-")[0], Style())


# ---------------------------------------------------------------- presets (FR-18.4)


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    style: Style
    # TMDB genre names (English) that suggest this preset.
    genres: tuple[str, ...] = ()


PRESETS: dict[str, Preset] = {
    p.id: p
    for p in (
        Preset(
            "sitcom", "Sitcom",
            Style(formality="casual", tone="humorous, quick", localization="adapt", profanity="soften",
                  max_cps=17, notes="Jokes and wordplay land in Vietnamese before they stay literal."),
            ("Comedy",),
        ),
        Preset(
            "drama", "Drama",
            Style(formality="neutral", tone="serious", localization="balanced", profanity="keep", max_cps=17,
                  notes="Keep subtext and emotional restraint; do not over-explain."),
            ("Drama", "Crime", "Mystery", "Thriller", "War & Politics"),
        ),
        Preset(
            "anime", "Anime",
            Style(formality="casual", tone="expressive", localization="balanced", honorifics="keep", max_cps=17,
                  notes="Keep Japanese honorifics and names in their original form."),
            ("Animation", "Anime"),
        ),
        Preset(
            "documentary", "Documentary",
            Style(formality="formal", tone="informative, calm", localization="literal", profanity="keep",
                  max_cps=17, notes="Prefer exact terms; keep proper names and numbers unchanged."),
            ("Documentary", "News"),
        ),
        Preset(
            "kids", "Kids",
            Style(formality="casual", tone="friendly, simple", localization="adapt", profanity="remove",
                  max_cps=13, max_line_length=36, notes="Short simple words a child can read."),
            ("Kids", "Family"),
        ),
    )
}


_SPECIFICITY = ["anime", "kids", "documentary", "sitcom", "drama"]


def suggest_presets(genres: Iterable[str]) -> list[str]:
    """Preset ids that fit TMDB genre names, most specific first (anime before drama). Pure; unknown genres are
    ignored and the answer may be empty."""
    wanted = {g.strip().casefold() for g in genres if g and g.strip()}
    found = [p.id for p in PRESETS.values() if any(g.casefold() in wanted for g in p.genres)]
    # "Animation" with "Drama" is an anime, not a drama: the narrow genres come before the broad ones.
    return sorted(found, key=_SPECIFICITY.index)


# ---------------------------------------------------------------- character cards (FR-18.3)


@dataclass(frozen=True)
class CharacterCard:
    name: str
    aliases: tuple[str, ...] = ()
    speech: str = ""  # how they speak: fast, sarcastic, childish, ...
    # (to whom, the form) pairs, e.g. ("Bob", "anh–em"); the glossary's address rules still win
    address: tuple[tuple[str, str], ...] = ()
    catchphrases: tuple[str, ...] = ()
    samples: tuple[str, ...] = ()
    style: Style = field(default_factory=Style)  # axes where this character differs from the series
    always: bool = False  # in the prompt of every episode, not only where the name is mentioned

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    @classmethod
    def from_doc(cls, data: Any, where: str = "card") -> "CharacterCard":
        if not isinstance(data, Mapping):
            raise StyleError(f"{where}: expected a mapping")
        known = {"name", "aliases", "speech", "address", "catchphrases", "samples", "style", "always"}
        extra = sorted(set(data) - known)
        if extra:
            raise StyleError(f"{where}: unknown field {extra[0]!r}")
        name = _clean_text(data.get("name"), f"{where}.name")
        if not name:
            raise StyleError(f"{where}.name: must not be empty")

        def texts(key: str) -> tuple[str, ...]:
            raw = data.get(key)
            if raw in (None, ""):
                return ()
            if not isinstance(raw, list):
                raise StyleError(f"{where}.{key}: expected a list")
            return tuple(t for t in (_clean_text(item, f"{where}.{key}[{n}]") for n, item in enumerate(raw)) if t)

        address: list[tuple[str, str]] = []
        raw_address = data.get("address") or []
        if not isinstance(raw_address, list):
            raise StyleError(f"{where}.address: expected a list")
        for n, item in enumerate(raw_address):
            if not isinstance(item, Mapping):
                raise StyleError(f"{where}.address[{n}]: expected a mapping with 'to' and 'form'")
            to, form = _clean_text(item.get("to"), f"{where}.address[{n}].to"), _clean_text(item.get("form"), f"{where}.address[{n}].form")
            if not to or not form:
                raise StyleError(f"{where}.address[{n}]: needs both 'to' and 'form'")
            address.append((to, form))
        always = data.get("always", False)
        if not isinstance(always, bool):
            raise StyleError(f"{where}.always: expected true or false")
        return cls(
            name=name, aliases=texts("aliases"), speech=_clean_text(data.get("speech"), f"{where}.speech"),
            address=tuple(address), catchphrases=texts("catchphrases"), samples=texts("samples"),
            style=Style.from_doc(data.get("style"), f"{where}.style"), always=always,
        )

    def to_doc(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.aliases:
            out["aliases"] = list(self.aliases)
        if self.speech:
            out["speech"] = self.speech
        if self.address:
            out["address"] = [{"to": to, "form": form} for to, form in self.address]
        if self.catchphrases:
            out["catchphrases"] = list(self.catchphrases)
        if self.samples:
            out["samples"] = list(self.samples)
        if not self.style.is_empty():
            out["style"] = self.style.to_doc()
        if self.always:
            out["always"] = True
        return out


def _word(needle: str, haystack: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, re.IGNORECASE) is not None


def cards_present(cards: Iterable[CharacterCard], texts: Iterable[str]) -> list[CharacterCard]:
    """The cards that belong in the prompt of an episode: characters the dialogue names, and ``always`` ones.

    A source subtitle has no speaker labels, so "present" can only mean "named in the dialogue" (FR-18.3)."""
    haystack = "\n".join(texts)
    return [c for c in cards if c.always or any(_word(n, haystack) for n in c.names)]


# ---------------------------------------------------------------- rendering


_AXIS_LINES = {
    "formality": "Formality: {}",
    "tone": "Tone: {}",
    "profanity": "Profanity: {}",
    "localization": "Idioms and wordplay: {}",
    "dialect": "Dialect: {}",
    "honorifics": "Honorifics: {}",
}
_AXIS_MEANING = {
    ("formality", "casual"): "casual, spoken register",
    ("formality", "neutral"): "neutral register",
    ("formality", "formal"): "formal register",
    ("profanity", "keep"): "keep the strength of swearing as in the source",
    ("profanity", "soften"): "soften strong swearing",
    ("profanity", "remove"): "leave swearing out",
    ("localization", "literal"): "stay close to the source wording",
    ("localization", "balanced"): "natural wording, keep idioms recognisable",
    ("localization", "adapt"): "replace idioms and wordplay with natural target-language equivalents",
    ("dialect", "north"): "Northern Vietnamese vocabulary",
    ("dialect", "south"): "Southern Vietnamese vocabulary",
    ("dialect", "neutral"): "neutral vocabulary, no regional slang",
    ("honorifics", "keep"): "keep honorifics such as -san and -kun",
    ("honorifics", "translate"): "translate honorifics into forms of address",
}


def _axis_text(name: str, value: Any) -> str:
    return _AXIS_LINES[name].format(_AXIS_MEANING.get((name, value), value))


def render_style(style: Style) -> str:
    """The *Effective style* section (ADR-14 point 4); empty text when the style says nothing."""
    if style.is_empty():
        return ""
    lines = ["How the translation should sound (the glossary still wins where they disagree):"]
    for name in _AXIS_LINES:
        value = getattr(style, name)
        if value:
            lines.append(f"- {_axis_text(name, value)}")
    limits = []
    if style.max_cps:
        limits.append(f"at most {style.max_cps} characters per second of display time")
    if style.max_line_length:
        limits.append(f"lines of at most {style.max_line_length} characters")
    if style.max_lines:
        limits.append(f"at most {style.max_lines} line(s) per item")
    if limits:
        lines.append("- Subtitle limits: " + "; ".join(limits))
    if style.allow_condense is not None:
        lines.append(
            "- Fast dialogue: " + ("shorten wording to fit the limits" if style.allow_condense
                                   else "never drop meaning to fit the limits"),
        )
    if style.notes:
        lines.extend(f"- {line}" for line in style.notes.splitlines() if line.strip())
    return "\n".join(lines)


def render_voices(cards: Iterable[CharacterCard]) -> str:
    """The *Character voices* section: one block per card; empty text without cards."""
    blocks: list[str] = []
    for card in cards:
        lines = [f"- {card.name}" + (f" (also: {', '.join(card.aliases)})" if card.aliases else "")]
        if card.speech:
            lines.append(f"  Speaks: {card.speech}")
        for to, form in card.address:
            lines.append(f'  Addresses {to}: "{form}"')
        if card.catchphrases:
            lines.append("  Catchphrases: " + "; ".join(f'"{c}"' for c in card.catchphrases))
        if card.samples:
            lines.append("  Sample lines: " + "; ".join(f'"{s}"' for s in card.samples))
        for name in _AXIS_LINES:
            value = getattr(card.style, name)
            if value:
                lines.append(f"  {_axis_text(name, value)}")
        if card.style.notes:
            lines.append(f"  Note: {card.style.notes}")
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return "How these characters speak (where this differs from the style above, follow the character):\n" + "\n".join(blocks)
