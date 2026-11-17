"""Proposals drawn from story facts: forms of address, proper names and character cards (FR-6, FR-18.3; P14).

Two halves, both free of the worker's database and queue:

* **Candidates** are plain functions over the facts of the episodes in story order (and, for evidence, their source
  cues). They decide *what is worth proposing*: a pair of characters whose relationship the facts show (one candidate
  per state of the relationship, with the position where it starts), a proper name that shows up in two or more
  episodes and is not in the glossary yet, a character that appears in two or more episodes. No model is involved,
  so the worker can count them before spending a call.
* **Forms** are one model call per kind that turns the candidates into what the glossary stores in the target language
  (``anh–em``, a translated name, how a character speaks). The model is never trusted: an answer that is not JSON, or
  an entry that is malformed, is dropped and never raises past :class:`SuggestError`.

Every candidate carries its evidence: the cue number, the timecode and the text of the cue in the source subtitle.
A proposal never claims more than the facts say, and nothing here applies anything (FR-4.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import json_repair

from . import srt
from .glossary import Glossary, Position
from .memory.facts import Character, Facts, RelationshipEvent
from .memory.fold import _Names
from .providers.base import BadOutput, LLMRequest, Provider

__all__ = [
    "SUGGEST_PROMPT_VERSION",
    "AddressItem",
    "CharacterItem",
    "Draft",
    "Episode",
    "SuggestError",
    "TermItem",
    "address_drafts",
    "address_items",
    "character_drafts",
    "character_items",
    "term_drafts",
    "term_items",
]

# Bump when a prompt below changes: it is on the call record, so a change can be told apart.
SUGGEST_PROMPT_VERSION = "s1"

MIN_CONFIDENCE = 0.5  # a doubtful reading of the dialogue is not worth a person's attention
MIN_UNITS = 2  # a name or a character must show in this many episodes (FR-6.2)
MAX_STATES_PER_PAIR = 4  # the latest ones: a pair that flips more often than this is noise
MAX_EVIDENCE = 3
_QUOTE = 200

CuesOf = Callable[[str], Sequence[srt.Cue]]  # an episode label -> its source cues ([] when unknown)


class SuggestError(ValueError):
    """The model's answer could not be used."""


@dataclass(frozen=True)
class Episode:
    label: str  # S01E03
    season: int
    episode: int
    facts: Facts


@dataclass(frozen=True)
class Draft:
    """One proposal, ready for the Review inbox: a kind, what the glossary would store, and why."""

    kind: str  # address_rule | term | character
    payload: dict[str, Any]
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AddressItem:
    """How two characters stand to each other during one stretch of the story. ``a`` sorts before ``b`` by name."""

    a: Character
    b: Character
    relation: str
    hierarchy: str  # a_above | equal | b_above
    intimacy: int
    detail: str
    start: Position | None  # None for the first state of the pair
    until: Position | None  # None for the last
    evidence: tuple[dict[str, Any], ...]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.a.name.casefold(), self.b.name.casefold(), str(self.start or ""))


@dataclass(frozen=True)
class TermItem:
    source: str
    kind: str
    units: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]

    @property
    def key(self) -> tuple[str, ...]:
        return (self.source.casefold(), "")


@dataclass(frozen=True)
class CharacterItem:
    character: Character
    units: tuple[str, ...]
    relations: tuple[str, ...]  # "enemy of Bob (S02E01)"
    evidence: tuple[dict[str, Any], ...]

    @property
    def key(self) -> tuple[str, ...]:
        return (self.character.name.casefold(),)


# -- evidence ------------------------------------------------------------------------------------------------


def stamp(ms: int) -> str:
    total_s, milli = divmod(max(0, int(ms)), 1000)
    hours, rest = divmod(total_s, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milli:03d}"


