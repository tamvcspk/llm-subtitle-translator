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
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Collection, Sequence

import json_repair

from . import srt
from .glossary import AddressRule, Glossary, Position
from .memory.address import clean_facts
from .memory.facts import Character, Facts, RelationshipEvent
from .memory.fold import _Names
from .providers.base import BadOutput, LLMRequest, Provider

__all__ = [
    "CHANGE_MIN_EPISODES",
    "EVENT_CHANGE_CONFIDENCE",
    "SUGGEST_PROMPT_VERSION",
    "AddressItem",
    "AddressStage",
    "CharacterItem",
    "Draft",
    "Episode",
    "SuggestError",
    "TermItem",
    "address_drafts",
    "address_items",
    "character_drafts",
    "character_items",
    "collapse_stages",
    "is_group",
    "term_drafts",
    "term_items",
]

# Bump when a prompt below changes: it is on the call record, so a change can be told apart.
# s2: an address rule says why (`why`), the two directions must agree, and an unclear hierarchy gets the neutral form.
# s3: a character is read only up to the episode where it qualified (no `appears_in`, no facts of later episodes).
# s4: one address proposal per character pair (base + sturdy candidate change points only; P18f).
SUGGEST_PROMPT_VERSION = "s4"

MIN_CONFIDENCE = 0.5  # a doubtful reading of the dialogue is not worth a person's attention
MIN_UNITS = 2  # a name or a character must show in this many episodes (FR-6.2)
CHANGE_MIN_EPISODES = 2  # P18f hysteresis: at least this many episodes on each side of a change point
EVENT_CHANGE_CONFIDENCE = 0.85  # a single explicit turning-point event (wedding, breakup, betrayal...) needs this confidence
MAX_STATES_PER_PAIR = 4  # the latest ones: a pair that flips more often than this is noise
MAX_EVIDENCE = 3
_QUOTE = 200

_KINSHIP_DETAIL = re.compile(
    r"\b(mother|father|mom|dad|son|daughter|brother|sister|sibling|parent|child|cousin|uncle|aunt|grand(?:mother|father|ma|pa|son|daughter)|nephew|niece|family|mẹ|bố|cha|anh|chị|em|con|ông|bà)\b",
    re.IGNORECASE,
)
_TURNING_EVENT = re.compile(
    r"\b(marr(?:y|ied|iage)|wedding|engaged|engagement|divorc(?:e|ed)|break\s*up|broke\s*up|split\s*up|betray(?:ed|al)?|secret\s+identity|reveal(?:ed)?\s+(?:identity|truth)|became\s+enemies|fell\s+in\s+love)\b",
    re.IGNORECASE,
)

CuesOf = Callable[[str], Sequence[srt.Cue]]  # an episode label -> its source cues ([] when unknown)


class SuggestError(ValueError):
    """The model's answer could not be used."""


_EP_PREFIX = re.compile(r"^\s*s(\d+)e(\d+)\b", re.IGNORECASE)


def _fold(value: Any) -> str:
    """Case and spacing folded: how two candidates are told to be the same (the server folds a stored proposal alike)."""
    return " ".join(str(value).split()).casefold()


def change_episode_key(value: Any) -> str:
    """Normalise a ``Position`` or episode/timecode string to ``sXXeYY`` (R18-3/R18-6)."""
    if isinstance(value, Position):
        return f"s{value.season:02d}e{value.episode:02d}"
    text = _fold(value)
    match = _EP_PREFIX.match(text)
    if match:
        return f"s{int(match.group(1)):02d}e{int(match.group(2)):02d}"
    return text


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
class AddressStage:
    """One stretch of a pair's relationship within an :class:`AddressItem`."""

    relation: str
    hierarchy: str  # a_above | equal | b_above
    intimacy: int
    detail: str
    start: Position | None  # None for the base (first) stage
    until: Position | None  # None for the last stage
    evidence: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AddressItem:
    """One pair of characters across the whole series (P18f). ``a`` sorts before ``b`` by name.

    ``relation``/``hierarchy``/``detail`` hold the pair's *base* standing (majority across all episodes);
    ``stages`` has one entry when no sturdy change point was found and ≥ 2 entries when a sturdy change point
    (hysteresis ``CHANGE_MIN_EPISODES`` on each side, or a high-confidence turning event) was detected.
    ``change_from`` is set when the glossary already has rule(s) for this pair and a newer change point appeared."""

    a: Character
    b: Character
    relation: str
    hierarchy: str  # a_above | equal | b_above
    intimacy: int
    detail: str
    stages: tuple[AddressStage, ...]
    evidence: tuple[dict[str, Any], ...]
    timeline: tuple[dict[str, str], ...] = ()
    change_from: Position | None = None
    existing: tuple[AddressRule, ...] = ()

    @property
    def start(self) -> Position | None:
        return self.stages[0].start if self.stages else None

    @property
    def until(self) -> Position | None:
        return self.stages[0].until if self.stages else None

    @property
    def key(self) -> tuple[str, ...]:
        pair = (_fold(self.a.name), _fold(self.b.name))
        if self.change_from is not None:
            return (*pair, change_episode_key(self.change_from))
        return pair


