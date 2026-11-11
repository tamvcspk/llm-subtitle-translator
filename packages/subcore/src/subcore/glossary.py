"""Glossary: human-owned YAML rules per series + target language (FR-4).

Schema (all sections optional)::

    version: 1
    target_lang: vi
    style: "Informal, keep honorifics"
    terms:
      - source: Kyber crystal
        target: tinh thể Kyber
        aliases: [Kyber crystals]     # extra source spellings
        locked: true                  # output MUST contain `target` for cues containing `source`
        note: "object"
    characters:
      - name: Alice
        target: Alice
        gender: f
        description: "lead, 20s"
    address_rules:
      - a: Alice
        b: Bob
        a_to_b: "anh–em"              # "self–other" when A speaks to B
        b_to_a: "em–anh"              # optional, same format for B speaking to A
        from: "S01E06 00:32:10"       # inclusive; time part optional
        until: "S02E01"               # exclusive; optional
        note: "after the betrayal"

``from``/``until`` positions are ``S<season>E<episode>`` with an optional
``HH:MM:SS[.mmm]`` time inside that episode. The glossary always wins over
anything the machine infers (FR-4.3).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import srt

__all__ = [
    "Glossary",
    "GlossaryError",
    "Term",
    "Character",
    "AddressRule",
    "Position",
    "ActiveRule",
    "load",
    "loads",
    "dumps",
    "merge",
    "locked_violations",
]


class GlossaryError(ValueError):
    """The glossary file is malformed. The message names the offending path."""


_POS_RE = re.compile(
    r"^\s*S(\d+)E(\d+)(?:\s+(\d+):(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?)?\s*$", re.IGNORECASE
)
_END_OF_EPISODE = 1 << 60


@dataclass(frozen=True, order=True)
class Position:
    """A point in story order: (season, episode, ms into the episode)."""

    season: int
    episode: int
    ms: int = 0
    has_time: bool = field(default=False, compare=False)

    @classmethod
    def parse(cls, value: Any, where: str) -> "Position":
        match = _POS_RE.match(str(value))
        if not match:
            raise GlossaryError(f"{where}: expected 'S01E06' or 'S01E06 00:32:10', got {value!r}")
        season, episode, h, m, s, frac = match.groups()
        if h is None:
            return cls(int(season), int(episode), 0, False)
        ms = ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int((frac or "0").ljust(3, "0"))
        return cls(int(season), int(episode), ms, True)

    def __str__(self) -> str:
        base = f"S{self.season:02d}E{self.episode:02d}"
        if not self.has_time:
            return base
        total_s, ms = divmod(self.ms, 1000)
        h, rem = divmod(total_s, 3600)
        m, s = divmod(rem, 60)
        return f"{base} {h:02d}:{m:02d}:{s:02d}" + (f".{ms:03d}" if ms else "")


@dataclass(frozen=True)
class Term:
    source: str
    target: str
    aliases: tuple[str, ...] = ()
    locked: bool = False
    note: str = ""

    @property
    def sources(self) -> tuple[str, ...]:
        return (self.source, *self.aliases)


@dataclass(frozen=True)
class Character:
    name: str
    target: str = ""
    gender: str = ""
    description: str = ""


@dataclass(frozen=True)
class AddressRule:
    a: str
    b: str
    a_to_b: str = ""
    b_to_a: str = ""
    start: Position | None = None
    until: Position | None = None
    note: str = ""


@dataclass(frozen=True)
class ActiveRule:
    """An address rule as it applies to one episode.

    ``from_cue``/``until_cue`` are cue *ordinals* (the ``i`` the model sees); ``None``
    means "from the start" / "to the end" of this episode.
    """

    rule: AddressRule
    from_cue: int | None = None
    until_cue: int | None = None


@dataclass(frozen=True)
class Glossary:
    target_lang: str = ""
    style: str = ""
    terms: tuple[Term, ...] = ()
    characters: tuple[Character, ...] = ()
    address_rules: tuple[AddressRule, ...] = ()

    def is_empty(self) -> bool:
        return not (self.style or self.terms or self.characters or self.address_rules)

    def locked_terms(self) -> list[Term]:
        return [t for t in self.terms if t.locked]

    def active_address_rules(
        self, season: int | None, episode: int | None, cues: list[srt.Cue] | None = None
    ) -> list[ActiveRule]:
        """Rules in effect during the given episode (all rules if the episode is unknown)."""
        if season is None or episode is None:
            return [ActiveRule(rule) for rule in self.address_rules]
        ep_start = Position(season, episode, 0)
        ep_end = Position(season, episode, _END_OF_EPISODE)
        active: list[ActiveRule] = []
        for rule in self.address_rules:
            if rule.start is not None and rule.start >= ep_end:
                continue
            if rule.until is not None and rule.until <= ep_start:
                continue
            from_cue = until_cue = None
            if rule.start is not None and rule.start > ep_start:
                from_cue = _cue_at(cues, rule.start.ms)
            if rule.until is not None and rule.until < ep_end:
                until_cue = _cue_at(cues, rule.until.ms)
            active.append(ActiveRule(rule, from_cue, until_cue))
        return active


def _cue_at(cues: list[srt.Cue] | None, ms: int) -> int | None:
    """Ordinal (1-based) of the first cue starting at or after ``ms``."""
    if not cues:
        return None
    for ordinal, cue in enumerate(cues, start=1):
        if cue.start_ms >= ms:
            return ordinal
    return len(cues) + 1


# ---------------------------------------------------------------- loading


def _need_str(entry: dict[str, Any], key: str, where: str, *, required: bool = True) -> str:
    value = entry.get(key)
    if value is None:
        if required:
            raise GlossaryError(f"{where}: missing required field '{key}'")
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise GlossaryError(f"{where}.{key}: expected text, got {type(value).__name__}")
    text = str(value).strip()
    if required and not text:
        raise GlossaryError(f"{where}.{key}: must not be empty")
    return text


def _entries(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = data.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise GlossaryError(f"{key}: expected a list")
    for n, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise GlossaryError(f"{key}[{n}]: expected a mapping")
    return raw


def loads(text: str) -> Glossary:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GlossaryError(f"invalid YAML: {exc}") from exc
    if data is None:
        return Glossary()
    if not isinstance(data, dict):
        raise GlossaryError("top level must be a mapping")
    version = data.get("version", 1)
    if version != 1:
        raise GlossaryError(f"unsupported glossary version {version!r}")

    terms = []
    for n, entry in enumerate(_entries(data, "terms")):
        where = f"terms[{n}]"
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise GlossaryError(f"{where}.aliases: expected a list of non-empty text")
        locked = entry.get("locked", False)
        if not isinstance(locked, bool):
            raise GlossaryError(f"{where}.locked: expected true/false")
        terms.append(
            Term(
                source=_need_str(entry, "source", where),
                target=_need_str(entry, "target", where),
                aliases=tuple(a.strip() for a in aliases),
                locked=locked,
                note=_need_str(entry, "note", where, required=False),
            )
        )

    characters = []
    for n, entry in enumerate(_entries(data, "characters")):
        where = f"characters[{n}]"
        characters.append(
            Character(
                name=_need_str(entry, "name", where),
                target=_need_str(entry, "target", where, required=False),
                gender=_need_str(entry, "gender", where, required=False),
                description=_need_str(entry, "description", where, required=False),
            )
        )

    rules = []
    for n, entry in enumerate(_entries(data, "address_rules")):
        where = f"address_rules[{n}]"
        a_to_b = _need_str(entry, "a_to_b", where, required=False)
        b_to_a = _need_str(entry, "b_to_a", where, required=False)
        if not (a_to_b or b_to_a):
            raise GlossaryError(f"{where}: needs 'a_to_b' and/or 'b_to_a'")
        start = Position.parse(entry["from"], f"{where}.from") if entry.get("from") is not None else None
        until = Position.parse(entry["until"], f"{where}.until") if entry.get("until") is not None else None
        if start and until and until <= start:
            raise GlossaryError(f"{where}: 'until' ({until}) must be after 'from' ({start})")
        rules.append(
            AddressRule(
                a=_need_str(entry, "a", where),
                b=_need_str(entry, "b", where),
                a_to_b=a_to_b,
                b_to_a=b_to_a,
                start=start,
                until=until,
                note=_need_str(entry, "note", where, required=False),
            )
        )

    return Glossary(
        target_lang=_need_str(data, "target_lang", "glossary", required=False),
        style=_need_str(data, "style", "glossary", required=False),
        terms=tuple(terms),
        characters=tuple(characters),
        address_rules=tuple(rules),
    )


def load(path: str | os.PathLike[str]) -> Glossary:
    return loads(Path(path).read_text(encoding="utf-8-sig"))


def dumps(glossary: Glossary) -> str:
    """Render back to the YAML schema above (stable key order, UTF-8 friendly)."""
    data: dict[str, Any] = {"version": 1}
    if glossary.target_lang:
        data["target_lang"] = glossary.target_lang
    if glossary.style:
        data["style"] = glossary.style
    if glossary.terms:
        data["terms"] = []
        for t in glossary.terms:
            item: dict[str, Any] = {"source": t.source, "target": t.target}
            if t.aliases:
                item["aliases"] = list(t.aliases)
            if t.locked:
                item["locked"] = True
            if t.note:
                item["note"] = t.note
            data["terms"].append(item)
    if glossary.characters:
        data["characters"] = [
            {k: v for k, v in (("name", c.name), ("target", c.target), ("gender", c.gender), ("description", c.description)) if v}
            for c in glossary.characters
        ]
    if glossary.address_rules:
        data["address_rules"] = []
        for r in glossary.address_rules:
            item = {"a": r.a, "b": r.b}
            if r.a_to_b:
                item["a_to_b"] = r.a_to_b
            if r.b_to_a:
                item["b_to_a"] = r.b_to_a
            if r.start:
                item["from"] = str(r.start)
            if r.until:
                item["until"] = str(r.until)
            if r.note:
                item["note"] = r.note
            data["address_rules"].append(item)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def merge(*glossaries: Glossary) -> Glossary:
    """Combine glossaries; later ones win on conflicts.

    Terms merge by case-insensitive ``source``, characters by ``name``, address rules by
    ``(a, b, from)``. A non-empty ``style``/``target_lang`` from a later glossary replaces
    the earlier one.
    """
    terms: dict[str, Term] = {}
    characters: dict[str, Character] = {}
    rules: dict[tuple[str, str, str], AddressRule] = {}
    style = target_lang = ""
    for g in glossaries:
        style = g.style or style
        target_lang = g.target_lang or target_lang
        for t in g.terms:
            terms[t.source.casefold()] = t
        for c in g.characters:
            characters[c.name.casefold()] = c
        for r in g.address_rules:
            rules[(r.a.casefold(), r.b.casefold(), str(r.start or ""))] = r
    return Glossary(
        target_lang=target_lang,
        style=style,
        terms=tuple(terms.values()),
        characters=tuple(characters.values()),
        address_rules=tuple(rules.values()),
    )


# ---------------------------------------------------------------- checks


def _contains_word(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, re.IGNORECASE) is not None


def locked_violations(glossary: Glossary, source: str, translated: str) -> list[Term]:
    """Locked terms present in ``source`` whose ``target`` is missing from ``translated`` (FR-4.2)."""
    out = []
    folded = translated.casefold()
    for term in glossary.locked_terms():
        if any(_contains_word(source, s) for s in term.sources) and term.target.casefold() not in folded:
            out.append(term)
    return out
