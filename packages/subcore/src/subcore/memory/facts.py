"""Episode facts: what the source subtitle of ONE unit says about the story (FR-5.1, ARCHITECTURE §3.4).

Facts are extracted from the source subtitle only, never from a translation, and depend on nothing but
that file and the extraction prompt, so they do not depend on the order episodes are translated in.
The model is never trusted: ``Facts.from_dict`` drops malformed entries instead of failing, and cue
numbers are checked against the real cue count.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import json_repair

from .. import srt
from ..providers.base import BadOutput, LLMRequest, Provider

__all__ = [
    "EXTRACT_PROMPT_VERSION",
    "FACTS_SCHEMA",
    "HIERARCHY",
    "INTIMACY_MAX",
    "MIN_CONFIDENCE",
    "RELATIONS",
    "Character",
    "ExtractResult",
    "Facts",
    "FactsError",
    "FactTerm",
    "RelationshipEvent",
    "extract_facts",
]

# Bump when the extraction prompt or schema changes: stored facts of an older version are not reused,
# and translations made from them are flagged stale (FR-5.5).
# x2: fiancés and spouses are `romantic` (x1 called them `family` on real episodes).
# x3: events carry `detail` ("mother and son"), since `family` alone hides parent / sibling / in-law.
EXTRACT_PROMPT_VERSION = "x3"

RELATIONS = ("romantic", "family", "friend", "rival", "enemy", "professional", "stranger")
HIERARCHY = ("a_above", "equal", "b_above")
INTIMACY_MAX = 5
# Events the model itself doubts are ignored when folding the story (ARCHITECTURE §8: relationship
# extraction from speaker-less subtitles is noisy).
MIN_CONFIDENCE = 0.3
_MAX_CHARACTERS = 60
_MAX_EVENTS = 80
_MAX_TERMS = 60
_MAX_SUMMARY = 1200
_MAX_EVIDENCE = 200

FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "gender": {"type": "string", "enum": ["f", "m", "other", "unknown"]},
                    "age_hint": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        "relationship_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                    "relation": {"type": "string", "enum": list(RELATIONS)},
                    "intimacy": {"type": "integer"},
                    "hierarchy": {"type": "string", "enum": list(HIERARCHY)},
                    "cue": {"type": "integer"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                    "detail": {"type": "string"},
                },
                "required": ["a", "b", "relation", "cue"],
            },
        },
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"source": {"type": "string"}, "kind": {"type": "string"}},
                "required": ["source"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["characters", "relationship_events", "terms", "summary"],
}


class FactsError(ValueError):
    """The model's answer could not be turned into facts."""


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


@dataclass(frozen=True)
class Character:
    name: str
    aliases: tuple[str, ...] = ()
    gender: str = ""  # f | m | other | ""
    age_hint: str = ""
    role: str = ""


@dataclass(frozen=True)
class RelationshipEvent:
    a: str
    b: str
    relation: str
    cue: int  # 1-based cue ordinal in this unit's source: where the relationship is shown to have this state
    intimacy: int = 0
    hierarchy: str = "equal"
    evidence: str = ""
    confidence: float = 0.5
    # The exact relationship in a few words ("mother and son", "ex-girlfriend"), `a` first. `relation`
    # alone cannot tell a parent from a sibling or an in-law, which Vietnamese forms of address need.
    detail: str = ""


@dataclass(frozen=True)
class FactTerm:
    source: str
    kind: str = ""


@dataclass(frozen=True)
class Facts:
    characters: tuple[Character, ...] = ()
    relationship_events: tuple[RelationshipEvent, ...] = ()
    terms: tuple[FactTerm, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "characters": [
                {"name": c.name, "aliases": list(c.aliases), "gender": c.gender, "age_hint": c.age_hint, "role": c.role}
                for c in self.characters
            ],
            "relationship_events": [
                {
                    "a": e.a, "b": e.b, "relation": e.relation, "intimacy": e.intimacy, "hierarchy": e.hierarchy,
                    "cue": e.cue, "evidence": e.evidence, "confidence": e.confidence, "detail": e.detail,
                }
                for e in self.relationship_events
            ],
            "terms": [{"source": t.source, "kind": t.kind} for t in self.terms],
            "summary": self.summary,
        }

    @property
    def hash(self) -> str:
        """Content hash; identical facts always hash alike, which is what staleness compares."""
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_empty(self) -> bool:
        return not (self.characters or self.relationship_events or self.terms or self.summary)

    @classmethod
    def from_dict(cls, data: Any, cue_count: int | None = None) -> "Facts":
        """Build facts from a model answer; malformed entries are dropped, a non-object is an error."""
        if not isinstance(data, dict):
            raise FactsError("the answer is not a JSON object")
        characters: list[Character] = []
        seen: set[str] = set()
        for raw in data.get("characters") or []:
            if not isinstance(raw, dict):
                continue
            name = _text(raw.get("name"), 80)
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            aliases = tuple(
                a for a in dict.fromkeys(_text(x, 80) for x in (raw.get("aliases") or []) if isinstance(x, str))
                if a and a.casefold() != name.casefold()
            )
            gender = _text(raw.get("gender"), 10).lower()
            characters.append(Character(
                name, aliases, gender if gender in ("f", "m", "other") else "",
                _text(raw.get("age_hint"), 40), _text(raw.get("role"), 60),
            ))
        events: list[RelationshipEvent] = []
        for raw in data.get("relationship_events") or []:
            if not isinstance(raw, dict):
                continue
            a, b = _text(raw.get("a"), 80), _text(raw.get("b"), 80)
            relation = _text(raw.get("relation"), 30).lower()
            try:
                cue = int(raw.get("cue"))
            except (TypeError, ValueError):
                continue
            if not a or not b or a.casefold() == b.casefold() or relation not in RELATIONS or cue < 1:
                continue
            if cue_count is not None and cue > cue_count:
                continue
            try:
                intimacy = max(0, min(INTIMACY_MAX, int(raw.get("intimacy", 0))))
            except (TypeError, ValueError):
                intimacy = 0
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5
            hierarchy = _text(raw.get("hierarchy"), 12).lower()
            events.append(RelationshipEvent(
                a, b, relation, cue, intimacy, hierarchy if hierarchy in HIERARCHY else "equal",
                _text(raw.get("evidence"), _MAX_EVIDENCE), round(confidence, 2), _text(raw.get("detail"), 60),
            ))
        terms: list[FactTerm] = []
        seen_terms: set[str] = set()
        for raw in data.get("terms") or []:
            source = _text(raw.get("source") if isinstance(raw, dict) else raw, 80)
            if source and source.casefold() not in seen_terms:
                seen_terms.add(source.casefold())
                terms.append(FactTerm(source, _text(raw.get("kind"), 30) if isinstance(raw, dict) else ""))
        events.sort(key=lambda e: e.cue)  # stable: model order breaks ties
        summary = " ".join(str(data.get("summary") or "").split())[:_MAX_SUMMARY]
        return cls(tuple(characters[:_MAX_CHARACTERS]), tuple(events[:_MAX_EVENTS]), tuple(terms[:_MAX_TERMS]), summary)


