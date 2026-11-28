#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from subserver.adapters.backups import BackupMaintenance, create_scheduled_backup_if_due, purge_old_backups
from subserver.services.auth import ConsoleAuth
from subserver.services.cleanup import Cleanup
from subserver.services.auto_request import AutoRequest, sync_and_request
from subserver.adapters.bazarr import BazarrIntegration, BazarrMedia
from subserver.services.catalog import CatalogPaths, CatalogSync
from subserver.services.catalog_actions import CatalogActions
from subserver.services.catalog_view import CatalogView
from subserver.adapters.logs import configure_logging
from subserver.adapters.http import HTTPClient, HTTPTransport, JsonFileCache, MemoryCache
from subserver.domain.config import enabled_source_languages, provider_name
from subserver.jobs.intake import import_queue_tree
from subserver.services.job_requests import JobRequests
from subserver.jobs.jobs import EXTRACT, JUDGE, PUBLISH, RESEARCH, SUGGEST, VARIANT
from subserver.services.judge import JudgeService
from subserver.jobs.queue import JobQueue, skip_reason
from subserver.jobs.read_jobs import ExtractJob, JudgeJob, ResearchJob, SuggestJob
from subserver.services.console import ConsoleActions
from subserver.services.glossaries import Glossaries
from subserver.services.style_service import StyleService
from subserver.services.styles import Styles
from subserver.services.proposals import FactsProposer
from subserver.services.research import Researcher
from subserver.adapters.redirects import RedirectResolver
from subserver.services.suggestions import SuggestionService
from subserver.services.library import Library, enrich_job_identity
from subserver.services.memory import StoryMemory
from subserver.domain.queue_policy import configured_models
from subserver.adapters.store import Store
from subserver.adapters.llm import LiveProviderFactory, build_provider, rate_limits
from subserver.services.feedback import FeedbackProposer
from subserver.api.insight_api import Insights
from subserver.services.metrics import Metrics
from subserver.jobs.translation_attempt import TranslationAttempt
from subserver.services.units import UnitService
from subserver.services.versions import TranslationVersions
from subserver.runtime import BackgroundLoop, RuntimeContext, start_loops
from subserver.domain.ports import MediaLibrary, Metadata, Telemetry
from subserver.adapters.bodies import BodyLog
from subserver.domain.redaction import DEFAULT as DEFAULT_REDACTOR, SECRET_SETTING_KEYS
from subserver.adapters.telemetry import NULL as NULL_TELEMETRY, Telemetry as TelemetrySink
from subserver.adapters.tmdb import TmdbMetadata
# The settings service and the HTTP adapter live in their own modules; they stay importable from here.
from subserver.adapters.settings import SettingsService
from subserver.api.web import create_app, start_web_server  # noqa: F401

# Used when `process_job` is called without an explicit attempt (no history, no library): the legacy entry point.
DEFAULT_ATTEMPT = TranslationAttempt(provider_factory=build_provider)