def _cue_evidence(label: str, cue: int | None, quote: str, cues_of: CuesOf, note: str) -> dict[str, Any]:
    """The cue a fact rests on, as a person can check it: number, timecode and the cue's own text (the model's quote
    when the source is gone)."""
    cues = cues_of(label) if cue else ()
    text, timecode = quote, ""
    if cue and 1 <= cue <= len(cues):
        found = cues[cue - 1]
        timecode = stamp(found.start_ms)
        text = " ".join(found.text.split()) or quote
    out: dict[str, Any] = {"episode": label}  # the keys the Review page reads (docs/API.md)
    if cue:
        out["cue"] = cue
    if timecode:
        out["timecode"] = timecode
    return {**out, "text": " ".join(text.split())[:_QUOTE], "note": note}


def _start_of(episode: Episode, cue: int, cues_of: CuesOf) -> Position:
    cues = cues_of(episode.label)
    if 1 <= cue <= len(cues):
        return Position(episode.season, episode.episode, cues[cue - 1].start_ms, True)
    return Position(episode.season, episode.episode)


# -- the characters of the whole series ---------------------------------------------------------------------------


def _names(episodes: Sequence[Episode]) -> _Names:
    """Every character of the series, merged by name and alias, before any event is read: an alias that only shows up
    in a later episode still folds the earlier events into the same person."""
    names = _Names()
    for episode in episodes:
        taken: set[str] = set()
        for character in episode.facts.characters:
            names.add(character, taken)
    return names


def _person(names: _Names, name: str) -> Character:
    canonical = names.find(name) or name
    return names.characters.get(canonical.casefold()) or Character(canonical)


# -- forms of address ----------------------------------------------------------------------------------------------


@dataclass
class _State:
    relation: str
    hierarchy: str
    intimacy: int
    detail: str
    first: tuple[Episode, RelationshipEvent]
    seen: list[tuple[Episode, RelationshipEvent]] = field(default_factory=list)


def address_items(episodes: Sequence[Episode], cues_of: CuesOf, *, glossary: Glossary | None = None) -> list[AddressItem]:
    """One item per state of every pair whose relationship the facts show. Pairs the glossary already has a rule for
    are left alone: the admin's rule wins (FR-4.3) and a proposal would only argue with it."""
    names = _names(episodes)
    held = {frozenset((r.a.casefold(), r.b.casefold())) for r in (glossary.address_rules if glossary else ())}
    pairs: dict[tuple[str, str], list[tuple[Episode, RelationshipEvent]]] = {}
    for episode in episodes:
        for event in episode.facts.relationship_events:  # sorted by cue
            if event.confidence < MIN_CONFIDENCE or event.relation == "stranger":
                continue
            a, b = _person(names, event.a), _person(names, event.b)
            if a.name.casefold() == b.name.casefold():
                continue
            pair = (a.name.casefold(), b.name.casefold())
            if pair[0] > pair[1]:
                pair, event = (pair[1], pair[0]), _flipped(event)
                a, b = b, a
            pairs.setdefault(pair, []).append((episode, event))
    items: list[AddressItem] = []
    for pair, events in pairs.items():
        if frozenset(pair) in held:
            continue
        states: list[_State] = []
        for episode, event in events:
            current = states[-1] if states else None
            if current and (current.relation, current.hierarchy) == (event.relation, event.hierarchy):
                current.seen.append((episode, event))
                continue
            states.append(_State(event.relation, event.hierarchy, event.intimacy, event.detail, (episode, event), [(episode, event)]))
        states = states[-MAX_STATES_PER_PAIR:]
        a, b = _person(names, events[0][1].a), _person(names, events[0][1].b)
        starts = [None] + [_start_of(s.first[0], s.first[1].cue, cues_of) for s in states[1:]]
        for n, state in enumerate(states):
            evidence = tuple(
                _cue_evidence(e.label, ev.cue, ev.evidence, cues_of, f"{ev.relation}, {ev.hierarchy.replace('_', ' ')}")
                for e, ev in state.seen[:MAX_EVIDENCE]
            )
            items.append(AddressItem(
                a, b, state.relation, state.hierarchy, state.intimacy, state.detail,
                starts[n], starts[n + 1] if n + 1 < len(states) else None, evidence,
            ))
    return items


