"""An LLM as a judge between two translations of the same cue (FR-18.8, FR-19.6; P15).

The admin's own verdict on a variant is the measure that counts (``Compare``); this is the cheap first look that tells
them where to look first. It is advice and nothing more: no module here, and no caller, may turn a verdict of the judge
into an edit or into an A/B score.

Pure like the rest of ``subcore``: a request is built from plain pairs and a provider, the answer is parsed and every
entry that is not what the prompt asked for is dropped. Nothing knows the worker, its database or its queue.

Three biases of a model that judges are met by construction, not by hope:

* **Position.** Which translation is shown first (A) alternates by a fixed function of the cue number
  (:func:`variant_is_a`), so neither the current text nor the variant is always first, and a repeat of the same
  question gets the same order.
* **Length and formality.** The rubric says in so many words that longer, more formal or listed-first is no reason.
* **Context.** The previous source line is sent so a reply to it is judged as a reply; it is never judged itself.

No timecode and no cue number goes to the model: only the texts, numbered by their place in the call.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

import json_repair

from .providers.base import BadOutput, LLMRequest, Provider

__all__ = [
    "BATCH",
    "JUDGE_PROMPT_VERSION",
    "JudgeError",
    "Pair",
    "Verdict",
    "batches",
    "judge",
    "variant_is_a",
]

# Bump when the rubric or the answer shape changes: it is on the call record and on every stored verdict, and a verdict
# made under another version is not shown as the judge's opinion of today's prompt.
JUDGE_PROMPT_VERSION = "j1"
BATCH = 30  # cues per call: the answer is a line each, so a call stays far below any output limit
WINNERS = ("variant", "current", "tie")
_WHY = 120


class JudgeError(ValueError):
    """The model's answer could not be used."""


@dataclass(frozen=True)
class Pair:
    """One cue, as the two translations stand now. ``n`` is the caller's own key for it (the cue number)."""

    n: int
    source: str
    current: str
    variant: str
    before: str = ""  # the previous source line, for context only


@dataclass(frozen=True)
class Verdict:
    n: int
    winner: str  # variant | current | tie
    why: str = ""


def variant_is_a(n: int) -> bool:
    """Is the variant shown first (as A) for cue ``n``? A fixed, well-mixed function of ``n``, so the order does not
    depend on how many cues differ or which of them are asked about, and a second run asks in the same order."""
    return bool(hashlib.sha1(f"judge-order:{n}".encode("ascii")).digest()[0] & 1)


def batches(pairs: Sequence[Pair], size: int = BATCH) -> list[list[Pair]]:
    """``pairs`` cut into calls of at most ``size``, in order."""
    size = max(1, int(size))
    return [list(pairs[i:i + size]) for i in range(0, len(pairs), size)]


_SYSTEM = """\
You are a careful reviewer of subtitles who reads the language with code `{lang}` natively. Each item is one line of \
dialogue: `source` is the original line and `A` and `B` are two translations of it into `{lang}`. Decide which one \
serves a viewer better, weighing in this order:
1. Meaning: it says what the source says, with nothing added, dropped or changed (names, numbers, negation, who is \
speaking to whom, questions that stay questions).
2. Natural speech: fluent, idiomatic `{lang}` for spoken dialogue, as a native speaker would write it in a subtitle, \
not a word-for-word calque.
3. Subtitle fit: when meaning and naturalness are equal, the tighter wording wins, because a viewer has seconds to \
read it.
Do not prefer a translation because it is longer, more formal, more polite or listed first. If they are equal in \
meaning and naturalness, or differ only by taste, answer "tie". `prev` is the line before, there for context only: \
never judge it. `why` is a few words (at most 12) naming the point that decided it, so a person can check you.
Output a single JSON object and nothing else.
Shape: {{"verdicts": [{{"i": <item number>, "better": "A" | "B" | "tie", "why": "..."}}]}}"""


def _schema() -> dict[str, Any]:
    entry = {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "better": {"type": "string", "enum": ["A", "B", "tie"]},
            "why": {"type": "string"},
        },
        "required": ["i", "better"],
    }
    return {"type": "object", "properties": {"verdicts": {"type": "array", "items": entry}}, "required": ["verdicts"]}


def _line(index: int, pair: Pair) -> dict[str, Any]:
    first, second = (pair.variant, pair.current) if variant_is_a(pair.n) else (pair.current, pair.variant)
    line: dict[str, Any] = {"i": index, "source": pair.source, "A": first, "B": second}
    if pair.before:
        line["prev"] = pair.before
    return line


def _winner(better: Any, variant_first: bool) -> str | None:
    """``A``/``B``/``tie`` as the model wrote it, turned back into the side it was: ``variant`` or ``current``."""
    word = str(better).strip().strip(".\"'").casefold()
    if word in ("tie", "equal", "same", "draw"):
        return "tie"
    if word not in ("a", "b"):
        return None
    return "variant" if (word == "a") == variant_first else "current"


def _why(value: Any) -> str:
    return " ".join(str(value).split())[:_WHY] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def judge(provider: Provider, pairs: Sequence[Pair], *, lang: str) -> list[Verdict]:
    """Ask the model which of the two texts is better for each of ``pairs`` (one call, up to two tries) and return a
    verdict for every pair it answered, in the order asked. A pair the model skipped or answered oddly has no verdict;
    the caller counts the gap. Provider errors propagate typed (the queue maps them); :class:`JudgeError` is an answer
    that is not the expected JSON after both tries."""
    if not pairs:
        return []
    if len(pairs) > BATCH:
        raise ValueError(f"at most {BATCH} cues in one call")
    lines = [_line(index, pair) for index, pair in enumerate(pairs)]
    req = LLMRequest(
        system=_SYSTEM.format(lang=lang),
        user="ITEMS (one JSON object per line):\n" + "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        response_schema=_schema() if provider.caps.structured_output else None,
        task="judge",
        json_output=True,
        meta={"kind": "judge", "items": len(pairs), "numbers": [p.n for p in pairs]},
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
        entries = data.get("verdicts") if isinstance(data, dict) else data if isinstance(data, list) else None
        if not isinstance(entries, list):
            last = "the answer is not the expected JSON"
            continue
        return _read(entries, pairs)
    raise JudgeError(last)


def _read(entries: list[Any], pairs: Sequence[Pair]) -> list[Verdict]:
    seen: dict[int, Verdict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(pairs) or index in seen:
            continue
        pair = pairs[index]
        winner = _winner(entry.get("better"), variant_is_a(pair.n))
        if winner is None:
            continue
        seen[index] = Verdict(pair.n, winner, _why(entry.get("why")))
    return [seen[index] for index in sorted(seen)]
