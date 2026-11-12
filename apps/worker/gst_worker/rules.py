"""The rules that apply to one episode: glossary file + episode corrections (FR-4, FR-11.2).

Pure functions, no I/O. The result feeds the prompt (``PromptContext``) and, through ``hash``,
decides whether a stored translation is still current: a translation whose ``glossary_hash`` differs
from the one computed now was made with different rules and is shown as stale (FR-5.5, FR-5.6).
Cue edits are deliberately outside the hash; they are locked output, not rules.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from subcore import glossary as g
from subcore import srt

RULE_KINDS = ("term", "address", "note")


@dataclass
class UnitRules:
    glossary: g.Glossary = field(default_factory=g.Glossary)
    active_rules: list[g.ActiveRule] = field(default_factory=list)
    extra_sections: list[tuple[str, str]] = field(default_factory=list)
    locked: dict[int, str] = field(default_factory=dict)  # cue ordinal -> hand-edited text (FR-11.2.4)
    hash: str = ""


def _term(payload: dict[str, Any]) -> g.Term:
    aliases = payload.get("aliases") or []
    return g.Term(
        source=str(payload["source"]).strip(),
        target=str(payload["target"]).strip(),
        aliases=tuple(str(a).strip() for a in aliases if str(a).strip()),
        locked=bool(payload.get("locked", True)),  # a correction exists to be obeyed
        note=str(payload.get("note") or "").strip(),
    )


def _address(payload: dict[str, Any]) -> g.AddressRule:
    return g.AddressRule(
        a=str(payload["a"]).strip(),
        b=str(payload["b"]).strip(),
        a_to_b=str(payload.get("a_to_b") or "").strip(),
        b_to_a=str(payload.get("b_to_a") or "").strip(),
        note=str(payload.get("note") or "").strip(),
    )


def _supersede(active: list[g.ActiveRule], rule: g.AddressRule, from_cue: int | None) -> list[g.ActiveRule]:
    """A correction for a pair replaces the file's rules for that pair from ``from_cue`` on."""
    pair = {rule.a.casefold(), rule.b.casefold()}
    kept: list[g.ActiveRule] = []
    for current in active:
        if {current.rule.a.casefold(), current.rule.b.casefold()} != pair:
            kept.append(current)
        elif from_cue is not None:
            ends = current.until_cue if current.until_cue is not None and current.until_cue < from_cue else from_cue
            if (current.from_cue or 1) < ends:
                kept.append(replace(current, until_cue=ends))
    return kept


def build_rules(
    glossary: g.Glossary,
    *,
    season: int | None,
    episode: int | None,
    corrections: list[dict[str, Any]] | None = None,
    cues: list[srt.Cue] | None = None,
) -> UnitRules:
    """Merge the glossary file with an episode's active corrections.

    ``cues`` (the source) places timed address rules and validates cue edits; without it the
    result is still good for hashing and for term/note rules.
    """
    scoped = glossary.for_episode(season, episode)
    active = scoped.active_address_rules(season, episode, cues)
    terms = list(scoped.terms)
    rules = list(scoped.address_rules)
    notes: list[str] = []
    locked: dict[int, str] = {}
    fingerprint: list[Any] = []

    for correction in corrections or []:
        kind, payload = correction["kind"], correction["payload"]
        if kind == "term":
            term = _term(payload)
            terms = [t for t in terms if t.source.casefold() != term.source.casefold()] + [term]
            fingerprint.append(["term", payload])
        elif kind == "address":
            rule = _address(payload)
            from_cue = payload.get("from_cue")
            from_cue = int(from_cue) if from_cue not in (None, "") else None
            active = _supersede(active, rule, from_cue) + [g.ActiveRule(rule, from_cue, None)]
            rules.append(rule)
            fingerprint.append(["address", payload])
        elif kind == "note":
            text = str(payload.get("text") or "").strip()
            if text:
                notes.append(text)
                fingerprint.append(["note", text])
        elif kind == "cue_edit":
            ordinal = int(payload["cue"])
            if cues is not None:
                if not 1 <= ordinal <= len(cues) or cues[ordinal - 1].text != payload.get("source_text", cues[ordinal - 1].text):
                    continue  # the source changed under the edit: it no longer describes this cue
            locked[ordinal] = str(payload["text"])

    merged = replace(scoped, terms=tuple(terms), address_rules=tuple(rules))
    sections = [("Corrections for this episode", "\n".join(f"- {text}" for text in notes))] if notes else []
    digest = hashlib.sha256(
        json.dumps([g.dumps(scoped), fingerprint], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return UnitRules(merged, active, sections, locked, digest)


def correction_position(season: int | None, episode: int | None, cue: srt.Cue | None) -> g.Position | None:
    """Story position for a ``forward`` rule: the episode, plus the cue's start when one is given."""
    if season is None or episode is None:
        return None
    if cue is None:
        return g.Position(season, episode, 0, False)
    return g.Position(season, episode, cue.start_ms, True)
