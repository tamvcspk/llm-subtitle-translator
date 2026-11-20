"""The jobs that read and do not translate: what a worker runs for an ``extract`` job and for a ``suggest`` job.

The queue has already picked the model, claimed the job under the day's quota and the breaker, and will map whatever
a handler raises: a handler only does its work and lets typed provider errors through ``mapped_errors`` so the queue
sees the same classes it sees from a translation (F7). Nothing here classifies a failure by its text.
"""

from __future__ import annotations

from typing import Any, Callable

from .config import DEFAULT_MODEL, model_for_task
from .memory import StoryMemory, unit_label
from .proposals import FactsProposer
from .ports import ProviderFactory, Telemetry
from .provider_guard import JobProviders, mapped_errors, prompt_version_of
from .telemetry import Telemetry as TelemetrySink

StatusCallback = Callable[..., None]


class ExtractJob:
    """Reads the facts of one episode (``extract`` job). One job, one episode, so the queue can stop between episodes,
    let a translation go in between and keep what was read when the worker restarts."""

    def __init__(
        self,
        memory: StoryMemory,
        provider_factory: ProviderFactory | None,
        *,
        settings_source: Callable[[], dict[str, Any]],
        usage: Any = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.memory = memory
        self.provider_factory = provider_factory
        self.settings_source = settings_source
        self.usage = usage
        # Without a shared telemetry the job still keeps its plain call record (the day's call count reads it).
        self.telemetry: Telemetry = telemetry or TelemetrySink(usage=usage, synchronous=True)

    def __call__(self, job: dict[str, Any], update_status: StatusCallback) -> str:
        settings = self.settings_source()
        if job.get("gst_model"):  # the queue picks the model per attempt; the configured one is the default
            settings = {**settings, "gst_model": str(job["gst_model"])}
        unit = self.memory.units.get_unit(int(job.get("unit_id") or 0))
        if unit is None:
            raise RuntimeError("The episode is no longer in the library")
        series = self.memory.units.get_series(unit.series_id) if unit.series_id is not None else None
        label = unit_label(unit)
        update_status(f"Reading {label}")
        job_id = str(job.get("job_id") or "")
        model = str(settings.get("gst_model") or DEFAULT_MODEL)

        def record(log) -> None:
            self.telemetry.llm_call(job_id, log, prompt_version=prompt_version_of(log.task, getattr(log, "kind", "") or ""))

        providers = JobProviders(self.provider_factory, settings, model, usage=self.usage, on_call=record)
        why: list[str] = []
        with mapped_errors(settings, model_for_task(settings, "extract", model)):
            _, how = self.memory.ensure_facts(unit, series.title if series else unit.title, settings, providers.for_task, why)
        if how == "no-source":
            return "skipped-no-source"  # nothing to read: the job ends without a call and without a fault
        if how == "failed" or why:
            reason = why[0] if why else "the source subtitle could not be read"
            raise RuntimeError(f"The facts of {label} could not be read: {reason}")
        return how  # extracted, cached or stored


class SuggestJob:
    """Looks for proposals in the stored facts of one series for one language (``suggest`` job): at most three calls (address
    rules, terms, characters), each kind stored as soon as it comes back, so a run that stops loses nothing it paid for and
    running it again asks only about what is left (R13-4). Only the queue decides when it may run."""

    def __init__(
        self,
        proposer: FactsProposer,
        provider_factory: ProviderFactory | None,
        *,
        settings_source: Callable[[], dict[str, Any]],
        usage: Any = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.proposer = proposer
        self.provider_factory = provider_factory
        self.settings_source = settings_source
        self.usage = usage
        self.telemetry: Telemetry = telemetry or TelemetrySink(usage=usage, synchronous=True)

    def __call__(self, job: dict[str, Any], update_status: StatusCallback) -> str:
        settings = self.settings_source()
        if job.get("gst_model"):
            settings = {**settings, "gst_model": str(job["gst_model"])}
        series_id, lang = int(job.get("stored_series_id") or 0), str(job.get("target_code") or "")
        update_status("Looking for proposals")
        job_id = str(job.get("job_id") or "")
        model = str(settings.get("gst_model") or DEFAULT_MODEL)

        def record(log) -> None:
            self.telemetry.llm_call(job_id, log, prompt_version=prompt_version_of(log.task, getattr(log, "kind", "") or ""))

        providers = JobProviders(self.provider_factory, settings, model, usage=self.usage, on_call=record)
        report: dict[str, Any] = {}
        try:
            with mapped_errors(settings, model_for_task(settings, "extract", model)):
                self.proposer.run(series_id, lang, providers.for_task, report)
        finally:
            job["report"] = report  # what was stored before a stop or a failure is told as well as what was stored after a finish
        return "proposed" if report.get("added") else "nothing-new"