@dataclass(frozen=True)
class TermItem:
    source: str
    kind: str
    units: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]

    @property
    def key(self) -> tuple[str, ...]:
        return (_fold(self.source), "")


@dataclass(frozen=True)
class CharacterItem:
    character: Character
    units: tuple[str, ...]
    relations: tuple[str, ...]  # "enemy of Bob (S02E01)"
    evidence: tuple[dict[str, Any], ...]

    @property
    def key(self) -> tuple[str, ...]:
        return (_fold(self.character.name),)


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


def _clean(episodes: Sequence[Episode]) -> list[Episode]:
    """The episodes with generic forms of address ("Mom", "Sir") taken off their facts (FR-5.9): they are not people."""
    return [replace(e, facts=clean_facts(e.facts)) for e in episodes]


def _names(episodes: Sequence[Episode]) -> _Names:
    """Every character of the series, merged by name and alias, before any event is read: an alias that only shows up
    in a later episode still folds the earlier events into the same person."""
    names = _Names()
    for episode in episodes:
        taken: set[str] = set()
        for character in episode.facts.characters:
            if not is_group(character.name):
                names.add(character, taken)
    return names


def _person(names: _Names, name: str) -> Character:
    canonical = names.find(name) or name
    return names.characters.get(canonical.casefold()) or Character(canonical)


# "Malcolm, Reese and Dewey" is three people the extraction put in one slot. A rule or a card for it would name nobody
# (the worker could never match it against a line), so such an entry is left out, never split: which of the three a
# relationship is about cannot be told from the name.
_GROUP = re.compile(r"[,;/+&]|\s(?:and|và)\s", re.IGNORECASE)


def is_group(name: str) -> bool:
    """Does this name stand for several people (a list, "A and B")?"""
    return bool(_GROUP.search(name))


# -- forms of address (P18f: one proposal per pair, two layers + hysteresis) ----------------------------------------


def _majority_relation(ep_readings: Sequence[tuple[Episode, RelationshipEvent, list[RelationshipEvent]]]) -> str:
    non_stranger = [ev.relation for _, ev, _ in ep_readings if ev.relation != "stranger"]
    counts = Counter(non_stranger)
    if not counts:
        return "stranger"
    best_count = max(counts.values())
    for rel in non_stranger:
        if counts[rel] == best_count:
            return rel
    return non_stranger[0]


def _majority_hierarchy(
    ep_readings: Sequence[tuple[Episode, RelationshipEvent, list[RelationshipEvent]]],
    preferred_relation: str = "",
) -> str:
    counts = Counter(ev.hierarchy for _, ev, _ in ep_readings)
    best_count = max(counts.values())
    top = [h for h, c in counts.items() if c == best_count]
    if len(top) == 1:
        return top[0]
    if preferred_relation:
        pref = Counter(ev.hierarchy for _, ev, _ in ep_readings if ev.relation == preferred_relation)
        if pref:
            best_pref = max(pref.values())
            top_pref = [h for h, c in pref.items() if c == best_pref]
            if len(top_pref) == 1:
                return top_pref[0]
    return "equal" if "equal" in top else top[0]


