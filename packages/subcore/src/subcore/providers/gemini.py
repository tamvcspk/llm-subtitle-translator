"""Gemini adapter (google-genai) with typed error mapping.

The SDK is imported lazily so the rest of ``subcore`` (and its tests) load
without it. Requests stream so long outputs do not hit idle timeouts (NFR-2).
"""

from __future__ import annotations

import re
from typing import Any

from .base import (
    AuthError,
    BadOutput,
    Capabilities,
    LLMRequest,
    LLMResponse,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)

__all__ = ["GeminiProvider", "map_api_error", "DEFAULT_MODEL", "DEFAULT_TIMEOUT_S"]

# gemini-2.5-flash is closed to new API users (404); 3.5-flash answered promptly in the
# 2026-11-11 trial, while 3.7/3.8-flash timed out.
DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_TIMEOUT_S = 300.0  # NFR-2: >= 300 s per call

_RETRY_DELAY_RE = re.compile(r"^\s*([\d.]+)\s*s\s*$")
_DAILY_QUOTA_MARKERS = ("perday", "per_day", "daily")


def _retry_after(details: list[Any]) -> float | None:
    for item in details:
        if not isinstance(item, dict):
            continue
        delay = item.get("retryDelay")
        if isinstance(delay, str):
            match = _RETRY_DELAY_RE.match(delay)
            if match:
                return float(match.group(1))
    return None


def _is_daily_quota(details: list[Any], message: str) -> bool:
    for item in details:
        if not isinstance(item, dict):
            continue
        for violation in item.get("violations") or []:
            if not isinstance(violation, dict):
                continue
            quota = f"{violation.get('quotaId', '')} {violation.get('quotaMetric', '')}".lower()
            if any(marker in quota for marker in _DAILY_QUOTA_MARKERS):
                return True
    return "per day" in message.lower()


def map_api_error(code: int | None, status: str | None, message: str | None, details: Any) -> ProviderError:
    """Map a Gemini HTTP error (code/status/message/``error.details``) to a typed exception."""
    message = message or status or f"HTTP {code}"
    body = details.get("error", details) if isinstance(details, dict) else {}
    inner = body.get("details") if isinstance(body, dict) else None
    inner = inner if isinstance(inner, list) else []
    retry_after = _retry_after(inner)
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        if _is_daily_quota(inner, message):
            return QuotaExhausted(message, retry_after)
        return RateLimited(message, retry_after)
    if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        return AuthError(message)
    if code in (500, 502, 503, 504) or status in ("UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED"):
        return Unavailable(message, retry_after)
    return ProviderError(message)


def _caps_for(model: str) -> Capabilities:
    # Gemini 2.x/3 models share these limits; refine per model as needed.
    return Capabilities(max_input_tokens=1_048_576, max_output_tokens=65_536, structured_output=True, web_search=True)


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        thinking_budget: int | None = None,
        caps: Capabilities | None = None,
        client: Any = None,
    ) -> None:
        if not api_key and client is None:
            raise AuthError("Gemini API key is empty")
        self.model = model
        self.caps = caps or _caps_for(model)
        self._timeout_s = timeout_s
        self._thinking_budget = thinking_budget
        self._api_key = api_key
        self._client = client

    def __repr__(self) -> str:  # never expose the key
        return f"GeminiProvider(model={self.model!r})"

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=int(self._timeout_s * 1000)),
            )
        return self._client

    def _config(self, req: LLMRequest) -> Any:
        from google.genai import types

        kwargs: dict[str, Any] = {"system_instruction": req.system}
        if req.response_schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_json_schema"] = req.response_schema
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if self._thinking_budget is not None:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=self._thinking_budget)
        return types.GenerateContentConfig(**kwargs)

    def _convert(self, exc: Exception) -> ProviderError:
        from google.genai import errors

        if isinstance(exc, errors.APIError):
            return map_api_error(
                getattr(exc, "code", None),
                getattr(exc, "status", None),
                getattr(exc, "message", None),
                getattr(exc, "details", None),
            )
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return Unavailable(f"network error: {type(exc).__name__}")
        raise exc

    def generate(self, req: LLMRequest) -> LLMResponse:
        client = self._get_client()
        parts: list[str] = []
        tokens_in = tokens_out = 0
        finish = None
        try:
            stream = client.models.generate_content_stream(
                model=self.model, contents=req.user, config=self._config(req)
            )
            for chunk in stream:
                if chunk.text:
                    parts.append(chunk.text)
                usage = getattr(chunk, "usage_metadata", None)
                if usage is not None:
                    tokens_in = usage.prompt_token_count or tokens_in
                    # Thinking tokens are billed as output.
                    tokens_out = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) or tokens_out
                candidates = getattr(chunk, "candidates", None) or []
                if candidates and getattr(candidates[0], "finish_reason", None) is not None:
                    finish = candidates[0].finish_reason
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted or re-raised below
            raise self._convert(exc) from exc

        text = "".join(parts)
        finish_name = getattr(finish, "name", str(finish)) if finish is not None else ""
        if not text.strip():
            raise BadOutput(f"empty response (finish_reason={finish_name or 'unknown'})")
        # A MAX_TOKENS cut-off is returned as-is: the cues that made it are kept and the
        # rest are repaired/split by the translator instead of re-buying the whole call.
        return LLMResponse(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out, model=self.model, finish_reason=finish_name
        )

    def count_tokens(self, req: LLMRequest) -> int:
        client = self._get_client()
        try:
            result = client.models.count_tokens(model=self.model, contents=f"{req.system}\n\n{req.user}")
        except Exception as exc:  # noqa: BLE001
            raise self._convert(exc) from exc
        return int(result.total_tokens or 0)
