"""One execution of translation work: read the source, call ``subcore`` in-process, publish the result."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from subcore import srt
from subcore.providers import (
    AuthError,
    CallLog,
    DeadlineExceeded,
    Provider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from subcore.translate import PromptContext, TranslateOptions, TranslationError, translate

from .llm import MissingApiKey, build_provider
from .store import Store
from .subtitles import target_output_path
from .translation import ERROR_TAIL_LENGTH, DailyQuotaExceededError, ProviderUnavailableError, redact
from .versions import AUTO, SKIPPED_EXISTING, TranslationVersions, sha256_bytes

# Bump when the prompt or engine behaviour changes enough that old results should not be reused (NFR-3).
PROMPT_VERSION = "p2-1"
DEFAULT_JOB_TIMEOUT_SECONDS = 1800  # NFR-2 watchdog

ProviderFactory = Callable[..., Provider]


def job_timeout_seconds() -> float:
    try:
        return max(60.0, float(os.getenv("JOB_TIMEOUT_SECONDS", DEFAULT_JOB_TIMEOUT_SECONDS)))
    except ValueError:
        return float(DEFAULT_JOB_TIMEOUT_SECONDS)


def context_hash(model: str, target_code: str, description: str, temperature: float | None) -> str:
    """Everything besides the source text that changes what the model is asked (NFR-3)."""
    payload = json.dumps(
        [PROMPT_VERSION, model, target_code, description, temperature], ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _temperature(settings: dict[str, Any]) -> float | None:
    try:
        return float(str(settings.get("gst_temperature") or "").strip())
    except ValueError:
        return None


class TranslationAttempt:
    """Runs one translation job. Wired with a store/versions pair, or bare (no history, no cache)."""

    def __init__(
        self,
        versions: TranslationVersions | None = None,
        store: Store | None = None,
        provider_factory: ProviderFactory = build_provider,
    ) -> None:
        self.versions = versions
        self.store = store if store is not None else (versions.store if versions else None)
        self.provider_factory = provider_factory

    def run(
        self,
        job: dict[str, Any],
        description: str,
        settings: dict[str, Any],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        source = Path(str(job["subtitle_path"]))
        target_code = str(job.get("target_code") or "zh")
        output = str(job.get("output_path") or target_output_path(str(source), target_code))
        trigger = str(job.get("trigger") or AUTO)
        if trigger == AUTO and Path(output).exists():
            return SKIPPED_EXISTING

        source_sha = sha256_bytes(source.read_bytes())
        model = str(settings.get("gst_model") or os.getenv("GST_MODEL", "gemini-flash-latest"))
        temperature = _temperature(settings)
        ctx_hash = context_hash(model, target_code, description, temperature)
        published = dict(
            trigger=trigger, source_path=str(source), source_sha256=source_sha,
            target_lang=target_code, context_hash=ctx_hash, model=model,
        )

        if self.versions is not None and trigger == AUTO:
            cached = self.versions.cached_text(source_sha, target_code, ctx_hash)
            if cached is not None:
                logging.info("Reusing stored translation for %s (same input and context); no model call", output)
                return self.versions.publish(output, cached, **published).status

        cues = srt.parse_file(source)
        if not cues:
            raise RuntimeError(f"Source subtitle has no cues: {source.name}")

        job_id = str(job.get("job_id") or "")

        def record(log: CallLog) -> None:
            if self.store is not None:
                self.store.record_llm_call(
                    job_id, provider=log.provider, model=log.model, task=log.task,
                    tokens_in=log.tokens_in, tokens_out=log.tokens_out,
                    latency_ms=log.latency_ms, outcome=log.outcome,
                )

        deadline = time.monotonic() + job_timeout_seconds()
        try:
            provider = self.provider_factory(settings, model, deadline=deadline, on_call=record)
            context = PromptContext(
                target_lang=target_code,
                target_name=str(job.get("target_language") or "") or None,
                source_lang=str(job.get("source_code") or "") or None,
                overview=description,
            )
            result = translate(
                provider, cues, context, TranslateOptions(temperature=temperature), on_progress=on_progress
            )
        except QuotaExhausted as exc:
            raise DailyQuotaExceededError(self._message("Daily quota exhausted", exc, settings)) from None
        except (RateLimited, Unavailable) as exc:
            raise ProviderUnavailableError(self._message("Provider unavailable", exc, settings)) from None
        except DeadlineExceeded:
            raise RuntimeError(
                f"Translation exceeded the {int(job_timeout_seconds() // 60)} minute job limit"
            ) from None
        except AuthError as exc:
            raise RuntimeError(self._message("The provider rejected the API key", exc, settings)) from None
        except MissingApiKey as exc:
            raise RuntimeError(str(exc)) from None
        except (TranslationError, ProviderError) as exc:
            raise RuntimeError(self._message("Translation failed", exc, settings)) from None

        for warning in result.warnings:
            logging.warning("Job %s: %s", job_id or source.name, redact(warning, settings))
        text = srt.serialize(result.cues)
        if self.versions is None:
            if Path(output).exists():
                return SKIPPED_EXISTING
            srt.write_atomic(output, text)
            return "translated"
        return self.versions.publish(
            output, text, tokens_in=result.tokens_in, tokens_out=result.tokens_out, **published
        ).status

    @staticmethod
    def _message(prefix: str, exc: BaseException, settings: dict[str, Any]) -> str:
        detail = redact(f"{type(exc).__name__}: {exc}", settings)
        return f"{prefix}: {detail}"[-ERROR_TAIL_LENGTH:]


DEFAULT_ATTEMPT = TranslationAttempt()