def _flipped(event: RelationshipEvent) -> RelationshipEvent:
    swap = {"a_above": "b_above", "b_above": "a_above"}
    return RelationshipEvent(
        event.b, event.a, event.relation, event.cue, event.intimacy, swap.get(event.hierarchy, event.hierarchy),
        event.evidence, event.confidence, event.detail,
    )


# -- proper names ---------------------------------------------------------------------------------------------------


def term_items(
    episodes: Sequence[Episode], cues_of: CuesOf, *, glossary: Glossary | None = None, limit: int = 30,
) -> list[TermItem]:
    """Names and terms that show in ``MIN_UNITS`` or more episodes and are not in the glossary, most widespread first.
    A character's own name is not a term."""
    names = _names(episodes)
    have = {s.casefold() for t in (glossary.terms if glossary else ()) for s in t.sources}
    found: dict[str, dict[str, Any]] = {}
    for episode in episodes:
        for term in episode.facts.terms:
            key = term.source.casefold()
            slot = found.setdefault(key, {"source": term.source, "kind": "", "units": []})
            slot["kind"] = slot["kind"] or term.kind
            if episode.label not in slot["units"]:
                slot["units"].append(episode.label)
    items = []
    for key, slot in found.items():
        if len(slot["units"]) < MIN_UNITS or key in have or names.find(slot["source"]) is not None:
            continue
        items.append((slot, tuple(slot["units"])))
    items.sort(key=lambda pair: (-len(pair[1]), pair[0]["source"].casefold()))
    return [
        TermItem(slot["source"], slot["kind"], units, _term_evidence(slot["source"], units, cues_of))
        for slot, units in items[:limit]
    ]


def _term_evidence(source: str, units: Sequence[str], cues_of: CuesOf) -> tuple[dict[str, Any], ...]:
    needle = source.casefold()
    out: list[dict[str, Any]] = []
    for label in units:
        if len(out) >= MAX_EVIDENCE:
            break
        hit = next((n for n, cue in enumerate(cues_of(label), start=1) if needle in cue.text.casefold()), None)
        out.append(_cue_evidence(
            label, hit, "" if hit else "named in the facts of this episode, no cue of the source has it", cues_of,
            "first cue with this name" if hit else "",
        ))
    return tuple(out)


# -- characters -----------------------------------------------------------------------------------------------------


def character_items(
    episodes: Sequence[Episode], cues_of: CuesOf, *, glossary: Glossary | None = None, limit: int = 20,
) -> list[CharacterItem]:
    """Characters that show in ``MIN_UNITS`` or more episodes and have no entry in the glossary."""
    names = _names(episodes)
    have = {c.name.casefold() for c in (glossary.characters if glossary else ())}
    units: dict[str, list[str]] = {}
    for episode in episodes:
        for character in episode.facts.characters:
            canonical = (names.find(character.name) or character.name).casefold()
            if episode.label not in units.setdefault(canonical, []):
                units[canonical].append(episode.label)
    events: dict[str, list[tuple[Episode, RelationshipEvent, str]]] = {}
    for episode in episodes:
        for event in episode.facts.relationship_events:
            if event.confidence < MIN_CONFIDENCE:
                continue
            a, b = _person(names, event.a), _person(names, event.b)
            events.setdefault(a.name.casefold(), []).append((episode, event, b.name))
            events.setdefault(b.name.casefold(), []).append((episode, event, a.name))
    out: list[CharacterItem] = []
    for key, labels in units.items():
        person = names.characters.get(key)
        if person is None or len(labels) < MIN_UNITS or key in have or person.name.casefold() in have:
            continue
        mine = events.get(key, [])
        relations = tuple(dict.fromkeys(f"{ev.relation} with {other}" for _, ev, other in mine))[-4:]
        evidence = tuple(
            _cue_evidence(e.label, ev.cue, ev.evidence, cues_of, f"{ev.relation} with {other}")
            for e, ev, other in mine[-MAX_EVIDENCE:]
        )
        out.append(CharacterItem(person, tuple(labels), relations, evidence))
    out.sort(key=lambda item: (-len(item.units), item.character.name.casefold()))
    return out[:limit]


