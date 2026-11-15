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

from subcore import fewshot, srt
from subcore.memory.facts import EXTRACT_PROMPT_VERSION
from subcore.providers import (
    AuthError,
    CallLog,
    DeadlineExceeded,
    ModelNotFound,
    Provider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from subcore.translate import TranslationError, run
from subcore.translate.engine import TranslationResult
from subcore.translate.prompt import PROMPT_VERSION as SUBCORE_PROMPT_VERSION
from subcore.translate.review import Finding, review

from .config import DEFAULT_MODEL, model_for_task
from .gather import gather
from .library import Library
from .memory import StoryMemory
from .ports import MissingApiKey, ProviderFactory, Telemetry, Unit
from .store import Store
from .rules import UnitRules
from .subtitles import target_output_path
from .telemetry import Telemetry as TelemetrySink
from .queue_policy import next_utc_midnight
from .translation import (
    ERROR_TAIL_LENGTH, CallBudgetError, DailyQuotaExceededError, HardJobError, ModelNotFoundError, ProviderAuthError,
    ProviderUnavailableError, redact,
)
from .versions import AUTO, RETRANSLATE, SKIPPED_EXISTING, VARIANT, TranslationVersions, sha256_bytes

# Bump when the prompt or engine behaviour changes enough that old results should not be reused (NFR-3).
PROMPT_VERSION = SUBCORE_PROMPT_VERSION
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


class _Budget:
    """The model calls one job may make, and the day's calls all jobs may make (FR-17.6)."""

    def __init__(self, per_job: int, per_day: int, used_today: Callable[[float], int] | None, clock=time.time) -> None:
        self.per_job, self.per_day, self.used_today, self.clock = per_job, per_day, used_today, clock
        self.calls = 0

    def spend(self) -> None:
        if self.per_job > 0 and self.calls >= self.per_job:
            raise CallBudgetError(f"the job already made {self.calls} model calls (max_calls_per_job={self.per_job})", "job")
        if self.per_day > 0 and self.used_today is not None:
            now = self.clock()
            if self.used_today(next_utc_midnight(now) - 86_400) >= self.per_day:
                raise CallBudgetError(f"the day's {self.per_day} model calls are spent (max_calls_per_day)", "day")
        self.calls += 1


class _BudgetedProvider:
    """A provider that counts its calls against a budget before making them."""

    def __init__(self, inner: Provider, budget: _Budget) -> None:
        self.inner, self.budget = inner, budget

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def caps(self):
        return self.inner.caps

    def count_tokens(self, req):
        return self.inner.count_tokens(req)

    def generate(self, req):
        self.budget.spend()
        return self.inner.generate(req)


def _int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(settings.get(key) if settings.get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        return default


class TranslationAttempt:
    """Runs one translation job. Wired with versions and a usage log, or bare (no history, no cache, no call log).

    With a ``library`` the attempt also knows which episode it is translating, so the glossary and the
    episode's corrections shape the prompt and the published version is linked to its unit (P3).
    """

    def __init__(
        self,
        versions: TranslationVersions | None = None,
        usage: Store | None = None,
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
        # What the admin asked for by hand brings a removed subtitle back; the policy and a leftover of Bazarr's
        # script do not. (`admission` is the mark jobs of an older worker still carry.)
        manual = trigger != AUTO or str(job.get("origin") or "") == "manual" or job.get("admission") == "admin"
        if self.versions is not None and not manual and self.versions.latest_status(output) == "removed":
            self.telemetry.event(
                "translation.skipped", job_id=job_id, unit_id=unit_id, target=target_code,
                reason="the admin removed this subtitle; only a manual request brings it back (FR-16.6)",
            )
            return SKIPPED_EXISTING
        # The version in force when the job starts: a replace job publishes over it only if it is still that one
        # (FR-16.3). Taken here, not when the job was queued, so a wait does not make every edit a conflict.
        base = self.versions.repo.current_version(output) if self.versions is not None else None
        base_version = base.version if base else None

        source_sha = sha256_bytes(source.read_bytes())
        cues = srt.parse_file(source)
        if not cues:
            raise RuntimeError(f"Source subtitle has no cues: {source.name}")

        retranslate = dict(job.get("retranslate") or {}) if trigger == RETRANSLATE else {}
        variant = dict(job.get("variant") or {}) if trigger == VARIANT else {}
        rules = self._rules(unit, target_code, cues, retranslate.get("correction_ids"), variant)
        # How this version was made, when its other fields do not say: a retranslated range (FR-11.3.6), a variant's style.
        params: dict[str, Any] = {}
        if retranslate.get("range"):
            params["range"] = dict(retranslate["range"])
        if variant:
            params["variant"] = {k: variant.get(k) for k in ("label", "preset", "style")}
        model = str(settings.get("gst_model") or os.getenv("GST_MODEL", DEFAULT_MODEL))
        temperature = _temperature(settings)

        # The bundle of this attempt, once gathered: calls made for the translation carry its hash (NFR-12).
        current: dict[str, str] = {"bundle_hash": ""}

        def record(log: CallLog) -> None:
            self.telemetry.llm_call(
                job_id, log, prompt_version=prompt_version_of(log.task), glossary_hash=rules.hash,
                bundle_hash=current["bundle_hash"] if log.task == "translate" else "",
                style_hash=rules.style_hash if log.task == "translate" else "",
            )

        deadline = time.monotonic() + job_timeout_seconds()
        providers: dict[str, Provider] = {}
        budget = _Budget(
            _int_setting(settings, "max_calls_per_job", 0), _int_setting(settings, "max_calls_per_day", 0),
            getattr(self.usage, "calls_since", None),
        )

        def provider_for(task: str) -> Provider:
            """One provider (and rate limiter) per model; each task has its own model setting (FR-9.3)."""
            name = model if task == "translate" else model_for_task(settings, task, model)
            if name not in providers:
                if self.provider_factory is None:
                    raise RuntimeError("no provider factory is wired into this translation attempt")
                providers[name] = _BudgetedProvider(
                    self.provider_factory(settings, name, deadline=deadline, on_call=record), budget
                )
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
            job_id=job_id, base_version=base_version, params=params,
        )

        if self.versions is not None and trigger == AUTO and not rules.locked:
            cached = self.versions.cached_text(source_sha, target_code, ctx_hash)
            if cached is not None:
                logging.info("Reusing stored translation for %s (same input and context); no model call", output)
                self.telemetry.event(
                    "translation.cached", job_id=job_id, unit_id=unit_id, target=target_code, model=model,
                    reason="the same source and context were translated before; the stored result is reused",
                )
                return self.versions.publish(output, cached, **published, **self._source_moved(source, source_sha)).status

        only, keep = self._plan(retranslate, rules, cues, output)

        try:
            result, bundle = self._translate(
                provider_for, cues, job, description, settings, rules, plan, unit, only, keep, temperature, on_progress,
                current,
            )
        except HardJobError as exc:
            self._record_issues(getattr(exc, "issues", None), job_id, unit_id, model)
            raise

        self._record_issues(result.issues, job_id, unit_id, model)
        findings = self._review(bundle, result, settings)
        self.telemetry.event(
            "translation.finished", job_id=job_id, unit_id=unit_id, target=target_code, model=model,
            reason=f"{len(result.calls)} model call(s), {sum(1 for c in result.calls if c.kind != 'translate')} repair or split",
            calls=len(result.calls), tokens_in=result.tokens_in, tokens_out=result.tokens_out,
            issues=len(result.issues), trigger=trigger,
            review_errors=sum(1 for f in findings if f.severity == "error"),
            review_warnings=sum(1 for f in findings if f.severity == "warning"),
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
            output, text, tokens_in=result.tokens_in, tokens_out=result.tokens_out, **published,
            **self._source_moved(source, source_sha),
        ).status

    @staticmethod
    def _source_moved(source: Path, source_sha: str) -> dict[str, str]:
        """A result made from a source that changed meanwhile is not published over the target (FR-15.10)."""
        try:
            moved = sha256_bytes(source.read_bytes()) != source_sha
        except OSError:
            moved = True  # the source vanished: nothing must go on disk from it
        if not moved:
            return {}
        return {"candidate_reason": "the source subtitle changed while the job ran; the result waits as a candidate"}

    def _translate(
        self, provider_for, cues, job, description, settings, rules, plan, unit, only, keep, temperature, on_progress,
        current: dict[str, str],
    ) -> tuple[TranslationResult, Any]:
        """The model work of one attempt: gather the bundle, keep it, run it through ``subcore``."""
        target_code = str(job.get("target_code") or "zh")
        with self._mapped(settings):
            provider = provider_for("translate")
            bundle = gather(
                cues=cues,
                rules=rules,
                unit=unit,
                title=self._title(unit),  # the series, or the movie
                overview=description,
                target_code=target_code,
                target_name=str(job.get("target_language") or ""),
                source_code=str(job.get("source_code") or ""),
                extra_sections=plan.sections if plan else [],
                model=str(getattr(provider, "model", "") or ""),
                max_output_tokens=provider.caps.max_output_tokens,
                temperature=temperature,
                only=only,
                keep=keep,
                few_shot=self._few_shot(unit, target_code, cues, only, keep, settings),
                glossary_filter_threshold=_int_setting(settings, "glossary_filter_threshold", 50),
            )
            bundle = self._clean(bundle, settings)
            current["bundle_hash"] = bundle.bundle_hash()
            self._keep_bundle(job, bundle, settings)
            result = run(bundle, provider, on_progress=on_progress)
        return result, bundle

    def _few_shot(self, unit, target_code: str, cues, only, keep, settings: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        """Lines an admin corrected elsewhere in this series that look like the ones now being translated (FR-18.5).

        Taken from the series' cue edits, never from this episode's own (those are kept as they are). They go in the
        bundle and its hash, not in the rules hash: a fixed cue must not make the whole series stale (ADR-14 point 9)."""
        budget = _int_setting(settings, "few_shot_budget_tokens", 600)
        if budget <= 0 or unit is None or unit.series_id is None or self.library is None:
            return ()
        edits = self.library.catalog.series_cue_edits(unit.series_id, target_code, exclude_unit=unit.id)
        if not edits:
            return ()
        wanted = range(1, len(cues) + 1) if only is None else only
        texts = [cues[n - 1].text for n in wanted if 1 <= n <= len(cues) and n not in keep]
        return fewshot.select([(e.source_text, e.text) for e in edits], texts, budget)

    @staticmethod
    def _review(bundle, result: TranslationResult, settings: dict[str, Any]) -> list[Finding]:
        """The same review ``subcore validate`` makes, against the glossary the engine used (the bundle's, narrowed to
        the episode). The translation is already paid for: a failure here is logged and costs nothing else."""
        try:
            return review(list(bundle.cues), result.cues, bundle.glossary)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Review of the translation failed: %s", redact(str(exc), settings))
            return []

    @staticmethod
    def _clean(bundle, settings: dict[str, Any]):
        """Mask a key that reached the text (a pasted overview, a correction) before anything else sees the bundle.

        The model never receives it, and the hash, the call records and the exported spec all describe the same text,
        so a spec reproduces the run (NFR-12). When masking leaves nothing readable the bundle is kept as it was.
        """
        text = bundle.to_json()
        masked = redact(text, settings)
        if masked == text:
            return bundle
        try:
            return type(bundle).from_json(masked)
        except Exception:  # noqa: BLE001 - a bundle that cannot be read back is not worth changing
            return bundle

    def _keep_bundle(self, job: dict[str, Any], bundle, settings: dict[str, Any]) -> None:
        """Keep the bundle for "Xuất job spec" and put its hash in the job's snapshot (FR-15.10, FR-21.2).

        Best effort: a failure here is logged and the translation goes on; the bundle is never a reason to fail a job.
        """
        bundle_hash = bundle.bundle_hash()
        snapshot = job.get("snapshot")
        if isinstance(snapshot, dict):
            snapshot["bundle_hash"] = bundle_hash
        job_id = str(job.get("job_id") or "")
        if self.usage is None or not job_id:
            return
        try:
            self.usage.save_job_bundle(job_id, bundle_hash, bundle.to_json())
        except Exception as exc:  # noqa: BLE001 - a bundle that cannot be kept must not fail the job
            logging.warning("Could not keep the bundle of job %s: %s", job_id, redact(str(exc), settings))

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
            raise ProviderAuthError(self._message("The provider rejected the API key", exc, settings)) from None
        except ModelNotFound as exc:
            raise ModelNotFoundError(
                self._message("The provider does not know the model (check the model in Settings)", exc, settings),
                model=str(settings.get("gst_model") or ""),
            ) from None
        except MissingApiKey as exc:
            raise RuntimeError(str(exc)) from None
        except TranslationError as exc:
            error = HardJobError(self._message("Translation failed", exc, settings))
            error.issues = list(getattr(exc, "issues", ()))  # type: ignore[attr-defined]
            error.blocked = int(getattr(exc, "blocked", 0) or 0)  # type: ignore[attr-defined]
            raise error from None
        except ProviderError as exc:
            raise RuntimeError(self._message("Translation failed", exc, settings)) from None

    # -- library ---------------------------------------------------------------
    def _register(self, job: dict[str, Any]) -> Unit | None:
        if self.library is None:
            return None
        unit_id = self.library.register_job(job)
        return self.library.catalog.get_unit(unit_id)

    def _rules(self, unit, target_code: str, cues, correction_ids, variant: dict[str, Any] | None = None) -> UnitRules:
        if self.library is None or unit is None:
            return UnitRules()
        layers = self.library.variant_layers(unit, variant) if variant else None
        return self.library.unit_rules(unit, target_code, cues=cues, correction_ids=correction_ids, styles=layers)

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

