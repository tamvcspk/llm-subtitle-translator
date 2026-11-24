"""Gemini adapter (google-genai) with typed error mapping.

The SDK is imported lazily so the rest of ``subcore`` (and its tests) load
without it. Requests stream so long outputs do not hit idle timeouts (NFR-2).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import (
    AuthError,
    BadOutput,
    Blocked,
    Capabilities,
    GroundingSource,
    LLMRequest,
    LLMResponse,
    ModelNotFound,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)

__all__ = [
    "GeminiProvider", "map_api_error", "parse_grounding", "parse_search_suggestions", "source_domain", "DEFAULT_MODEL", "DEFAULT_TIMEOUT_S",
    "BLOCKED_FINISH_REASONS",
]

# Finish reasons that mean "the content was refused", as opposed to a cut-off or an empty answer (FR-17.2).
BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "RECITATION"})

# The light model does the routine work; the worker escalates hard jobs to a stronger one
# (gemini-3.5-flash). gemini-2.5-flash is closed to new API users (404); in the 2026-11-11
# trial 3.5-flash answered promptly while 3.7/3.8-flash timed out.
DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_TIMEOUT_S = 300.0  # NFR-2: >= 300 s per call

_RETRY_DELAY_RE = re.compile(r"^\s*([\d.]+)\s*s\s*$")
_DAILY_QUOTA_MARKERS = ("perday", "per_day", "daily")
_INVALID_KEY_REASONS = ("API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_NOT_FOUND")


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
    error = _map_api_error(code, status, message, details)
    error.http_status = code
    return error


def _map_api_error(code: int | None, status: str | None, message: str | None, details: Any) -> ProviderError:
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
    # A key Google does not know comes back as 400 INVALID_ARGUMENT; its ErrorInfo names the reason.
    if any(isinstance(item, dict) and item.get("reason") in _INVALID_KEY_REASONS for item in inner):
        return AuthError(message)
    if code == 404 or status == "NOT_FOUND":
        return ModelNotFound(message)
    if code in (500, 502, 503, 504) or status in ("UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED"):
        return Unavailable(message, retry_after)
    return ProviderError(message)


# -- grounding (FR-7) ---------------------------------------------------------------------------------------------
# What a live call showed (2026-11-23, gemini-3.1-flash-lite with the Google Search tool): ``grounding_chunks[].web.uri`` is a
# redirect link of Google's (``vertexaisearch.cloud.google.com/grounding-api-redirect/...``) that answers 302 to the page, and
# ``web.title`` is the site's registered domain (``wikipedia.org``), not the page's title. The same page comes back as
# several chunks. ``grounding_supports`` ties a passage of the answer (``segment.text``) to chunk indices.
_HOSTLIKE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$", re.IGNORECASE)
_REDIRECT_HOSTS = frozenset({"vertexaisearch.cloud.google.com"})


def source_domain(uri: str, title: str) -> str:
    """The site a grounding chunk is from: its title when that is a domain name (what Gemini gives), else the host of its
    link, unless that is Google's redirect host (which says nothing about the page). Empty when neither says."""
    named = (title or "").strip().lower()
    if _HOSTLIKE.match(named):
        return named.removeprefix("www.")
    try:
        host = (urlparse(uri or "").hostname or "").lower()
    except ValueError:
        host = ""
    return "" if not host or host in _REDIRECT_HOSTS else host.removeprefix("www.")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        data = dump(mode="json", exclude_none=True)
        return data if isinstance(data, dict) else {}
    return {}


def _cite(text: str, supports: list[Any], source_of: dict[int, int]) -> str:
    """``text`` with ``[n]`` after every passage some source backs (``n`` counts the deduplicated sources from 0). A passage
    is found by its own words; when they are not there the provider's byte offset is used."""
    marks: dict[int, list[int]] = {}
    cursor = 0
    raw = text.encode("utf-8")
    for support in supports:
        if not isinstance(support, dict):
            continue
        segment = support.get("segment") if isinstance(support.get("segment"), dict) else {}
        found = sorted({source_of[i] for i in support.get("grounding_chunk_indices") or [] if i in source_of})
        if not found:
            continue
        passage = str(segment.get("text") or "")
        at = text.find(passage, cursor) if passage else -1
        if at < 0 and passage:
            at = text.find(passage)
        if at >= 0:
            end = at + len(passage)
            cursor = max(cursor, at)
        elif isinstance(segment.get("end_index"), int):
            end = len(raw[: segment["end_index"]].decode("utf-8", errors="ignore"))
        else:
            continue
        slot = marks.setdefault(min(end, len(text)), [])
        slot.extend(n for n in found if n not in slot)
    out: list[str] = []
    last = 0
    for end in sorted(marks):
        out.append(text[last:end])
        out.append(" [" + ", ".join(str(n) for n in sorted(marks[end])) + "]")
        last = end
    out.append(text[last:])
    return "".join(out)