# -- the model --------------------------------------------------------------------------------------------------------

_AN_ANSWER = "Output a single JSON object and nothing else."

_ADDRESS_SYSTEM = """\
You decide how characters of a story address each other in subtitles translated into the language with code `{lang}`.
Each item is one pair of characters (A and B) during one stretch of the story. Give `a_to_b`, how A speaks to B, and
`b_to_a`, how B speaks to A, each as a "self–other" pair joined with an en dash: the word the speaker uses for themself,
then the word used for the listener. In Vietnamese, "anh–em" means the speaker says "anh" of themself and "em" of the
listener. Choose what a native speaker would use for exactly this relationship, ages, genders and seniority.
Use only what the item says; do not invent. If the language has no such forms, or plain names are what people would
say, answer an empty string for that direction. {answer}
Shape: {{"rules": [{{"i": <item number>, "a_to_b": "...", "b_to_a": "..."}}]}}"""

_TERM_SYSTEM = """\
You translate proper names and invented terms of a story for subtitles in the language with code `{lang}`. Each item
is a name or term and the lines where it appears. Give `target`: the form to use in {lang} subtitles. Keep the original
when that is what a translator would do (most personal and place names); give a translation or transliteration only
when the language really has one. `note` is a few words saying what it is. {answer}
Shape: {{"terms": [{{"i": <item number>, "target": "...", "note": "..."}}]}}"""

_CHARACTER_SYSTEM = """\
You prepare short character cards for the subtitles of a story translated into the language with code `{lang}`.
Each item is a character with what the dialogue showed about them. Give `target`, the name as it should be written in
{lang} subtitles (the same when it does not change), and `speech`, how this character talks, in a few words, but only
when the lines make it clear (for example "clipped and formal", "teasing, slangy"); otherwise an empty string. Use only
what the item says. {answer}
Shape: {{"characters": [{{"i": <item number>, "target": "...", "speech": "..."}}]}}"""


def _lines(evidence: Sequence[dict[str, Any]]) -> list[str]:
    """The cue texts to show the model: only real cues, never the placeholder of an episode with none."""
    return [e["text"] for e in evidence if e.get("cue") and e["text"]]


def _person_line(c: Character) -> dict[str, str]:
    return {k: v for k, v in (("name", c.name), ("gender", c.gender), ("age", c.age_hint), ("role", c.role)) if v}


def _ask(provider: Provider, system: str, lines: list[dict[str, Any]], key: str, schema: dict[str, Any], count: int) -> list[Any]:
    """One call, up to two tries. Returns the list under ``key``; provider errors propagate."""
    req = LLMRequest(
        system=system,
        user="ITEMS (one JSON object per line):\n" + "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        response_schema=schema if provider.caps.structured_output else None,
        task="extract",
        json_output=True,
        meta={"kind": "suggest", "what": key, "items": count},
    )
    last = "no answer"
    for _ in range(2):
        try:
            response = provider.generate(req)
        except BadOutput as exc:
            last = str(exc) or "empty answer"
            continue
        try:
            data = json_repair.loads(response.text)
        except Exception:  # noqa: BLE001 - json_repair raises assorted errors on garbage
            data = None
        entries = data.get(key) if isinstance(data, dict) else data if isinstance(data, list) else None
        if isinstance(entries, list):
            return entries
        last = "the answer is not the expected JSON"
    raise SuggestError(last)


def _schema(key: str, fields: dict[str, str]) -> dict[str, Any]:
    entry = {"type": "object", "properties": {"i": {"type": "integer"}, **{f: {"type": t} for f, t in fields.items()}}, "required": ["i"]}
    return {"type": "object", "properties": {key: {"type": "array", "items": entry}}, "required": [key]}


def _by_item(entries: list[Any], count: int) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            n = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= n < count and n not in out:
            out[n] = entry
    return out


def _text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