def _is_kinship_pair(ep_readings: Sequence[tuple[Episode, RelationshipEvent, list[RelationshipEvent]]]) -> bool:
    """Blood kinship (family) never auto-opens relationship stages (P18f, R18-2).

    Requires ``family`` to be the plurality relation across episodes (never a single stray reading among
    other relations), or a top tie backed by an explicit kinship detail (mother, brother, etc.)."""
    counts = Counter(ev.relation for _, ev, _ in ep_readings)
    family_eps = counts.get("family", 0)
    if family_eps == 0:
        return False
    best = max(counts.values())
    if family_eps < best:
        return False
    top = [rel for rel, c in counts.items() if c == best]
    if top == ["family"]:
        return True
    return any(_KINSHIP_DETAIL.search(ev.detail or "") for _, _, evs in ep_readings for ev in evs)


def _is_turning_event(events: Sequence[RelationshipEvent]) -> bool:
    return any(ev.confidence >= EVENT_CHANGE_CONFIDENCE and bool(_TURNING_EVENT.search(ev.detail or "")) for ev in events)


def _detect_cuts(
    ep_readings: Sequence[tuple[Episode, RelationshipEvent, list[RelationshipEvent]]],
    *,
    is_kinship: bool,
) -> list[int]:
    """Indices into ``ep_readings`` where a sturdy change of relation occurs. Always begins with 0.

    Hysteresis rule (P18f):
    - Blood kinship (``is_kinship``) never opens a new stage.
    - Otherwise, a shift from ``cur_rel`` to ``new_rel`` at ``idx`` opens a new stage only when:
      (a) ``cur_rel`` appeared in at least ``CHANGE_MIN_EPISODES`` episodes before ``idx``, ``new_rel`` appears in
          at least ``CHANGE_MIN_EPISODES`` episodes from ``idx`` onwards, and ``cur_rel`` does not return after ``idx``;
      OR
      (b) an event at ``idx`` is an explicit high-confidence turning-point event (``>= EVENT_CHANGE_CONFIDENCE``)
          and ``cur_rel`` does not return after ``idx``.
    """
    if is_kinship or len(ep_readings) < 2:
        return [0]
    cuts = [0]
    cur_start = 0
    cur_rel = ep_readings[0][1].relation
    for idx in range(1, len(ep_readings)):
        _, ev, all_evs = ep_readings[idx]
        new_rel = ev.relation
        if new_rel == cur_rel:
            continue
        returns_to_old = any(r_ev.relation == cur_rel for _, r_ev, _ in ep_readings[idx + 1 :])
        if returns_to_old:
            continue
        before_count = sum(1 for _, b_ev, _ in ep_readings[cur_start:idx] if b_ev.relation == cur_rel)
        after_count = sum(1 for _, a_ev, _ in ep_readings[idx:] if a_ev.relation == new_rel)
        sturdy = before_count >= CHANGE_MIN_EPISODES and after_count >= CHANGE_MIN_EPISODES
        explicit = _is_turning_event(all_evs)
        if sturdy or explicit:
            cuts.append(idx)
            cur_start = idx
            cur_rel = new_rel
    return cuts


def _best_detail(ep_readings: Sequence[tuple[Episode, RelationshipEvent, list[RelationshipEvent]]], relation: str) -> str:
    for _, ev, all_evs in ep_readings:
        for candidate in (ev, *all_evs):
            if candidate.relation == relation and candidate.detail:
                return candidate.detail
    for _, ev, _ in ep_readings:
        if ev.detail:
            return ev.detail
    return ""


def _pair_rules(glossary: Glossary | None, pair: tuple[str, str]) -> tuple[AddressRule, ...]:
    if glossary is None:
        return ()
    matched = [r for r in glossary.address_rules if tuple(sorted((r.a.casefold(), r.b.casefold()))) == pair]
    matched.sort(key=lambda r: (r.start is not None, r.start or Position(0, 0)))
    return tuple(matched)


