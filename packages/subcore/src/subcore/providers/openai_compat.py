"""OpenAI-compatible chat completions adapter (FR-9.1, ADR-3): OpenRouter, Ollama, LM Studio, vLLM, OpenAI itself.

Standard library only (``urllib``): the worker already carries google-genai, and a second HTTP stack for one
endpoint is not worth a dependency. Replies stream (server-sent events) so a long answer from a slow local
model is not cut by an idle timeout (NFR-2); a server that ignores ``stream`` and answers with one JSON body
is read too.

Structured output degrades by capability (FR-9.2): the adapter starts with a JSON schema, and when the server
refuses ``response_format`` (HTTP 400/422) it falls back to plain JSON mode, then to no format at all, and
reports ``structured_output = False`` from then on so the engine stops sending a schema. The reply is then
checked cue by cue like any other, by the engine's validator.

Every vendor failure becomes a typed exception here; nothing above parses an error string. The key is sent
only as the ``Authorization`` header of requests to the configured base URL: redirects are never followed (the
header would go with them) and neither the key nor the URL is ever put in an exception or in ``repr``.
"""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Any

from .base import (
    AuthError,
    BadOutput,
    Capabilities,
    LLMRequest,
    LLMResponse,
    ModelNotFound,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from .limiter import estimate_tokens

__all__ = [
    "OpenAICompatProvider",
    "map_http_error",
    "normalize_base_url",
    "DEFAULT_TIMEOUT_S",
    "MODE_SCHEMA",
    "MODE_OBJECT",
    "MODE_NONE",
]

DEFAULT_TIMEOUT_S = 300.0  # NFR-2: >= 300 s per call (a read timeout, so a live stream never trips it)
MODE_SCHEMA, MODE_OBJECT, MODE_NONE = "schema", "object", "none"
_MODES = (MODE_SCHEMA, MODE_OBJECT, MODE_NONE)
# Error ``code``/``type`` values (OpenAI and the servers that copy it) that say the money or the day is gone,
# as opposed to "slow down".
_QUOTA_CODES = frozenset({"insufficient_quota", "billing_hard_limit_reached", "quota_exceeded"})
_ERROR_BODY_LIMIT = 65_536
_MESSAGE_LIMIT = 300
_JSON_MODE_HINT = (
    '\n\nReply with a single JSON object. If your answer is a list, put it under the key "items".'
)
_USER_AGENT = "subcore-openai-compat/1"


def normalize_base_url(base_url: str) -> str:
    """``https://host/v1/`` (or one that already ends in ``/chat/completions``) -> ``https://host/v1``."""
    url = str(base_url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("the base URL must start with http:// or https:// (for example https://openrouter.ai/api/v1)")
    if parts.username or parts.password:
        raise ValueError("put the API key in the key field, not in the base URL")
    return url


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return None  # an HTTP date or nothing: let the caller's own backoff decide


def _error_fields(body: Any) -> tuple[str, str, str]:
    """(message, code, type) of an OpenAI-style error body; empty strings for what is missing."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message, code, kind = error.get("message"), error.get("code"), error.get("type")
    elif isinstance(error, str):
        message, code, kind = error, None, None
    elif isinstance(body, dict):
        message, code, kind = body.get("message") or body.get("detail"), body.get("code"), body.get("type")
    else:
        message = code = kind = None
    text = message if isinstance(message, str) else ""
    return text.strip()[:_MESSAGE_LIMIT], str(code or "").lower(), str(kind or "").lower()


def map_http_error(status: int | None, body: Any = None, headers: Any = None) -> ProviderError:
    """Map an HTTP error answer (status, parsed JSON body or None, response headers) to a typed exception."""
    error = _map_http_error(status, body, headers)
    error.http_status = status
    return error


def _map_http_error(status: int | None, body: Any, headers: Any) -> ProviderError:
    message, code, kind = _error_fields(body)
    message = message or f"HTTP {status}"
    retry_after = _retry_after(headers)
    if status in (401, 403):
        return AuthError(message)
    if status == 402:  # OpenRouter: out of credits
        return QuotaExhausted(message, retry_after)
    if status == 404:
        return ModelNotFound(message)
    if status == 429:
        if code in _QUOTA_CODES or kind in _QUOTA_CODES:
            return QuotaExhausted(message, retry_after)
        return RateLimited(message, retry_after)
    if status == 408 or (status is not None and status >= 500):
        return Unavailable(message, retry_after)
    return ProviderError(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Authorization header to wherever it points: refuse it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content parts: [{"type": "text", "text": "..."}]
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


class OpenAICompatProvider:
    """One model behind an OpenAI-compatible ``/chat/completions`` endpoint."""

    name = "openai_compat"
    # What earlier providers of the same (base URL, model) taught us about response_format, so only the first
    # call of the process pays for the refusal. Keyed without the API key.
    _learned: dict[tuple[str, str], str] = {}

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "",
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        caps: Capabilities | None = None,
        mode: str | None = None,
        stream: bool = True,
        opener: Any = None,
    ) -> None:
        if mode is not None and mode not in _MODES:
            raise ValueError(f"mode must be one of: {', '.join(_MODES)}")
        if not str(model or "").strip():
            raise ValueError("a model name is required")
        self._base_url = normalize_base_url(base_url)
        self._api_key = str(api_key or "").strip()
        self.model = str(model).strip()
        self._timeout_s = timeout_s
        self._stream = stream
        self._opener = opener or urllib.request.build_opener(_NoRedirect)
        self.caps = caps or Capabilities(max_input_tokens=128_000, max_output_tokens=8_192, structured_output=True)
        self._mode = mode or self._learned.get((self._base_url, self.model), MODE_SCHEMA)
        if not self.caps.structured_output and mode is None:
            self._mode = MODE_OBJECT if self._mode == MODE_SCHEMA else self._mode
        self._sync_caps()

    def __repr__(self) -> str:  # never expose the key (or the URL, which may name a private host)
        return f"OpenAICompatProvider(model={self.model!r})"

    @classmethod
    def forget_learned_modes(cls) -> None:
        cls._learned.clear()

    @property
    def json_mode(self) -> str:
        """How replies are asked for now: ``schema``, ``object`` (JSON mode) or ``none``."""
        return self._mode

    # -- request ---------------------------------------------------------------
    def _sync_caps(self) -> None:
        wanted = self._mode == MODE_SCHEMA
        if self.caps.structured_output != wanted:
            self.caps = replace(self.caps, structured_output=wanted)

    def _response_format(self, req: LLMRequest) -> dict[str, Any] | None:
        if self._mode == MODE_SCHEMA and req.response_schema is not None:
            schema = req.response_schema
            if isinstance(schema, dict) and schema.get("type") == "array":
                # json_schema wants an object at the top; the engine's reader finds the list inside it.
                schema = {"type": "object", "properties": {"items": schema}, "required": ["items"]}
            return {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}}
        if self._mode != MODE_NONE and (req.json_output or req.response_schema is not None):
            return {"type": "json_object"}
        return None

    def _payload(self, req: LLMRequest, response_format: dict[str, Any] | None) -> dict[str, Any]:
        system = req.system
        if response_format is not None and response_format["type"] == "json_object":
            system += _JSON_MODE_HINT
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": req.user}],
            "stream": self._stream,
        }
        if self._stream:
            payload["stream_options"] = {"include_usage": True}
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if response_format is not None:
            payload["response_format"] = response_format
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": _USER_AGENT,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # -- call ------------------------------------------------------------------
    def generate(self, req: LLMRequest) -> LLMResponse:
        while True:
            response_format = self._response_format(req)
            try:
                return self._generate_once(req, response_format)
            except ProviderError as exc:
                # The server did not take the response format: ask less of it and try again, once per step.
                if response_format is not None and exc.http_status in (400, 422) and self._demote():
                    continue
                raise

    def _demote(self) -> bool:
        if self._mode == MODE_NONE:
            return False
        self._mode = MODE_OBJECT if self._mode == MODE_SCHEMA else MODE_NONE
        self._learned[(self._base_url, self.model)] = self._mode
        self._sync_caps()
        return True

    def _generate_once(self, req: LLMRequest, response_format: dict[str, Any] | None) -> LLMResponse:
        body = json.dumps(self._payload(req, response_format), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions", data=body, headers=self._headers(), method="POST"
        )
        try:
            with self._opener.open(request, timeout=self._timeout_s) as response:
                content_type = response.headers.get_content_type() if response.headers is not None else ""
                if content_type == "text/event-stream":
                    text, tokens_in, tokens_out, finish = self._read_stream(response)
                else:
                    text, tokens_in, tokens_out, finish = self._read_body(response.read())
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except ProviderError:
            raise
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError) as exc:
            # Timeouts, refused or dropped connections: transient as far as the caller can tell.
            raise Unavailable(f"network error: {type(exc).__name__}") from None
        if not text.strip():
            raise BadOutput(f"empty response (finish_reason={finish or 'unknown'})")
        # A cut-off ("length") is returned as-is: the cues that made it are kept and the rest repaired or split.
        return LLMResponse(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out, model=self.model, finish_reason=finish
        )

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> ProviderError:
        try:
            raw = exc.read(_ERROR_BODY_LIMIT)
            body = json.loads(raw.decode("utf-8", "replace")) if raw else None
        except (OSError, ValueError, http.client.HTTPException):
            body = None
        return map_http_error(exc.code, body, exc.headers)

    # -- replies ---------------------------------------------------------------
    def _read_stream(self, response: Any) -> tuple[str, int, int, str]:
        parts: list[str] = []
        tokens_in = tokens_out = 0
        finish = ""
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):  # blank separators and ": keep-alive" comments
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                raise self._stream_error(chunk)
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                tokens_in = int(usage.get("prompt_tokens") or tokens_in)
                tokens_out = int(usage.get("completion_tokens") or tokens_out)
            choices = chunk.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            if choice is None:
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            parts.append(_as_text(delta.get("content", choice.get("text"))))
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])
        return "".join(parts), tokens_in, tokens_out, finish

    @staticmethod
    def _stream_error(chunk: dict[str, Any]) -> ProviderError:
        """An error the server sent inside an event stream (OpenRouter does): retryable unless it says otherwise."""
        error = chunk.get("error")
        status = error.get("code") if isinstance(error, dict) else None
        status = int(status) if isinstance(status, (int, str)) and str(status).isdigit() else None
        if status is None or not 400 <= status < 600:
            message, _, _ = _error_fields(chunk)
            failure: ProviderError = Unavailable(message or "the server broke the stream")
            return failure
        return map_http_error(status, chunk)

    @staticmethod
    def _read_body(raw: bytes) -> tuple[str, int, int, str]:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise BadOutput("the server's answer is not JSON") from None
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(_error_fields(data)[0] or "the server answered with an error")
        choices = data.get("choices") if isinstance(data, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        if choice is None:
            raise BadOutput("the server's answer has no choices")
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return (
            _as_text(message.get("content", choice.get("text"))),
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            str(choice.get("finish_reason") or ""),
        )

    def count_tokens(self, req: LLMRequest) -> int:
        """There is no standard endpoint for this: the same cheap estimate the rate limiter paces with."""
        return estimate_tokens(req)
