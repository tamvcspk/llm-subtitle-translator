"""The answers of the console's routes (ADR-18, P15e): one pydantic model for what a route returns.

A model is the *contract* of an answer, written once: it goes into the OpenAPI document (``docs/openapi.json``), and
``apps/web`` gets its TypeScript types from that document, so a field renamed here and not exported turns CI red.
The routes keep serving what their use cases return (``json_response``: nothing is validated or rewritten at run time, so
no answer can fail because of its model); ``tests/test_answer_contract.py`` runs the real worker on seeded data and checks
every answer against its model, which is what keeps a model true.

Two bases:

* ``Answer`` forbids fields it does not name, so a use case that starts to return one more key fails the contract test
  until the model says so;
* ``OpenAnswer`` ignores them: for an object the use cases build from a stored payload (a job) of which the console reads
  a part.

A field with no default is always in the answer. A field the answer may leave out is ``x: T = maybe()`` (the TypeScript type
is ``x?: T``); one that is there and says "nothing" is ``x: T | None`` (``x: T | null``); one that is either is
``x: T | None = None``. The contract test checks which it is, strictly. Write what a field means in the class docstring:
it reaches the generated types.

What a field means is written as the string right under it (``use_attribute_docstrings``), which becomes the ``description`` of the field in
the document and the comment above it in TypeScript.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def maybe() -> Any:
    """The default of a field that the answer leaves out when it has nothing to say (it is never ``null``)."""
    return Field(default=None)


def _plain(schema: dict[str, Any]) -> None:
    """Drop what a document of answers does not need: the ``title`` pydantic derives from each field name (it says nothing
    the name does not and triples the document) and a ``default`` (a field that may be missing has no value to fall back on:
    the reader decides what missing means)."""
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
        prop.pop("default", None)
    schema.pop("title", None)


class Answer(BaseModel):
    """An answer whose fields are all named."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_plain, use_attribute_docstrings=True)


class OpenAnswer(BaseModel):
    """An answer built from a stored payload: the fields the console reads are named, the others are not its business."""

    model_config = ConfigDict(extra="ignore", json_schema_extra=_plain, use_attribute_docstrings=True)
