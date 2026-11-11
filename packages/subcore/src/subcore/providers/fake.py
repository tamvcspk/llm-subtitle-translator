"""Deterministic in-memory provider for tests and dry runs (NFR-8). Never touches the network."""

from __future__ import annotations

import json
from typing import Any, Callable

from .base import Capabilities, LLMRequest, LLMResponse

__all__ = ["FakeProvider", "echo_items"]

Handler = Callable[[LLMRequest, int], "str | list[dict[str, Any]]"]


def echo_items(req: LLMRequest, prefix: str = "") -> list[dict[str, Any]]:
    """Default "translation": ``{i, text}`` for every target, text prefixed with ``prefix``.

    Reads ``req.meta["targets"]`` / ``req.meta["source"]`` set by the translator, so it
    returns tags and line breaks untouched.
    """
    source = req.meta["source"]
    return [{"i": i, "text": f"{prefix}{source[i]}"} for i in req.meta["targets"]]


class FakeProvider:
    """Records every request in ``calls``; ``handler(req, call_no)`` produces the reply.

    ``call_no`` is 1-based. The handler returns either a raw string (sent as-is, so
    tests can return broken JSON) or a list of items (JSON-encoded). It may raise a
    provider exception to simulate failures. With no handler the fake echoes targets
    with ``prefix``.
    """

    name = "fake"

    def __init__(
        self,
        handler: Handler | None = None,
        *,
        prefix: str = "[vi] ",
        caps: Capabilities | None = None,
        model: str = "fake-1",
    ) -> None:
        self.handler = handler
        self.prefix = prefix
        self.caps = caps or Capabilities()
        self.model = model
        self.calls: list[LLMRequest] = []

    def generate(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        if self.handler is None:
            reply: str | list[dict[str, Any]] = echo_items(req, self.prefix)
        else:
            reply = self.handler(req, len(self.calls))
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return LLMResponse(
            text=text,
            tokens_in=self.count_tokens(req),
            tokens_out=max(1, len(text) // 4),
            model=self.model,
        )

    def count_tokens(self, req: LLMRequest) -> int:
        return max(1, (len(req.system) + len(req.user)) // 4)