def parse_grounding(metadata: Any, text: str) -> tuple[tuple[GroundingSource, ...], tuple[str, ...], str]:
    """``(sources, search queries, cited text)`` of a response's ``grounding_metadata`` (an SDK object or its dict).
    The sources are the distinct pages in the order they first appear; the same page backs several passages."""
    meta = _as_dict(metadata)
    sources: list[GroundingSource] = []
    by_uri: dict[str, int] = {}
    source_of: dict[int, int] = {}
    for number, chunk in enumerate(meta.get("grounding_chunks") or []):
        web = chunk.get("web") if isinstance(chunk, dict) else None
        uri = str(web.get("uri") or "").strip() if isinstance(web, dict) else ""
        if not uri:
            continue
        if uri not in by_uri:
            title = str(web.get("title") or "").strip()
            by_uri[uri] = len(sources)
            sources.append(GroundingSource(uri, source_domain(uri, title), title))
        source_of[number] = by_uri[uri]
    queries = tuple(q.strip() for q in meta.get("web_search_queries") or [] if isinstance(q, str) and q.strip())
    cited = _cite(text, meta.get("grounding_supports") or [], source_of) if sources else ""
    return tuple(sources), queries, cited


MAX_SEARCH_SUGGESTIONS_HTML = 64 * 1024  # the chip is a few KB; anything bigger is not what Google sends


def parse_search_suggestions(metadata: Any) -> str:
    """The HTML of the "Search Suggestions" of a grounded response (``search_entry_point.rendered_content``), exactly as
    the provider sent it, or ``""``. It is never edited: Google's terms forbid changing it."""
    entry = _as_dict(metadata).get("search_entry_point")
    html = entry.get("rendered_content") if isinstance(entry, dict) else None
    return html if isinstance(html, str) and html.strip() and len(html) <= MAX_SEARCH_SUGGESTIONS_HTML else ""


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
        if req.web_search and self.caps.web_search:
            kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
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
        block_reason = ""
        grounding: Any = None
        try:
            stream = client.models.generate_content_stream(
                model=self.model, contents=req.user, config=self._config(req)
            )
            for chunk in stream:
                if chunk.text:
                    parts.append(chunk.text)
                usage = getattr(chunk, "usage_metadata", None)
                if usage is not None:
                    # What a search brought back into the prompt is billed as input too.
                    tokens_in = (usage.prompt_token_count or 0) + (getattr(usage, "tool_use_prompt_token_count", 0) or 0) or tokens_in
                    # Thinking tokens are billed as output.
                    tokens_out = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) or tokens_out
                candidates = getattr(chunk, "candidates", None) or []
                if candidates and getattr(candidates[0], "finish_reason", None) is not None:
                    finish = candidates[0].finish_reason
                if candidates and getattr(candidates[0], "grounding_metadata", None) is not None:
                    grounding = candidates[0].grounding_metadata  # the last one is the whole picture
                feedback = getattr(chunk, "prompt_feedback", None)
                reason = getattr(feedback, "block_reason", None) if feedback is not None else None
                if reason is not None:
                    block_reason = getattr(reason, "name", str(reason))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted or re-raised below
            raise self._convert(exc) from exc

        text = "".join(parts)
        finish_name = getattr(finish, "name", str(finish)) if finish is not None else ""
        if not text.strip():
            if finish_name in BLOCKED_FINISH_REASONS or block_reason:
                raise Blocked(f"blocked by the content filter ({block_reason or finish_name})")
            raise BadOutput(f"empty response (finish_reason={finish_name or 'unknown'})")
        # A MAX_TOKENS cut-off is returned as-is: the cues that made it are kept and the
        # rest are repaired/split by the translator instead of re-buying the whole call.
        sources, queries, cited = parse_grounding(grounding, text) if req.web_search and grounding is not None else ((), (), "")
        suggestions = parse_search_suggestions(grounding) if req.web_search and grounding is not None else ""
        return LLMResponse(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out, model=self.model, finish_reason=finish_name,
            grounding_sources=sources, search_queries=queries, cited_text=cited, search_suggestions=suggestions,
        )

    def count_tokens(self, req: LLMRequest) -> int:
        client = self._get_client()
        try:
            result = client.models.count_tokens(model=self.model, contents=f"{req.system}\n\n{req.user}")
        except Exception as exc:  # noqa: BLE001
            raise self._convert(exc) from exc
        return int(result.total_tokens or 0)
