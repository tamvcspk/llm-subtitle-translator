"""The translation bundle: everything one translation reads, as plain data (FR-21.1, FR-21.6, ADR-16).

A bundle holds exactly what ``translate.prompt.render_system`` and the request planner use, so that the
same bundle gives the same prompt and the same output in the worker and in the CLI (NFR-12). It has no
API key, no machine path and no database id. Few-shot examples are stored by value, never as references.

``bundle_hash`` covers the whole serialised bundle, so it changes exactly when the prompt or a parameter
changes. Anything that only *describes* a bundle for a screen (which glossary entry matches cue 142) is
not a field here: ``matches`` reads a bundle and returns that description, and nothing it returns is hashed.

Format: ``bundle_version`` 2 (1 had no style or voices). ``from_dict`` accepts any version up to :data:`BUNDLE_VERSION`, fills
optional fields that an older writer did not have, and ignores fields it does not know, so a bundle
written by an earlier build still runs (FR-21.1, criterion 18h).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from . import matching, srt
from .glossary import AddressRule, ActiveRule, Glossary, dumps as dump_glossary, loads as load_glossary
from .style import CharacterCard, Style, StyleError

__all__ = ["BUNDLE_VERSION", "Params", "TranslationBundle", "BundleError", "CueMatch", "matches"]

# 2: the effective style and the character voices (P12). A version-1 bundle reads as one with no style.
BUNDLE_VERSION = 2


class BundleError(ValueError):
    """The bundle is not one this build can read."""


def _optional_int(value: Any, name: str, minimum: int) -> int | None:
    """``None`` stays unset; anything else must be a whole number (JSON ``2``, not ``"2"``, ``2.5`` or ``true``)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BundleError(f"{name} must be a whole number, got {value!r}")
    if value < minimum:
        raise BundleError(f"{name} must be at least {minimum}, got {value}")
    return value


def _cue_number(value: Any, name: str) -> int:
    number = _optional_int(value, name, 1)
    if number is None:
        raise BundleError(f"{name} holds a cue number that is null")
    return number


def _params_of(raw: Any) -> "Params":
    """Read the ``params`` object with its types checked, so a bundle edited by hand fails here, before any call is
    paid for, and not in the middle of a run."""
    if raw is None:
        return Params()
    if not isinstance(raw, dict):
        raise BundleError("params is a JSON object")
    defaults = Params()
    values: dict[str, Any] = {}
    for name in ("model", "prompt_version"):
        value = raw.get(name, getattr(defaults, name))
        if not isinstance(value, str):
            raise BundleError(f"params.{name} must be text, got {value!r}")
        values[name] = value
    temperature = raw.get("temperature", defaults.temperature)
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or temperature < 0:
            raise BundleError(f"params.temperature must be a number, 0 or more, got {temperature!r}")
        temperature = float(temperature)
    values["temperature"] = temperature
    factor = raw.get("output_factor", defaults.output_factor)
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or factor <= 0:
        raise BundleError(f"params.output_factor must be a number above 0, got {factor!r}")
    values["output_factor"] = float(factor)
    for name, minimum in (
        ("max_output_tokens", 0), ("min_part_cues", 1), ("repair_rounds", 0), ("context_window", 0),
        ("max_split_depth", 0), ("glossary_filter_threshold", 0),
    ):
        number = _optional_int(raw.get(name, getattr(defaults, name)), f"params.{name}", minimum)
        values[name] = getattr(defaults, name) if number is None else number
    return Params(**values)


@dataclass(frozen=True)
class Params:
    """Model and planning parameters. They change the requests, so they are part of the hash.

    ``model`` empty and ``max_output_tokens`` 0 mean "not decided": whoever runs the bundle uses its provider's model
    and output limit. They are then left out of the serialised bundle and of its hash (D6), so a bundle written without
    a provider keeps the same hash when it is run, whatever the provider turns out to be."""

    model: str = ""
    prompt_version: str = ""
    temperature: float | None = None
    max_output_tokens: int = 0
    output_factor: float = 1.3
    min_part_cues: int = 100
    repair_rounds: int = 2
    context_window: int = 5
    max_split_depth: int = 1
    # More glossary terms than this: each request lists only the terms its lines mention (FR-4.3). 0 = never filter.
    glossary_filter_threshold: int = 50