def process_job(
    job: dict[str, Any],
    settings: dict[str, Any],
    cache: MemoryCache,
    http: HTTPClient,
    status_callback: Callable[..., None] | None = None,
    attempt: TranslationAttempt | None = None,
    media: MediaLibrary | None = None,
    metadata: Metadata | None = None,
    telemetry: Telemetry | None = None,
) -> str:
    # The ports default to the adapters over the settings this job runs with (composition done here, not in core).
    media = media or BazarrMedia(http, lambda: settings)
    metadata = metadata or TmdbMetadata(http, cache, lambda: settings)
    telemetry = telemetry or NULL_TELEMETRY
    subtitle = Path(str(job.get("subtitle_path") or "")).name or None
    reason = skip_reason(job)
    if reason:
        logging.info("Skipping job for %s: %s", job.get("subtitle_path"), reason)
        telemetry.event("job.skipped", job_id=str(job.get("job_id") or ""), unit_id=job.get("unit_id") or None,
                        reason=reason, subtitle=subtitle, target=job.get("target_code"))
        return "skipped"
    # Bazarr decides what is wanted: when it says the language is not missing, spend no model call.
    if job.get("trigger") not in ("retranslate", "variant") and media.target_still_missing(job) is False:
        logging.info("Skipping job for %s: Bazarr does not list %s as missing",
                     job.get("subtitle_path"), job.get("target_code"))
        telemetry.event("job.skipped", job_id=str(job.get("job_id") or ""), unit_id=job.get("unit_id") or None,
                        reason=f"Bazarr no longer lists {job.get('target_code')} as missing for this item",
                        subtitle=subtitle, target=job.get("target_code"), cause="not-wanted")
        return "skipped-not-wanted"

    if status_callback:
        status_callback("Resolving movie details")
    if not job.get("unit_id"):
        enrich_job_identity(job, media)
    # Both lookups are handed over as callables: the attempt asks for the description only when the job is not skipped, and
    # for the cast only when Story memory is on for the series (D-08), so a job that needs neither costs no HTTP call.
    description = lambda: metadata.description(job)  # noqa: E731
    cast = (lambda: metadata.cast(job)) if hasattr(metadata, "cast") else None  # a port older than P19 has none: every rule stays
    if status_callback:
        status_callback("Sending subtitle batches to Gemini")
    # The queue picks primary or fallback per attempt; the configured model is the default.
    if job.get("gst_model"):
        settings = {**settings, "gst_model": str(job["gst_model"])}
    def report_progress(done: int, total: int) -> None:
        if status_callback:
            status_callback(f"Translating {done}/{total} cues", progress_done=done, progress_total=total)

    status = (attempt or DEFAULT_ATTEMPT).run(job, description, settings, on_progress=report_progress, cast=cast)
    if job.get("trigger") == "variant":
        return status  # nothing went on disk: Bazarr has nothing to rescan
    if status_callback:
        status_callback("Refreshing Bazarr")
    # The subtitle is already published: a Bazarr that is slow or down must not turn a finished translation into a
    # failed job (a rescan of a large series can outlast the HTTP timeout). Bazarr finds the file on its next scan.
    try:
        media.refresh(job)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Translated %s, but Bazarr could not be asked to rescan (%s: %s)",
                        job.get("subtitle_path"), type(exc).__name__, exc)
    return status


class QueueWorker:
    """Bind the queue's execution callback to the configured translation workflow."""

    def __init__(
        self,
        queue_dir: str,
        settings: dict[str, Any],
        cache: MemoryCache,
        http: HTTPClient,
        attempt: TranslationAttempt | None = None,
        media: MediaLibrary | None = None,
        metadata: Metadata | None = None,
        telemetry: Telemetry | None = None,
        store: Store | None = None,
        key_ok: Callable[[], bool] | None = None,
        library: Library | None = None,
    ) -> None:
        self.telemetry: Telemetry = telemetry or NULL_TELEMETRY
        self.queue_dir = queue_dir
        self.store = store or Store(":memory:")
        self.queue = JobQueue(self.store, self.telemetry, key_ok=key_ok)
        self.settings = settings
        self.cache = cache
        self.http = http
        self.attempt = attempt
        self.media = media
        self.metadata = metadata
        self.console = ConsoleActions(self.queue)
        self.handlers: dict[str, Callable[[dict[str, Any], Callable[..., None]], str]] = {
            PUBLISH: self.translate_job, VARIANT: self.translate_job,
        }
        # A waiting job finds its files again after Sonarr renamed them (FR-14.3).
        self.paths = CatalogPaths(self.store)
        self.queue.configure(settings, self.models(), provider_name(settings))
        # Start-up, explicitly: an older queue (folders, the script's gate) settles into the table once, then jobs a
        # stopped worker held go back in line (ADR-10).
        import_queue_tree(self.queue, queue_dir, library)
        self.queue.recover()

    def models(self) -> tuple[str, ...]:
        return configured_models(
            str(self.settings.get("gst_model") or ""), str(self.settings.get("gst_fallback_model") or "")
        )

    def apply_settings(self, settings: dict[str, Any]) -> None:
        """Adopt new settings; a newly configured model with quota left is usable at once."""
        old = self.settings
        self.settings = settings
        changed = sorted(key for key in {*old, *settings} if old.get(key) != settings.get(key))
        if changed:  # a secret is named, never shown, and a new key is not echoed back
            self.telemetry.event(
                "settings.changed", reason="the settings were saved", keys=changed[:40],
                changes={k: [old.get(k), settings.get(k)] for k in changed[:40] if k not in SECRET_SETTING_KEYS},
            )
        self.queue.configure(settings, self.models(), provider_name(settings))
        # The log level, the secrets to hide and the body limits follow the saved settings at once (FR-19.3).
        apply = getattr(self.telemetry, "apply_settings", None)
        if apply is not None:
            apply(settings)

    def register_handler(self, kind: str, handler: Callable[[dict[str, Any], Callable[..., None]], str]) -> None:
        """What runs a job of this kind. The composition root registers the kinds that are not translations (F7)."""
        self.handlers[kind] = handler

    def run_job(self, job: dict[str, Any], update_status: Callable[..., None]) -> str:
        kind = str(job.get("kind") or PUBLISH)
        handler = self.handlers.get(kind)
        if handler is None:
            raise RuntimeError(f"No handler is registered for jobs of kind {kind!r}")
        return handler(job, update_status)

    def translate_job(self, job: dict[str, Any], update_status: Callable[..., None]) -> str:
        """A ``publish`` or ``variant`` job: translate one subtitle."""
        self.paths.refresh(job)  # the library's own id says where the files are now, not the paths the job was made with
        return process_job(
            job, self.settings, self.cache, self.http, update_status, self.attempt,
            media=self.media, metadata=self.metadata, telemetry=self.telemetry,
        )

    def process_once(self, now: float | None = None) -> bool:
        return self.queue.process_once(
            self.run_job,
            settle_seconds=max(0, int(self.settings.get("job_settle_seconds") or 0)),
            now=now,
            models=self.models(),
            settings=self.settings,
        )

    def run_forever(self, sleep_seconds: int) -> None:
        while True:
            try:
                worked = self.process_once()
            except Exception:  # noqa: BLE001 - the loop must outlive one bad tick
                logging.exception("Queue tick failed")
                worked = False
            if not worked:
                time.sleep(sleep_seconds)