def address_items(
    episodes: Sequence[Episode], cues_of: CuesOf, *, glossary: Glossary | None = None,
    exclude: Collection[tuple[str, ...]] = (),
    exclude_changes: Collection[tuple[str, str, str]] = (),
) -> list[AddressItem]:
    """One item per unordered character pair whose relationship the facts show (P18f).

    Pairs the glossary already has rules for are skipped unless a newer sturdy change point appeared that the existing
    rules do not yet cover (in which case ``change_from`` is set on the item so a change proposal is created).
    ``exclude`` are pair keys ``(low, high)`` not to ask about for new pairs; ``exclude_changes`` are
    ``(low, high, change_from)`` keys already decided or pending for change proposals."""
    episodes = _clean(episodes)
    names = _names(episodes)
    pairs: dict[tuple[str, str], list[tuple[Episode, RelationshipEvent]]] = {}
    for episode in episodes:
        for event in episode.facts.relationship_events:  # sorted by cue
            if event.confidence < MIN_CONFIDENCE:
                continue
            if is_group(event.a) or is_group(event.b):
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
        if all(ev.relation == "stranger" for _, ev in events):
            continue
        # Collapse to one dominant reading per episode (highest confidence, preferring non-stranger) in story order.
        by_ep: dict[str, tuple[Episode, list[RelationshipEvent]]] = {}
        for episode, ev in events:
            slot = by_ep.get(episode.label)
            if slot is None:
                by_ep[episode.label] = (episode, [ev])
            else:
                slot[1].append(ev)
        ep_readings: list[tuple[Episode, RelationshipEvent, list[RelationshipEvent]]] = []
        for episode, evs in by_ep.values():
            best = max(evs, key=lambda e: (e.relation != "stranger", e.confidence))
            ep_readings.append((episode, best, evs))

        is_kinship = _is_kinship_pair(ep_readings)
        base_relation = "family" if is_kinship else _majority_relation(ep_readings)
        base_hierarchy = _majority_hierarchy(ep_readings, preferred_relation=base_relation)
        base_detail = _best_detail(ep_readings, base_relation)
        base_intimacy = round(sum(ev.intimacy for _, ev, _ in ep_readings) / len(ep_readings))

        cuts = _detect_cuts(ep_readings, is_kinship=is_kinship)
        if len(cuts) > MAX_STATES_PER_PAIR:
            cuts = [0, *cuts[-(MAX_STATES_PER_PAIR - 1) :]]

        # Build stages from the detected cuts.
        raw_slices = [
            ep_readings[cuts[n] : (cuts[n + 1] if n + 1 < len(cuts) else len(ep_readings))]
            for n in range(len(cuts))
        ]
        stage_starts: list[Position | None] = [None]
        for sl in raw_slices[1:]:
            first_ep, first_ev, _ = sl[0]
            stage_starts.append(_start_of(first_ep, first_ev.cue, cues_of))

        stages: list[AddressStage] = []
        for n, sl in enumerate(raw_slices):
            rel = base_relation if n == 0 and len(raw_slices) == 1 else _majority_relation(sl)
            hier = base_hierarchy if n == 0 and len(raw_slices) == 1 else _majority_hierarchy(sl, preferred_relation=rel)
            det = base_detail if n == 0 and len(raw_slices) == 1 else _best_detail(sl, rel)
            intim = base_intimacy if n == 0 and len(raw_slices) == 1 else round(sum(ev.intimacy for _, ev, _ in sl) / len(sl))
            st_evidence = tuple(
                _cue_evidence(e.label, ev.cue, ev.evidence, cues_of, f"{ev.relation}, {ev.hierarchy.replace('_', ' ')}")
                for e, ev, _ in sl[:MAX_EVIDENCE]
            )
            stages.append(AddressStage(
                relation=rel, hierarchy=hier, intimacy=intim, detail=det,
                start=stage_starts[n], until=stage_starts[n + 1] if n + 1 < len(raw_slices) else None,
                evidence=st_evidence,
            ))

        existing = _pair_rules(glossary, pair)
        change_from: Position | None = None
        if existing:
            # Pair already has glossary rule(s): only propose if a newer sturdy change point appeared in an
            # episode not already covered by an existing rule start (R18-6: compare by (season, episode)).
            existing_eps = {(r.start.season, r.start.episode) for r in existing if r.start is not None}
            new_starts = [
                st.start for st in stages[1:]
                if st.start is not None and (st.start.season, st.start.episode) not in existing_eps
            ]
            if not new_starts:
                continue
            change_from = new_starts[-1]
            change_keys = {(*pair, ""), (*pair, change_episode_key(change_from)), (*pair, _fold(change_from))}
            if change_keys & set(exclude_changes):
                continue
        else:
            if pair in exclude:
                continue

        a, b = _person(names, events[0][1].a), _person(names, events[0][1].b)
        # Gather evidence across stages (for a change point: before and after).
        if len(stages) == 1:
            combined_evidence = stages[0].evidence
        else:
            combined: list[dict[str, Any]] = []
            for st in stages:
                combined.extend(st.evidence[:2])
            combined_evidence = tuple(combined)
        timeline = tuple(
            {
                "episode": e.label,
                "relation": ev.relation,
                "standing": _SENIOR.get(ev.hierarchy, "equals"),
                **({"detail": ev.detail} if ev.detail else {}),
            }
            for e, ev, _ in ep_readings
        )
        # R18-1: when multiple stages exist, stage 0 describes the stretch before the first change point
        # (not the majority across all episodes, which could be the post-change relation).
        lead = stages[0]
        items.append(AddressItem(
            a=a, b=b, relation=lead.relation, hierarchy=lead.hierarchy, intimacy=lead.intimacy, detail=lead.detail,
            stages=tuple(stages), evidence=combined_evidence, timeline=timeline,
            change_from=change_from, existing=existing,
        ))
    return items


