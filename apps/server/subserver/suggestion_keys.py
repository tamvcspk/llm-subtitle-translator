"""What makes two suggestions the same one (R13-6): a pure helper, shared by the store, the service and the generator.

The *fingerprint* of a suggestion hashes everything it says, including the free text a model writes (``note``,
``target``, ``why``), so a rejected proposal came back whenever the wording differed. The *identity* is only what the
glossary keys an entry by: a term is its source (and where it starts), an address rule is the pair of characters
(whichever side is named first) and where it starts, a character is its name. A series keeps one decision per identity
and language, so a rejected proposal is not proposed again however it is worded.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["identity", "identity_key"]


def _fold(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def identity(kind: str, payload: dict[str, Any]) -> tuple[str, ...]:
    """The parts that identify a proposal of ``kind``. ``KeyError``/``TypeError`` for a payload without them."""
    if kind == "term":
        return (_fold(payload["source"]), _fold(payload.get("from")))
    if kind == "address_rule":
        low, high = sorted((_fold(payload["a"]), _fold(payload["b"])))
        return (low, high, _fold(payload.get("from")))
    if kind == "character":
        return (_fold(payload["name"]),)
    raise KeyError(kind)


def identity_key(kind: str, payload: dict[str, Any]) -> str:
    """The identity as one string (a column of the table). Empty when the payload has no identity."""
    try:
        return json.dumps([kind, *identity(kind, payload)], ensure_ascii=False)
    except (KeyError, TypeError, AttributeError):
        return ""