@dataclass
class ExtractResult:
    facts: Facts
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


_SYSTEM = """\
You read the subtitles of ONE episode (or film) of a story and report, as structured data, what the
dialogue shows about it. Subtitles carry no speaker names, so only report what the lines make clear.

Report:
- characters: every named character who speaks or is spoken about. Use the most common spelling as
  `name`; put other names, nicknames and titles for the same person in `aliases`. gender is f, m, other
  or unknown. age_hint is a rough label such as "teen", "20s", "middle-aged", "elderly". role is a few
  words ("the lead", "Alice's mother", "the villain").
- relationship_events: moments where the dialogue shows how two characters stand to each other. One
  event per change of state; do not repeat an unchanged relationship. `cue` is the item number (`i`)
  where the state is first clear. relation is one of: {relations}. Use romantic for anyone who is
  dating, engaged, married or in love, and family only for blood or adoptive relatives (a fiancé or
  spouse is romantic, never family). detail names the exact relationship in a few words, `a` first
  ("mother and son", "husband and wife", "older sister and younger brother", "boss and subordinate",
  "ex-girlfriend"). intimacy is 0 (hostile or distant)
  to 5 (very close). hierarchy is a_above, equal or b_above (who holds authority or seniority over
  whom, `a` and `b` as written in the event). `evidence` is a short quote copied from that item.
  confidence is 0..1; use low values when the dialogue only hints at it.
- terms: proper names of places, organisations, objects and invented terms a translator must keep
  consistent (not the characters' own names).
- summary: what happens in this episode, in English, at most 150 words, spoilers included.

Write names exactly as the subtitles spell them. Answer in English except for names and quotes.
Output a single JSON object with the keys characters, relationship_events, terms and summary."""


def _user(cues: list[srt.Cue], title: str, season: int | None, episode: int | None, source_lang: str | None) -> str:
    head = [f"Title: {title}"] if title else []
    if season is not None and episode is not None:
        head.append(f"Episode: season {season}, episode {episode}")
    if source_lang:
        head.append(f"Subtitle language: {source_lang}")
    lines = "\n".join(
        json.dumps({"i": n, "text": cue.text}, ensure_ascii=False)
        for n, cue in enumerate(cues, start=1)
        if cue.text.strip()
    )
    return "\n".join(head + ["", "SUBTITLE ITEMS (one JSON object per line; `i` is the item number):", lines])


def extract_facts(
    provider: Provider,
    cues: list[srt.Cue],
    *,
    title: str = "",
    season: int | None = None,
    episode: int | None = None,
    source_lang: str | None = None,
    temperature: float | None = None,
    attempts: int = 2,
) -> ExtractResult:
    """Ask the model for the facts of one unit. Raises :class:`FactsError` when it keeps giving junk.

    Provider errors (``RateLimited``, ``QuotaExhausted``...) propagate. Timecodes are never sent.
    """
    req = LLMRequest(
        system=_SYSTEM.format(relations=", ".join(RELATIONS)),
        user=_user(cues, title, season, episode, source_lang),
        response_schema=FACTS_SCHEMA if provider.caps.structured_output else None,
        temperature=temperature,
        task="extract",
        meta={"kind": "extract", "cues": len(cues), "title": title, "season": season, "episode": episode},
    )
    tokens_in = tokens_out = 0
    model = ""
    last = "no answer"
    for _ in range(max(1, attempts)):
        try:
            response = provider.generate(req)
        except BadOutput as exc:
            last = str(exc) or "empty answer"
            continue
        tokens_in += response.tokens_in
        tokens_out += response.tokens_out
        model = response.model or model
        try:
            data = json_repair.loads(response.text)
        except Exception:  # noqa: BLE001 - json_repair raises assorted errors on garbage
            data = None
        try:
            facts = Facts.from_dict(data, cue_count=len(cues))
        except FactsError as exc:
            last = str(exc)
            continue
        return ExtractResult(facts, tokens_in, tokens_out, model)
    raise FactsError(last)