def collapse_stages(stages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent stages that carry the exact same ``(a_to_b, b_to_a)`` forms (P18f)."""
    merged: list[dict[str, Any]] = []
    for raw in stages:
        stage = dict(raw)
        if merged:
            prev = merged[-1]
            same_a = _fold(prev.get("a_to_b") or "") == _fold(stage.get("a_to_b") or "")
            same_b = _fold(prev.get("b_to_a") or "") == _fold(stage.get("b_to_a") or "")
            if same_a and same_b:
                if stage.get("until"):
                    prev["until"] = stage["until"]
                else:
                    prev.pop("until", None)
                continue
        merged.append(stage)
    # Re-stitch boundaries cleanly.
    if len(merged) == 1:
        merged[0].pop("from", None)
        merged[0].pop("until", None)
    else:
        merged[0].pop("from", None)
        for n in range(len(merged) - 1):
            nxt_from = merged[n + 1].get("from")
            if nxt_from:
                merged[n]["until"] = nxt_from
        merged[-1].pop("until", None)
    return merged


def _flipped(event: RelationshipEvent) -> RelationshipEvent:
    swap = {"a_above": "b_above", "b_above": "a_above"}
    return RelationshipEvent(
        event.b, event.a, event.relation, event.cue, event.intimacy, swap.get(event.hierarchy, event.hierarchy),
        event.evidence, event.confidence, event.detail,
    )


# -- proper names ---------------------------------------------------------------------------------------------------


def term_items(
    episodes: Sequence[Episode], cues_of: CuesOf, *, glossary: Glossary | None = None, limit: int = 30,
    exclude: Collection[tuple[str, ...]] = (),
) -> list[TermItem]:
    """Names and terms that show in ``MIN_UNITS`` or more episodes and are not in the glossary, most widespread first.
    A character's own name is not a term. ``exclude`` are keys not to ask about; they go before ``limit`` applies, so
    what was already decided or could not be answered never crowds out the rest."""
    names = _names(_clean(episodes))
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
        if (_fold(slot["source"]), "") in exclude:
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
    cards: Collection[str] = (), exclude: Collection[tuple[str, ...]] = (),
) -> list[CharacterItem]:
    """Characters that show in ``MIN_UNITS`` or more episodes and have neither an entry in the glossary nor a card.

    A card applies to every episode, so a proposal is drawn only from what the story had shown by the episode where
    the character qualified (the ``MIN_UNITS``-th one they are in): their role, age, relations and evidence never come
    from a later episode (R13-2; a unit never sees the facts of a later one). ``cards`` are the casefolded names and
    aliases of the cards the admin already has; ``exclude`` are keys not to ask about (they are dropped before
    ``limit`` applies, so they cannot crowd out the rest)."""
    episodes = _clean(episodes)
    names = _names(episodes)
    have = {c.name.casefold() for c in (glossary.characters if glossary else ())} | {c.casefold() for c in cards}
    order = {episode.label: n for n, episode in enumerate(episodes)}
    units: dict[str, list[str]] = {}
    for episode in episodes:
        for character in episode.facts.characters:
            if is_group(character.name):
                continue
            canonical = (names.find(character.name) or character.name).casefold()
            if episode.label not in units.setdefault(canonical, []):
                units[canonical].append(episode.label)
    events: dict[str, list[tuple[int, Episode, RelationshipEvent, str]]] = {}
    for n, episode in enumerate(episodes):
        for event in episode.facts.relationship_events:
            if event.confidence < MIN_CONFIDENCE or is_group(event.a) or is_group(event.b):
                continue
            a, b = _person(names, event.a), _person(names, event.b)
            events.setdefault(a.name.casefold(), []).append((n, episode, event, b.name))
            events.setdefault(b.name.casefold(), []).append((n, episode, event, a.name))
    scoped: dict[int, _Names] = {}
    ranked: list[tuple[int, CharacterItem]] = []
    for key, labels in units.items():
        person = names.characters.get(key)
        if person is None or len(labels) < MIN_UNITS or (_fold(person.name),) in exclude:
            continue
        if key in have or person.name.casefold() in have or any(a.casefold() in have for a in person.aliases):
            continue
        through = order[labels[MIN_UNITS - 1]]  # labels follow the story order
        if through not in scoped:
            scoped[through] = _names(episodes[: through + 1])
        at = scoped[through]
        then = at.characters.get((at.find(person.name) or person.name).casefold())
        known = Character(
            person.name, person.aliases, then.gender if then else "", then.age_hint if then else "",
            then.role if then else "",
        )
        mine = [(e, ev, other) for n, e, ev, other in events.get(key, []) if n <= through]
        relations = tuple(dict.fromkeys(f"{ev.relation} with {other}" for _, ev, other in mine))[-4:]
        evidence = tuple(
            _cue_evidence(e.label, ev.cue, ev.evidence, cues_of, f"{ev.relation} with {other}")
            for e, ev, other in mine[-MAX_EVIDENCE:]
        )
        ranked.append((len(labels), CharacterItem(known, tuple(labels[:MIN_UNITS]), relations, evidence)))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].character.name.casefold()))
    return [item for _, item in ranked[:limit]]


# -- the model --------------------------------------------------------------------------------------------------------

_AN_ANSWER = "Output a single JSON object and nothing else."

_ADDRESS_SYSTEM = """\
You decide how characters of a story address each other in subtitles translated into the language with code `{lang}`.
Each item is ONE pair of characters (A and B) across the whole series, showing their base standing, their episode
timeline, sample lines, and (only when there is strong evidence of a lasting shift) a list of `candidate_changes`.
Give `a_to_b`, how A speaks to B, and `b_to_a`, how B speaks to A, each as a "self–other" pair joined with an en dash:
the word the speaker uses for themself, then the word used for the listener. In Vietnamese, "anh–em" means the speaker
says "anh" of themself and "em" of the listener. Choose what a native speaker would use for exactly this relationship,
ages, genders and seniority. Use only what the item says; do not invent. If the language has no such forms, or plain
names are what people would say, answer an empty string for that direction. The two directions must fit together (if A
calls B "chị", B is a woman older than A, and B does not call A "chị" back). When the item does not make age or
seniority clear, choose the neutral form for the relationship rather than guess a hierarchy. `why` is one short sentence
naming what in the item decided the answer (for example "Lois is Craig's boss"), so a reviewer can check it.
When `candidate_changes` is present, you may confirm or reject each candidate change in `stages` (`stage` number,
`confirm`: true/false, `a_to_b`, `b_to_a`, `why`); NEVER invent new stages that are not in `candidate_changes`. {answer}
Shape: {{"rules": [{{"i": <item number>, "a_to_b": "...", "b_to_a": "...", "why": "...", "stages": [{{"stage": 1, "confirm": true, "a_to_b": "...", "b_to_a": "...", "why": "..."}}]}}]}}"""

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


def _address_schema() -> dict[str, Any]:
    stage = {
        "type": "object",
        "properties": {
            "stage": {"type": "integer"},
            "confirm": {"type": "boolean"},
            "a_to_b": {"type": "string"},
            "b_to_a": {"type": "string"},
            "why": {"type": "string"},
        },
        "required": ["stage", "confirm"],
    }
    entry = {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "a_to_b": {"type": "string"},
            "b_to_a": {"type": "string"},
            "why": {"type": "string"},
            "stages": {"type": "array", "items": stage},
        },
        "required": ["i"],
    }
    return {"type": "object", "properties": {"rules": {"type": "array", "items": entry}}, "required": ["rules"]}


def address_drafts(provider: Provider, items: Sequence[AddressItem], *, lang: str) -> list[Draft]:
    """Ask ONCE per character pair for its base forms and any confirmed candidate stages (P18f)."""
    if not items:
        return []
    lines = []
    for n, item in enumerate(items):
        line: dict[str, Any] = {
            "i": n,
            "A": _person_line(item.a),
            "B": _person_line(item.b),
            "relation": item.relation,
            "detail": item.detail,
            "standing": _SENIOR.get(item.hierarchy, "equals"),
            "closeness": f"{item.intimacy}/5",
            "timeline": list(item.timeline),
            "lines": _lines(item.evidence),
        }
        if len(item.stages) > 1:
            line["candidate_changes"] = [
                {
                    "stage": k,
                    "from": str(st.start),
                    "relation": st.relation,
                    "standing": _SENIOR.get(st.hierarchy, "equals"),
                    "detail": st.detail,
                    "lines": _lines(st.evidence),
                }
                for k, st in enumerate(item.stages[1:], start=1)
                if st.start is not None
            ]
        if item.existing:
            line["existing_rules"] = [
                {k: v for k, v in (("a", r.a), ("b", r.b), ("a_to_b", r.a_to_b), ("b_to_a", r.b_to_a),
                                   ("from", str(r.start or "")), ("until", str(r.until or ""))) if v}
                for r in item.existing
            ]
        lines.append(line)
    answers = _by_item(
        _ask(provider, _ADDRESS_SYSTEM.format(lang=lang, answer=_AN_ANSWER), lines, "rules", _address_schema(), len(items)),
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
        base_note = f"{item.relation}: {item.detail}" if item.detail else item.relation
        why = _text(answer.get("why"), 100)
        full_note = f"{base_note}. {why}" if why else base_note

        # Build base stage + any candidate change confirmed by the model (model may never invent stages).
        raw_stages: list[dict[str, Any]] = []
        base_stage: dict[str, Any] = {}
        if a_to_b:
            base_stage["a_to_b"] = a_to_b
        if b_to_a:
            base_stage["b_to_a"] = b_to_a
        if full_note:
            base_stage["note"] = full_note
        raw_stages.append(base_stage)

        if len(item.stages) > 1 and isinstance(answer.get("stages"), list):
            by_idx: dict[int, dict[str, Any]] = {}
            for entry in answer["stages"]:
                if not isinstance(entry, dict):
                    continue
                try:
                    st_idx = int(entry.get("stage"))
                except (TypeError, ValueError):
                    continue
                if 1 <= st_idx < len(item.stages) and st_idx not in by_idx:
                    by_idx[st_idx] = entry
            for k, st in enumerate(item.stages[1:], start=1):
                reply = by_idx.get(k)
                if not reply or reply.get("confirm") is False or st.start is None:
                    continue
                st_a, st_b = _text(reply.get("a_to_b"), 40), _text(reply.get("b_to_a"), 40)
                if not (st_a or st_b):
                    continue
                st_doc: dict[str, Any] = {"from": str(st.start)}
                if st_a:
                    st_doc["a_to_b"] = st_a
                if st_b:
                    st_doc["b_to_a"] = st_b
                st_base = f"{st.relation}: {st.detail}" if st.detail else st.relation
                st_why = _text(reply.get("why"), 100)
                st_doc["note"] = f"{st_base}. {st_why}" if st_why else st_base
                raw_stages.append(st_doc)

        merged = collapse_stages(raw_stages)
        if len(merged) == 1:
            if item.change_from is not None:
                # A change proposal whose forms did not actually change has nothing to propose.
                continue
            payload: dict[str, Any] = {"a": item.a.name, "b": item.b.name, **merged[0]}
        else:
            payload = {
                "a": item.a.name,
                "b": item.b.name,
                **({"a_to_b": merged[0]["a_to_b"]} if merged[0].get("a_to_b") else {}),
                **({"b_to_a": merged[0]["b_to_a"]} if merged[0].get("b_to_a") else {}),
                "stages": merged,
                "note": full_note,
            }
            if item.change_from is not None:
                payload["change_from"] = str(item.change_from)
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
            "relations": list(item.relations), "lines": _lines(item.evidence),
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