_SENIOR = {"a_above": "A is senior to B", "b_above": "B is senior to A", "equal": "equals"}


def address_drafts(provider: Provider, items: Sequence[AddressItem], *, lang: str) -> list[Draft]:
    """Ask for the forms of address of ``items`` (one call) and return a draft for each pair the model answered."""
    if not items:
        return []
    lines = [
        {
            "i": n, "A": _person_line(item.a), "B": _person_line(item.b), "relation": item.relation,
            "detail": item.detail, "standing": _SENIOR.get(item.hierarchy, "equals"), "closeness": f"{item.intimacy}/5",
            "lines": _lines(item.evidence),
        }
        for n, item in enumerate(items)
    ]
    answers = _by_item(
        _ask(provider, _ADDRESS_SYSTEM.format(lang=lang, answer=_AN_ANSWER), lines, "rules",
             _schema("rules", {"a_to_b": "string", "b_to_a": "string"}), len(items)),
        len(items),
    )
    drafts = []
    for n, item in enumerate(items):
        answer = answers.get(n)
        if not answer:
            continue
        a_to_b, b_to_a = _text(answer.get("a_to_b"), 40), _text(answer.get("b_to_a"), 40)
        if not (a_to_b or b_to_a):
            continue
        payload: dict[str, Any] = {"a": item.a.name, "b": item.b.name}
        if a_to_b:
            payload["a_to_b"] = a_to_b
        if b_to_a:
            payload["b_to_a"] = b_to_a
        if item.start:
            payload["from"] = str(item.start)
        if item.until:
            payload["until"] = str(item.until)
        payload["note"] = f"{item.relation}: {item.detail}" if item.detail else item.relation
        drafts.append(Draft("address_rule", payload, item.evidence))
    return drafts


def term_drafts(provider: Provider, items: Sequence[TermItem], *, lang: str, title: str = "") -> list[Draft]:
    if not items:
        return []
    lines = [
        {"i": n, "term": item.source, "kind": item.kind, "title": title, "lines": _lines(item.evidence)}
        for n, item in enumerate(items)
    ]
    answers = _by_item(
        _ask(provider, _TERM_SYSTEM.format(lang=lang, answer=_AN_ANSWER), lines, "terms",
             _schema("terms", {"target": "string", "note": "string"}), len(items)),
        len(items),
    )
    drafts = []
    for n, item in enumerate(items):
        answer = answers.get(n)
        target = _text(answer.get("target"), 80) if answer else ""
        if not target:
            continue
        payload = {"source": item.source, "target": target}
        note = _text(answer.get("note"), 80) or item.kind
        if note:
            payload["note"] = note
        drafts.append(Draft("term", payload, item.evidence))
    return drafts


def character_drafts(provider: Provider, items: Sequence[CharacterItem], *, lang: str, title: str = "") -> list[Draft]:
    if not items:
        return []
    lines = [
        {
            "i": n, "title": title, **_person_line(item.character), "aliases": list(item.character.aliases),
            "appears_in": len(item.units), "relations": list(item.relations),
            "lines": _lines(item.evidence),
        }
        for n, item in enumerate(items)
    ]
    answers = _by_item(
        _ask(provider, _CHARACTER_SYSTEM.format(lang=lang, answer=_AN_ANSWER), lines, "characters",
             _schema("characters", {"target": "string", "speech": "string"}), len(items)),
        len(items),
    )
    drafts = []
    for n, item in enumerate(items):
        answer = answers.get(n) or {}
        person = item.character
        payload: dict[str, Any] = {"name": person.name}
        target = _text(answer.get("target"), 80)
        if target:
            payload["target"] = target
        if person.gender:
            payload["gender"] = person.gender
        description = ", ".join(x for x in (person.age_hint, person.role) if x)
        if description:
            payload["description"] = description
        speech = _text(answer.get("speech"), 120)
        if speech:
            payload["card"] = {"speech": speech}
        if len(payload) == 1:  # nothing to say about them beyond their name
            continue
        drafts.append(Draft("character", payload, item.evidence))
    return drafts
