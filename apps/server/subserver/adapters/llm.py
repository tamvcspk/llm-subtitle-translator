"""Build the model provider for one translation attempt from worker settings.

Each configured API key becomes its own rate-limited provider (limits are per key/project); the keys are tried
in order and the next one takes over when one is spent (FR-9.4). Which adapter that is follows the setting
``llm_provider``: Gemini, or an OpenAI-compatible server at ``llm_base_url`` (FR-9.1). The model of each task
comes from ``model_for_task`` (FR-9.3).
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from subcore.providers import (
    AuthError,
    CallLog,
    Capabilities,
    FailoverProvider,
    LLMRequest,
    LLMResponse,
    ModelNotFound,
    Provider,
    RateLimiter,
    ResilientProvider,
)

from ..domain.config import PROVIDER_GEMINI, PROVIDER_OPENAI_COMPAT, model_for_task, provider_name  # noqa: F401 - model_for_task re-exported
from ..domain.ports import ApiKey, MissingApiKey  # noqa: F401 - defined by the core; re-exported for callers of this module

# Free-tier default; paid projects set LLM_RPM=0 (unlimited) or their own number.
FREE_TIER_RPM = 10


def _int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def rate_limits(settings: dict[str, Any]) -> tuple[int, int, int]:
    """(rpm, tpm, rpd); 0 means unlimited. Only Gemini's free tier has a known default; a server of your own
    (or a paid account) is unlimited until ``LLM_RPM`` says otherwise."""
    default_rpm = 0 if settings.get("gst_paid_quota") or provider_name(settings) == PROVIDER_OPENAI_COMPAT else FREE_TIER_RPM
    return (
        _int(settings.get("llm_rpm"), default_rpm),
        _int(settings.get("llm_tpm"), 0),
        _int(settings.get("llm_rpd"), 0),
    )


def thinking_budget(settings: dict[str, Any], model: str) -> int | None:
    """Gemini 2.5 takes a token budget; newer models are left on their own default."""
    if "2.5" not in model:
        return None
    if settings.get("gst_no_thinking"):
        return 0
    try:
        return int(str(settings.get("gst_thinking_budget") or "").strip())
    except ValueError:
        return None


class KeyLedger(Protocol):
    """What the worker wants to hear from a provider call: which key was refused, which was used, which model
    the provider does not know (``SettingsService`` keeps this record)."""

    def key_refused(self, key_id: int) -> None: ...

    def key_used(self, key_id: int) -> None: ...

    def model_not_found(self, model: str, message: str) -> None: ...

    def model_found(self, model: str) -> None: ...


class KeyedProvider:
    """One stored key's provider, reporting what the call says about the key and the model."""

    def __init__(self, inner: Provider, key_id: int, ledger: KeyLedger) -> None:
        self.inner = inner
        self.key_id = key_id
        self.ledger = ledger

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def caps(self):  # noqa: ANN201 - Capabilities of the wrapped provider
        return self.inner.caps

    def count_tokens(self, req: LLMRequest) -> int:
        return self.inner.count_tokens(req)

    def generate(self, req: LLMRequest) -> LLMResponse:
        try:
            response = self.inner.generate(req)
        except AuthError:
            self.ledger.key_refused(self.key_id)
            raise
        except ModelNotFound as exc:
            self.ledger.model_not_found(self.inner.model, str(exc))
            raise
        self.ledger.key_used(self.key_id)
        self.ledger.model_found(self.inner.model)
        return response