@dataclass(frozen=True)
class TranslationBundle:
    cues: tuple[srt.Cue, ...] = ()
    target_lang: str = "vi"
    target_name: str = ""
    source_lang: str = ""
    title: str = ""
    overview: str = ""
    season: int | None = None
    episode: int | None = None
    # Already resolved for the episode: terms outside their window are gone, corrections are merged in.
    glossary: Glossary = field(default_factory=Glossary)
    active_rules: tuple[ActiveRule, ...] = ()
    # The effective style of the episode, every layer already laid on top of each other (FR-18.1), and the cards of
    # the characters the dialogue names (FR-18.3). The glossary is not part of either: it always wins in the prompt.
    style: Style = field(default_factory=Style)
    voices: tuple[CharacterCard, ...] = ()
    # Extra (title, body) system sections: corrections, story memory. Placed before metadata.
    sections: tuple[tuple[str, str], ...] = ()
    # Accepted (source, translation) pairs, embedded by value (FR-21.6). Rendered last in the system prompt.
    few_shot: tuple[tuple[str, str], ...] = ()
    # Ordinals (1-based) to translate; None = every cue with text.
    requested: tuple[int, ...] | None = None
    # Ordinal -> final text: copied to the output and shown to the model as reference, never requested.
    keep: dict[int, str] = field(default_factory=dict)
    params: Params = field(default_factory=Params)
    bundle_version: int = BUNDLE_VERSION

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "prompt_version": self.params.prompt_version,
            "temperature": self.params.temperature,
            "output_factor": self.params.output_factor,
            "min_part_cues": self.params.min_part_cues,
            "repair_rounds": self.params.repair_rounds,
            "context_window": self.params.context_window,
            "max_split_depth": self.params.max_split_depth,
            "glossary_filter_threshold": self.params.glossary_filter_threshold,
        }
        if self.params.model:  # not decided yet: no key, so the hash does not depend on a value nobody chose
            params["model"] = self.params.model
        if self.params.max_output_tokens:
            params["max_output_tokens"] = self.params.max_output_tokens
        return {
            "bundle_version": BUNDLE_VERSION,
            "params": params,
            "language": {"target": self.target_lang, "target_name": self.target_name, "source": self.source_lang},
            "episode": {
                "title": self.title, "overview": self.overview, "season": self.season, "episode": self.episode,
            },
            "glossary": dump_glossary(self.glossary),
            "active_rules": [
                {
                    "a": ar.rule.a, "b": ar.rule.b, "a_to_b": ar.rule.a_to_b, "b_to_a": ar.rule.b_to_a,
                    "note": ar.rule.note, "from_cue": ar.from_cue, "until_cue": ar.until_cue,
                }
                for ar in self.active_rules
            ],
            "style": self.style.to_doc(),
            "voices": [card.to_doc() for card in self.voices],
            "sections": [{"title": t, "body": b} for t, b in self.sections],
            "few_shot": [{"source": s, "target": t} for s, t in self.few_shot],
            "cues": [
                {"index": c.index, "timing": c.timing, "start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text}
                for c in self.cues
            ],
            "requested": None if self.requested is None else list(self.requested),
            "keep": [{"i": n, "text": text} for n, text in sorted(self.keep.items())],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def bundle_hash(self) -> str:
        """SHA-256 of the canonical JSON. Stable across processes: the same bundle hashes the same everywhere."""
        canonical = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Any) -> "TranslationBundle":
        try:
            return cls._from_dict(data)
        except BundleError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise BundleError(f"damaged bundle ({type(exc).__name__}: {exc})") from None

    @classmethod
    def _from_dict(cls, data: Any) -> "TranslationBundle":
        if not isinstance(data, dict):
            raise BundleError("a bundle is a JSON object")
        version = data.get("bundle_version", 1)
        if not isinstance(version, int) or version < 1:
            raise BundleError(f"bundle_version must be a positive integer, got {version!r}")
        if version > BUNDLE_VERSION:
            raise BundleError(f"bundle_version {version} is newer than this build reads ({BUNDLE_VERSION})")

        p = _params_of(data.get("params"))
        language = data.get("language") or {}
        episode = data.get("episode") or {}
        if not isinstance(language, dict) or not isinstance(episode, dict):
            raise BundleError("language and episode are JSON objects")
        season = _optional_int(episode.get("season"), "episode.season", 0)
        episode_no = _optional_int(episode.get("episode"), "episode.episode", 0)
        try:
            cues = tuple(
                srt.Cue(
                    index=int(c["index"]), start_ms=int(c["start_ms"]), end_ms=int(c["end_ms"]),
                    text=str(c["text"]), timing=str(c.get("timing", "")),
                )
                for c in data.get("cues") or []
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BundleError(f"bad cue in bundle: {exc}") from None

        glossary_text = data.get("glossary") or ""
        try:
            glossary = load_glossary(glossary_text) if glossary_text.strip() else Glossary()
        except Exception as exc:  # GlossaryError, a ValueError subclass
            raise BundleError(f"bad glossary in bundle: {exc}") from None

        active = tuple(
            ActiveRule(
                AddressRule(
                    a=str(r.get("a", "")), b=str(r.get("b", "")), a_to_b=str(r.get("a_to_b", "")),
                    b_to_a=str(r.get("b_to_a", "")), note=str(r.get("note", "")),
                ),
                from_cue=_optional_int(r.get("from_cue"), f"active_rules[{n}].from_cue", 0),
                until_cue=_optional_int(r.get("until_cue"), f"active_rules[{n}].until_cue", 0),
            )
            for n, r in enumerate(data.get("active_rules") or [])
        )
        requested = data.get("requested")
        if requested is not None and not isinstance(requested, list):
            raise BundleError("requested is a list of cue numbers, or null")
        keep = {int(k["i"]): str(k["text"]) for k in data.get("keep") or []}
        try:  # bundles of version 1 have neither
            style = Style.from_doc(data.get("style"), "style")
            voices = tuple(CharacterCard.from_doc(c, f"voices[{n}]") for n, c in enumerate(data.get("voices") or []))
        except StyleError as exc:
            raise BundleError(f"bad style in bundle: {exc}") from None
        return cls(
            cues=cues,
            target_lang=str(language.get("target") or "vi"),
            target_name=str(language.get("target_name") or ""),
            source_lang=str(language.get("source") or ""),
            title=str(episode.get("title") or ""),
            overview=str(episode.get("overview") or ""),
            season=season,
            episode=episode_no,
            glossary=glossary,
            active_rules=active,
            style=style,
            voices=voices,
            sections=tuple((str(s["title"]), str(s["body"])) for s in data.get("sections") or []),
            few_shot=tuple((str(s["source"]), str(s["target"])) for s in data.get("few_shot") or []),
            requested=None if requested is None else tuple(_cue_number(n, "requested") for n in requested),
            keep=keep,
            params=p,
            bundle_version=version,
        )

    @classmethod
    def from_json(cls, text: str) -> "TranslationBundle":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BundleError(f"not JSON: {exc}") from None
        return cls.from_dict(data)


# ---------------------------------------------------------------- reading (not hashed)


@dataclass(frozen=True)
class CueMatch:
    """One glossary entry that applies to a range of cues, and why (for the console, FR-21.6)."""

    kind: str  # term | character | address
    label: str
    cues: tuple[int, ...]


def matches(bundle: TranslationBundle, first: int, last: int) -> list[CueMatch]:
    """Glossary entries that touch cues ``first``..``last`` (1-based, inclusive). Pure; the bundle is not changed.

    Terms and characters match by word in the source text; address rules match the cues they cover.
    """
    if first > last:
        return []
    window = [n for n in range(first, last + 1) if 1 <= n <= len(bundle.cues)]
    out: list[CueMatch] = []
    for term in bundle.glossary.terms:
        hits = tuple(n for n in window if matching.contains_any(bundle.cues[n - 1].text, term.sources))
        if hits:
            out.append(CueMatch("term", f"{term.source} → {term.target}", hits))
    for character in bundle.glossary.characters:
        hits = tuple(n for n in window if matching.contains(bundle.cues[n - 1].text, character.name))
        if hits:
            out.append(CueMatch("character", character.name, hits))
    for ar in bundle.active_rules:
        start = ar.from_cue or 1
        end = (ar.until_cue - 1) if ar.until_cue is not None else len(bundle.cues)
        hits = tuple(n for n in window if start <= n <= end)
        if hits:
            out.append(CueMatch("address", f"{ar.rule.a} – {ar.rule.b}", hits))
    return out