CATALOG_CHECK_SECONDS = 15


def run_catalog_sync(sync: CatalogSync, auto: AutoRequest, check_seconds: float = CATALOG_CHECK_SECONDS) -> None:
    """Keep the catalog as young as ``catalog_sync_interval`` says, and let the policy act after each read (ADR-10).
    One failing cycle never stops the loop. This loop is the only caller of ``sync_and_request``: "Sync now" in the
    console only asks (``CatalogSync.request``) and this loop reads, so the policy never runs twice at once and an HTTP
    request never waits for Bazarr (R10-7)."""
    while True:
        try:
            if sync.take_request() or sync.due():
                sync_and_request(sync, auto)
        except Exception:  # noqa: BLE001
            logging.exception("Catalog sync cycle failed")
        sync.idle(check_seconds)


def backup_maintenance_once(
    state_dir: str, config_path: str, postprocess_targets_path: str, settings: Any = None
) -> dict[str, Any] | None:
    return create_scheduled_backup_if_due(state_dir, config_path, postprocess_targets_path, settings=settings)


def run_backup_scheduler(
    state_dir: str, config_path: str, postprocess_targets_path: str, sleep_seconds: int, settings: Any = None,
    cleanup: Cleanup | None = None,
) -> None:
    """The daily maintenance: scheduled backup, then file cleanup (FR-13.4), each failing alone."""
    while True:
        try:
            backup = backup_maintenance_once(state_dir, config_path, postprocess_targets_path, settings)
            if backup:
                logging.info("Scheduled backup created: %s", backup["path"])
        except Exception:
            logging.exception("Scheduled backup maintenance failed")
        if cleanup is not None:
            try:
                cleanup.run_if_due()
            except Exception:
                logging.exception("Scheduled file cleanup failed")
        time.sleep(sleep_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one available job and exit")
    parser.add_argument("--worker-only", action="store_true", help="run queue worker without the web console")
    parser.add_argument("--no-worker", action="store_true", help="run web console without the background worker")
    parser.add_argument(
        "--no-queue", action="store_true",
        help="keep the catalog sync but never run queued jobs (a preview that must not call a model)",
    )
    parser.add_argument("--host", default=os.getenv("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "6789")))
    parser.add_argument("--sleep", type=int, default=int(os.getenv("WORKER_SLEEP_SECONDS", "15")))
    args = parser.parse_args()

    state_dir = os.getenv("STATE_DIR", "/state")
    queue_dir = os.getenv("QUEUE_DIR", "/queue")
    log_dir = os.getenv("LOG_DIR", f"{state_dir}/logs")
    config_path = os.getenv("APP_CONFIG_PATH", f"{state_dir}/config.json")
    # Bazarr's post-processing script is gone (P10); the language list it read is still written, and kept in backups,
    # until that leftover is cleaned out of settings and backups.
    postprocess_targets_path = os.getenv("POSTPROCESS_TARGETS_PATH", f"{state_dir}/postprocess/targets.json")
    static_dir = os.getenv("STATIC_DIR", "/app/static")
    backup_check_seconds = int(os.getenv("BACKUP_CHECK_SECONDS", "21600"))
    configure_logging(log_dir)
    purge_old_backups(state_dir)

    cache = JsonFileCache(os.getenv("TMDB_CACHE_PATH", f"{state_dir}/cache/tmdb_cache.json"))
    http = HTTPClient(timeout=int(os.getenv("HTTP_TIMEOUT_SECONDS", "20")))
    data_dir = os.getenv("DATA_DIR", state_dir)
    store = Store(os.getenv("DB_PATH", f"{data_dir}/app.db"))
    # Settings and API keys live in the database (FR-20). The first run takes config.json and the environment in.
    settings_service = SettingsService(
        store, config_path=config_path, postprocess_targets_path=postprocess_targets_path
    )
    settings_service.start()
    settings = settings_service.settings()
    provider_factory = LiveProviderFactory(settings_service)  # keys are read at each call: a new key is live at once

    def live_settings() -> dict[str, Any]:
        try:
            return worker.settings  # the worker adopts new settings when they are saved
        except NameError:  # asked while the worker is still being built
            return settings

    # Events, quality signals and model-call bodies are written off the job's thread (NFR-11, FR-19).
    telemetry = TelemetrySink(
        store, usage=store, bodies=BodyLog(f"{data_dir}/logs/llm"), settings_source=live_settings,
    )
    telemetry.apply_settings(settings)  # log level now, and the secrets the log must hide
    settings_service.telemetry = telemetry
    DEFAULT_REDACTOR.add_values([os.getenv("CONSOLE_PASSWORD", "")])
    versions = TranslationVersions(store, f"{data_dir}/backups/subtitles", telemetry=telemetry)
    # Start-up, explicitly (FR-16.5): publishes a crash left announced are confirmed or cancelled before any job runs.
    settled = versions.reconcile()
    if any(settled.values()):
        logging.warning("Start-up: version history settled %s", settled)
    glossaries = Glossaries(store)
    styles = Styles(store)
    # Start-up, explicitly (P12): the YAML glossaries of an older worker enter the database once, and the built-in
    # style presets are stored where the admin can edit them. Neither touches a file.
    imported = glossaries.import_files(data_dir)
    if imported.imported or imported.broken:
        logging.info("Start-up: %d glossary file(s) imported, %d not readable", imported.imported, len(imported.broken))
    styles.seed_presets()

    media = BazarrMedia(http, live_settings)
    metadata = TmdbMetadata(http, cache, live_settings)
    memory = StoryMemory(store, store, settings_source=live_settings, media=media)
    library = Library(store, glossaries, versions, memory=memory, styles=styles)
    suggestion_service = SuggestionService(store, library, settings_source=live_settings, telemetry=telemetry)
    # What the machine proposes from the facts of the episodes it reads (P14): stored only through the one service.
    proposer = FactsProposer(
        memory, suggestion_service, settings_source=live_settings, telemetry=telemetry,
    )
    suggestion_service.generator = proposer
    # What the web says about the names and terms of a series (P16): a job of its own, queued by the same hook (FR-7.5).
    researcher = Researcher(proposer, settings_source=live_settings, telemetry=telemetry, resolver=RedirectResolver())
    proposer.researcher = researcher
    suggestion_service.researcher = researcher
    attempt = TranslationAttempt(
        versions=versions, usage=store, provider_factory=provider_factory, library=library, memory=memory,
        telemetry=telemetry, proposals=proposer,
    )
    worker = QueueWorker(
        queue_dir=queue_dir, settings=settings, cache=cache, http=http, attempt=attempt,
        media=media, metadata=metadata, telemetry=telemetry, store=store,
        key_ok=lambda: any(key.status == "ok" for key in store.api_keys(provider_name(live_settings()))),
        library=library,
    )
    # Jobs that read and do not translate go through the same queue, quota and breaker (F7).
    worker.register_handler(EXTRACT, ExtractJob(
        memory, provider_factory, settings_source=live_settings, usage=store, telemetry=telemetry,
    ))
    worker.register_handler(SUGGEST, SuggestJob(
        proposer, provider_factory, settings_source=live_settings, usage=store, telemetry=telemetry,
    ))
    worker.register_handler(RESEARCH, ResearchJob(
        researcher, provider_factory, settings_source=live_settings, usage=store, telemetry=telemetry,
    ))
    # Proposals are asked for through the queue: by the admin, and by a translation that read new facts.
    proposer.jobs = worker.queue
    # A saved setting, a pasted key or a key the provider refused reaches the running worker at once (FR-20.1).
    settings_service.subscribe(lambda: worker.apply_settings(settings_service.settings()))
    bazarr = BazarrIntegration(http, settings["bazarr_url"], settings["bazarr_api_key"])
    units = UnitService(
        library, glossaries, versions, worker.queue, refresh_bazarr=media.refresh,
        memory=memory, settings_source=live_settings, usage=store,
        telemetry=telemetry, source_languages=lambda: enabled_source_languages(settings_service.app_config()),
    )
    # What the admin keeps correcting becomes suggestions, and what they corrected is the golden set (P15, FR-19.6).
    feedback = FeedbackProposer(suggestion_service, settings_source=live_settings, telemetry=telemetry)
    suggestion_service.feedback = feedback
    units.feedback = feedback
    # The judge between a variant and the current translation (P15): advice, off until the admin turns it on. Its job
    # goes through the one queue like every job that spends model calls.
    judge = JudgeService(units, worker.queue, store, settings_source=live_settings, telemetry=telemetry)
    units.judge = judge
    worker.register_handler(JUDGE, JudgeJob(
        judge, provider_factory, settings_source=live_settings, usage=store, telemetry=telemetry,
    ))
    insights = Insights(
        Metrics(store, console=worker.console, settings_source=live_settings, limits=lambda: rate_limits(live_settings())),
        feedback,
    )
    style_service = StyleService(
        library, styles, metadata=metadata, settings_source=live_settings, provider_factory=provider_factory,
        telemetry=telemetry, usage=store,
    )
    # The library as Bazarr lists it, what the admin asks for from it, and the policy that asks by itself (P10, ADR-10).
    job_requests = JobRequests(
        store, library, worker.queue, versions, config_source=settings_service.app_config,
        settings_source=live_settings, telemetry=telemetry, memory=memory,
    )
    catalog_sync = CatalogSync(store, media, live_settings, telemetry)
    auto_request = AutoRequest(store, job_requests, live_settings, settings_service.app_config, telemetry)
    catalog_view = CatalogView(
        store, library, worker.queue, versions, config_source=settings_service.app_config, settings_source=live_settings,
    )
    backups = BackupMaintenance(state_dir, config_path, postprocess_targets_path, settings=settings_service)
    cleanup = Cleanup(
        worker.queue.jobs, versions, glossaries, store, settings_source=live_settings,
        temp_roots=lambda: [data_dir, queue_dir],
        media_roots=lambda: settings_service.app_config().get("media_roots") or [],
        telemetry=telemetry,
    )
    telemetry.event(
        "worker.started", reason="the worker process started", model=settings.get("gst_model"),
        fallback_model=settings.get("gst_fallback_model") or None, auto_request=settings.get("auto_request"),
        log_level=settings.get("log_level"), log_prompt_bodies=settings.get("log_prompt_bodies"),
    )

    try:
        if args.once:
            worker.process_once()
            return 0

        # The loops of the process. With the console they start and stop in the web app's lifespan; without it they start here.
        loops = [BackgroundLoop(
            "backup-scheduler", run_backup_scheduler,
            (state_dir, config_path, postprocess_targets_path, backup_check_seconds, settings_service, cleanup),
        )]
        if not args.no_worker:
            loops.append(BackgroundLoop("catalog-sync", run_catalog_sync, (catalog_sync, auto_request)))

        if args.worker_only:
            start_loops(loops)
            worker.run_forever(args.sleep)
            return 0

        ctx = RuntimeContext(
            queue_dir=queue_dir,
            console=worker.console,
            settings=settings,
            worker=worker,
            http=http,
            state_dir=state_dir,
            config_path=config_path,
            postprocess_targets_path=postprocess_targets_path,
            static_dir=static_dir,
            log_dir=log_dir,
            bazarr=bazarr,
            backups=backups,
            transport=HTTPTransport(http),
            units=units,
            telemetry=telemetry,
            settings_service=settings_service,
            cleanup=cleanup,
            catalog=CatalogActions(store, library, catalog_sync, auto_request, job_requests, catalog_view),
            style=style_service,
            suggestions=suggestion_service,
            insights=insights,
        ).as_handler_context()
        if not args.no_worker and not args.no_queue:
            loops.append(BackgroundLoop("queue-worker", worker.run_forever, (args.sleep,)))
        start_web_server(ctx, args.host, args.port, ConsoleAuth.from_env(os.environ, store), loops=loops)
        return 0
    finally:
        telemetry.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