def build_provider(
    settings: dict[str, Any],
    model: str,
    *,
    deadline: float | None = None,
    on_call: Callable[[CallLog], None] | None = None,
    keys: Sequence[ApiKey] | None = None,
    ledger: KeyLedger | None = None,
) -> Provider:
    """The provider chain for ``model``: one rate-limited provider per key, tried in order.

    ``keys`` are the stored keys to use (with a ``ledger`` to hear about them); without them the keys in
    ``settings`` are used, which is what the tests and the CLI-style callers pass."""
    if provider_name(settings) == PROVIDER_OPENAI_COMPAT:
        return _build_openai_compat(settings, model, deadline=deadline, on_call=on_call, keys=keys, ledger=ledger)
    from subcore.providers.gemini import GeminiProvider

    if keys is None:
        names = ("gemini_api_key", "gemini_api_key2")
        entries = [(None, value) for value in (str(settings.get(name) or "").strip() for name in names) if value]
    else:
        entries = [(key.id, key.value) for key in keys if key.value]
    if not entries:
        raise MissingApiKey("Gemini API key is not configured")
    rpm, tpm, rpd = rate_limits(settings)
    budget = thinking_budget(settings, model)
    providers: list[Provider] = []
    for key_id, value in entries:
        inner: Provider = GeminiProvider(value, model, thinking_budget=budget)
        if ledger is not None and key_id is not None:
            inner = KeyedProvider(inner, key_id, ledger)
        providers.append(
            ResilientProvider(inner, RateLimiter(rpm, tpm, rpd), deadline=deadline, on_call=on_call)
        )
    return providers[0] if len(providers) == 1 else FailoverProvider(providers)


def _build_openai_compat(
    settings: dict[str, Any],
    model: str,
    *,
    deadline: float | None,
    on_call: Callable[[CallLog], None] | None,
    keys: Sequence[ApiKey] | None,
    ledger: KeyLedger | None,
) -> Provider:
    """The same chain over an OpenAI-compatible server. A server of your own may need no key: with none
    configured one provider without credentials is built."""
    from subcore.providers.openai_compat import OpenAICompatProvider

    base_url = str(settings.get("llm_base_url") or "").strip()
    if not base_url:
        raise MissingApiKey("The model server's base URL is not configured (Settings: llm_base_url)")
    if keys is None:
        entries: list[tuple[int | None, str]] = [(None, str(settings.get("openai_compat_api_key") or "").strip())]
    else:
        entries = [(key.id, key.value) for key in keys if key.value] or [(None, "")]
    caps = Capabilities(
        max_input_tokens=_int(settings.get("llm_max_input_tokens"), 128_000) or 128_000,
        max_output_tokens=_int(settings.get("llm_max_output_tokens"), 8_192) or 8_192,
        structured_output=True,
    )
    rpm, tpm, rpd = rate_limits(settings)
    providers: list[Provider] = []
    for key_id, value in entries:
        try:
            inner: Provider = OpenAICompatProvider(base_url, value, model, caps=caps)
        except ValueError as exc:
            raise MissingApiKey(f"The model server is not usable: {exc}") from None
        if ledger is not None and key_id is not None:
            inner = KeyedProvider(inner, key_id, ledger)
        providers.append(
            ResilientProvider(inner, RateLimiter(rpm, tpm, rpd), deadline=deadline, on_call=on_call)
        )
    return providers[0] if len(providers) == 1 else FailoverProvider(providers)


class LiveProviderFactory:
    """The provider factory of the running worker: it takes the keys from the stored ones each time it builds a
    provider, so a key pasted or retested in the console is used by the very next call (FR-20, ADR-13)."""

    def __init__(self, keys: Any) -> None:
        self.keys = keys  # a SettingsService

    def __call__(
        self,
        settings: dict[str, Any],
        model: str,
        *,
        deadline: float | None = None,
        on_call: Callable[[CallLog], None] | None = None,
    ) -> Provider:
        provider = provider_name(settings)
        usable = self.keys.usable_keys(provider)
        if not usable:
            if self.keys.repo.api_keys(provider):
                raise MissingApiKey(
                    f"Every {provider} API key was refused. Paste a new key in Settings, or test the key again."
                )
            if provider != PROVIDER_OPENAI_COMPAT:  # a server of your own can run without a key
                raise MissingApiKey("Gemini API key is not configured")
        return build_provider(settings, model, deadline=deadline, on_call=on_call, keys=usable, ledger=self.keys)
