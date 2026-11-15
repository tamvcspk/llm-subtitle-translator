"""One execution of translation work: read the source, call ``subcore`` in-process, publish the result."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from subcore import srt
from subcore.memory.facts import EXTRACT_PROMPT_VERSION
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

from .config import DEFAULT_MODEL
from .library import Library
from .memory import StoryMemory
from .ports import MissingApiKey, ProviderFactory, Telemetry, Unit, UsageLog
from .rules import UnitRules
from .subtitles import target_output_path
from .telemetry import Telemetry as TelemetrySink
from .translation import ERROR_TAIL_LENGTH, DailyQuotaExceededError, HardJobError, ProviderUnavailableError, redact
from .versions import AUTO, RETRANSLATE, SKIPPED_EXISTING, TranslationVersions, sha256_bytes

# Bump when the prompt or engine behaviour changes enough that old results should not be reused (NFR-3).
PROMPT_VERSION = "p3-1"
DEFAULT_JOB_TIMEOUT_SECONDS = 1800  # NFR-2 watchdog



def prompt_version_of(task: str) -> str:
    """The version of the prompt a call of this task used, for the call record (FR-19.1)."""
    if task == "translate":
        return PROMPT_VERSION
    if task in ("extract", "summarize"):
        return f"memory-{EXTRACT_PROMPT_VERSION}"
    return ""


def job_timeout_seconds() -> float:
    try:
        return max(60.0, float(os.getenv("JOB_TIMEOUT_SECONDS", DEFAULT_JOB_TIMEOUT_SECONDS)))
    except ValueError:
        return float(DEFAULT_JOB_TIMEOUT_SECONDS)


def context_hash(
    model: str, target_code: str, description: str, temperature: float | None, rules_hash: str = "",
    memory_hash: str = "",
) -> str:
    """Everything besides the source text that changes what the model is asked (NFR-3)."""
    parts = [PROMPT_VERSION, model, target_code, description, temperature, rules_hash]
    if memory_hash:  # absent without memory, so those hashes stay what they were before P4
        parts.append(memory_hash)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _temperature(settings: dict[str, Any]) -> float | None:
    try:
        return float(str(settings.get("gst_temperature") or "").strip())
    except ValueError:
        return None


class TranslationAttempt:
    """Runs one translation job. Wired with versions and a usage log, or bare (no history, no cache, no call log).

    With a ``library`` the attempt also knows which episode it is translating, so the glossary and the
    episode's corrections shape the prompt and the published version is linked to its unit (P3).
    """

    def __init__(
        self,
        versions: TranslationVersions | None = None,
        usage: UsageLog | None = None,
        provider_factory: ProviderFactory | None = None,
        library: Library | None = None,
        memory: StoryMemory | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.versions = versions
        self.usage = usage
        self.provider_factory = provider_factory
        self.library = library
        self.memory = memory
        # Without a shared telemetry the attempt still keeps its plain call record (synchronously, as before P5).
        self.telemetry: Telemetry = telemetry or TelemetrySink(usage=usage, synchronous=True)

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
        unit = self._register(job)
        job_id = str(job.get("job_id") or "")
        unit_id = unit.id if unit else None
        if trigger == AUTO and Path(output).exists():
            self.telemetry.event(
                "translation.skipped", job_id=job_id, unit_id=unit_id, target=target_code,
                reason="a target subtitle already exists; an automatic job never overwrites it",
            )
            return SKIPPED_EXISTING

        source_sha = sha256_bytes(source.read_bytes())
        cues = srt.parse_file(source)
        if not cues:
            raise RuntimeError(f"Source subtitle has no cues: {source.name}")

        retranslate = dict(job.get("retranslate") or {}) if trigger == RETRANSLATE else {}
        rules = self._rules(unit, target_code, cues, retranslate.get("correction_ids"))
        model = str(settings.get("gst_model") or os.getenv("GST_MODEL", DEFAULT_MODEL))
        temperature = _temperature(settings)

        def record(log: CallLog) -> None:
            self.telemetry.llm_call(
                job_id, log, prompt_version=prompt_version_of(log.task), glossary_hash=rules.hash,
            )

        deadline = time.monotonic() + job_timeout_seconds()
        providers: dict[str, Provider] = {}

        def provider_for(task: str) -> Provider:
            """One provider (and rate limiter) per model; extraction may use a cheaper model (FR-9.3)."""
            name = model if task == "translate" else (str(settings.get("memory_model") or "").strip() or model)
            if name not in providers:
                if self.provider_factory is None:
                    raise RuntimeError("no provider factory is wired into this translation attempt")
                providers[name] = self.provider_factory(settings, name, deadline=deadline, on_call=record)
            return providers[name]

        # Story memory (P4): earlier episodes' facts are made sure of first, so the bundle is complete
        # whatever order Bazarr delivered the episodes in (FR-5.3). A gap defers the job (ContextGapError).
        plan = None
        if self.memory is not None and unit is not None and self.memory.enabled_for(unit, settings):
            with self._mapped(settings):
                plan = self.memory.plan(unit, job, settings, provider_for)
        ctx_hash = context_hash(model, target_code, description, temperature, rules.hash, plan.fingerprint if plan else "")
        published = dict(
            trigger=trigger, source_path=str(source), source_sha256=source_sha,
            target_lang=target_code, context_hash=ctx_hash, model=model,
            unit_id=unit.id if unit else None, glossary_hash=rules.hash,
            memory_hash=plan.fingerprint if plan else "",
            context_complete=plan.complete if plan else True,
            missing_units=plan.missing if plan else [],
            job_id=job_id,
        )

        if self.versions is not None and trigger == AUTO and not rules.locked:
            cached = self.versions.cached_text(source_sha, target_code, ctx_hash)
            if cached is not None:
                logging.info("Reusing stored translation for %s (same input and context); no model call", output)
                self.telemetry.event(
                    "translation.cached", job_id=job_id, unit_id=unit_id, target=target_code, model=model,
                    reason="the same source and context were translated before; the stored result is reused",
                )
                return self.versions.publish(output, cached, **published).status

        only, keep = self._plan(retranslate, rules, cues, output)

        try:
            result = self._translate(
                provider_for, cues, job, description, settings, rules, plan, unit, only, keep, temperature, on_progress,
            )
        except HardJobError as exc:
            self._record_issues(getattr(exc, "issues", None), job_id, unit_id, model)
            raise

        self._record_issues(result.issues, job_id, unit_id, model)
        self.telemetry.event(
            "translation.finished", job_id=job_id, unit_id=unit_id, target=target_code, model=model,
            reason=f"{len(result.calls)} model call(s), {sum(1 for c in result.calls if c.kind != 'translate')} repair or split",
            calls=len(result.calls), tokens_in=result.tokens_in, tokens_out=result.tokens_out,
            issues=len(result.issues), trigger=trigger,
        )

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

    def _translate(
        self, provider_for, cues, job, description, settings, rules, plan, unit, only, keep, temperature, on_progress,
    ):
        """The model work of one attempt: build the prompt context, run ``subcore.translate``."""
        target_code = str(job.get("target_code") or "zh")
        with self._mapped(settings):
            provider = provider_for("translate")
            context = PromptContext(
                target_lang=target_code,
                target_name=str(job.get("target_language") or "") or None,
                source_lang=str(job.get("source_code") or "") or None,
                overview=description,
                glossary=rules.glossary if not rules.glossary.is_empty() else None,
                active_rules=rules.active_rules,
                # Corrections come before the machine's story so they win; both follow the glossary.
                extra_sections=[*rules.extra_sections, *(plan.sections if plan else [])],
                season=unit.season if unit else None,
                episode=unit.episode if unit else None,
                title=self._title(unit),  # the series, or the movie
            )
            return translate(
                provider, cues, context, TranslateOptions(temperature=temperature),
                on_progress=on_progress, only=only, keep=keep,
            )

    MAX_ISSUE_SIGNALS = 500  # per attempt: a source that fails everywhere must not flood the table

    def _record_issues(self, issues, job_id: str, unit_id: int | None, model: str) -> None:
        """One ``validate`` quality signal per problem the validator found, repaired or not (FR-19.4)."""
        for issue in list(issues or [])[: self.MAX_ISSUE_SIGNALS]:
            self.telemetry.signal(
                "validate", unit_id=unit_id, cue=issue.cue, before=issue.source, after=issue.candidate,
                issue=issue.kind, call=issue.call, error=issue.error, detail=issue.detail, job_id=job_id, model=model,
            )

    @contextmanager
    def _mapped(self, settings: dict[str, Any]):
        """Turn typed provider failures into the queue's failure classes (never by parsing text)."""
        try:
            yield
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
        except TranslationError as exc:
            error = HardJobError(self._message("Translation failed", exc, settings))
            error.issues = list(getattr(exc, "issues", ()))  # type: ignore[attr-defined]
            raise error from None
        except ProviderError as exc:
            raise RuntimeError(self._message("Translation failed", exc, settings)) from None

    # -- library ---------------------------------------------------------------
    def _register(self, job: dict[str, Any]) -> Unit | None:
        if self.library is None:
            return None
        unit_id = self.library.register_job(job)
        return self.library.catalog.get_unit(unit_id)

    def _rules(self, unit, target_code: str, cues, correction_ids) -> UnitRules:
        if self.library is None or unit is None:
            return UnitRules()
        return self.library.unit_rules(unit, target_code, cues=cues, correction_ids=correction_ids)

    def _title(self, unit) -> str:
        if not unit:
            return ""
        if unit.series_id is not None and self.library is not None:
            series = self.library.catalog.get_series(unit.series_id)
            return series.title if series else ""
        return unit.title

    @staticmethod
    def _plan(retranslate: dict[str, Any], rules: UnitRules, cues, output: str):
        """Which cues to request (``only``) and which to carry over unchanged (``keep``).

        A range retranslation keeps every cue outside the range exactly as the current target has it
        (FR-11.3.2); locked cue edits are kept in every case (FR-11.2.4).
        """
        keep: dict[int, str] = {}
        only = None
        window = retranslate.get("range")
        if window:
            first, last = int(window["first"]), int(window["last"])
            try:
                current = srt.parse_file(output)
            except (OSError, srt.SrtError):
                raise RuntimeError("The current target subtitle cannot be read; retranslate the whole episode") from None
            if len(current) != len(cues):
                raise RuntimeError(
                    "The current target subtitle has a different number of cues than the source; "
                    "retranslate the whole episode"
                )
            only = range(first, last + 1)
            keep = {n: cue.text for n, cue in enumerate(current, start=1) if not first <= n <= last}
        keep.update(rules.locked)
        return only, keep

    @staticmethod
    def _message(prefix: str, exc: BaseException, settings: dict[str, Any]) -> str:
        detail = redact(f"{type(exc).__name__}: {exc}", settings)
        return f"{prefix}: {detail}"[-ERROR_TAIL_LENGTH:]

