#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from gst_worker.backups import BackupMaintenance, create_scheduled_backup_if_due, purge_old_backups
from gst_worker.auth import ConsoleAuth
from gst_worker.bazarr import BazarrIntegration, BazarrMedia
from gst_worker.logs import configure_logging
from gst_worker.http import HTTPClient, HTTPTransport, JsonFileCache, MemoryCache
from gst_worker.config import ADMISSION_AUTO, ADMISSION_HOLD, provider_name
from gst_worker.intake import import_queue_tree, receive_script_jobs
from gst_worker.queue import JobQueue, skip_reason
from gst_worker.console import ConsoleActions
from gst_worker.glossaries import GlossaryFiles
from gst_worker.library import Library, enrich_job_identity
from gst_worker.memory import StoryMemory
from gst_worker.queue_policy import configured_models
from gst_worker.store import Store
from gst_worker.llm import LiveProviderFactory, build_provider
from gst_worker.translation_attempt import TranslationAttempt
from gst_worker.units import UnitService
from gst_worker.versions import TranslationVersions
from gst_worker.runtime import RuntimeContext
from gst_worker.ports import MediaLibrary, Metadata, Telemetry
from gst_worker.bodies import BodyLog
from gst_worker.redaction import DEFAULT as DEFAULT_REDACTOR, SECRET_SETTING_KEYS
from gst_worker.telemetry import NULL as NULL_TELEMETRY, Telemetry as TelemetrySink
from gst_worker.tmdb import TmdbMetadata
# The settings service and the HTTP adapter live in their own modules; they stay importable from here.
from gst_worker.settings import SettingsService
from gst_worker.web import ConsoleHandler, start_web_server  # noqa: F401

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
    if job.get("trigger") != "retranslate" and media.target_still_missing(job) is False:
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
    description = metadata.description(job)
    if status_callback:
        status_callback("Sending subtitle batches to Gemini")
    # The queue picks primary or fallback per attempt; the configured model is the default.
    if job.get("gst_model"):
        settings = {**settings, "gst_model": str(job["gst_model"])}
    def report_progress(done: int, total: int) -> None:
        if status_callback:
            status_callback(f"Translating {done}/{total} cues", progress_done=done, progress_total=total)

    status = (attempt or DEFAULT_ATTEMPT).run(job, description, settings, on_progress=report_progress)
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
        self.queue.configure(settings, self.models(), provider_name(settings))
        # Start-up, explicitly: the old queue folders move into the table once, then jobs a stopped worker held go back in line.
        import_queue_tree(self.queue, queue_dir)
        self.queue.recover()

    def models(self) -> tuple[str, ...]:
        return configured_models(
            str(self.settings.get("gst_model") or ""), str(self.settings.get("gst_fallback_model") or "")
        )

    def apply_settings(self, settings: dict[str, Any]) -> None:
        """Adopt new settings; a newly configured model with quota left is usable at once."""
        was = self.admission_mode()
        if was != ADMISSION_HOLD and str(settings.get("admission_mode") or "") == ADMISSION_HOLD:
            # Switching to `hold` leaves the line as it is (FR-12.8): what the script dropped and what was queued
            # before now counts as admitted.
            receive_script_jobs(self.queue, self.queue_dir, ADMISSION_AUTO)
            self.queue.jobs.stamp_open_jobs(ADMISSION_AUTO)
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

    def admission_mode(self) -> str:
        return ADMISSION_HOLD if str(self.settings.get("admission_mode") or "") == ADMISSION_HOLD else ADMISSION_AUTO

    def process_once(self, now: float | None = None) -> bool:
        receive_script_jobs(self.queue, self.queue_dir, self.admission_mode())
        return self.queue.process_once(
            lambda job, update_status: process_job(
                job, self.settings, self.cache, self.http, update_status, self.attempt,
                media=self.media, metadata=self.metadata, telemetry=self.telemetry,
            ),
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


def backup_maintenance_once(
    state_dir: str, config_path: str, postprocess_targets_path: str, settings: Any = None
) -> dict[str, Any] | None:
    return create_scheduled_backup_if_due(state_dir, config_path, postprocess_targets_path, settings=settings)


def run_backup_scheduler(
    state_dir: str, config_path: str, postprocess_targets_path: str, sleep_seconds: int, settings: Any = None
) -> None:
    while True:
        try:
            backup = backup_maintenance_once(state_dir, config_path, postprocess_targets_path, settings)
            if backup:
                logging.info("Scheduled backup created: %s", backup["path"])
        except Exception:
            logging.exception("Scheduled backup maintenance failed")
        time.sleep(sleep_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one available job and exit")
    parser.add_argument("--worker-only", action="store_true", help="run queue worker without the web console")
    parser.add_argument("--no-worker", action="store_true", help="run web console without the background worker")
    parser.add_argument("--host", default=os.getenv("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "6789")))
    parser.add_argument("--sleep", type=int, default=int(os.getenv("WORKER_SLEEP_SECONDS", "15")))
    args = parser.parse_args()

    state_dir = os.getenv("STATE_DIR", "/state")
    queue_dir = os.getenv("QUEUE_DIR", "/queue")
    log_dir = os.getenv("LOG_DIR", f"{state_dir}/logs")
    config_path = os.getenv("APP_CONFIG_PATH", f"{state_dir}/config.json")
    postprocess_targets_path = os.getenv("POSTPROCESS_TARGETS_PATH", "/bazarr-postprocess/targets.json")
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
    glossaries = GlossaryFiles(data_dir)

    media = BazarrMedia(http, live_settings)
    metadata = TmdbMetadata(http, cache, live_settings)
    memory = StoryMemory(store, store, settings_source=live_settings, media=media)
    library = Library(store, glossaries, versions, memory=memory)
    attempt = TranslationAttempt(
        versions=versions, usage=store, provider_factory=provider_factory, library=library, memory=memory,
        telemetry=telemetry,
    )
    worker = QueueWorker(
        queue_dir=queue_dir, settings=settings, cache=cache, http=http, attempt=attempt,
        media=media, metadata=metadata, telemetry=telemetry, store=store,
        key_ok=lambda: any(key.status == "ok" for key in store.api_keys(provider_name(live_settings()))),
    )
    # A saved setting, a pasted key or a key the provider refused reaches the running worker at once (FR-20.1).
    settings_service.subscribe(lambda: worker.apply_settings(settings_service.settings()))
    bazarr = BazarrIntegration(http, settings["bazarr_url"], settings["bazarr_api_key"])
    units = UnitService(
        library, glossaries, versions, worker.queue, refresh_bazarr=media.refresh,
        memory=memory, settings_source=live_settings, usage=store, provider_factory=provider_factory,
        telemetry=telemetry,
    )
    backups = BackupMaintenance(state_dir, config_path, postprocess_targets_path, settings=settings_service)
    telemetry.event(
        "worker.started", reason="the worker process started", model=settings.get("gst_model"),
        fallback_model=settings.get("gst_fallback_model") or None, admission_mode=settings.get("admission_mode"),
        log_level=settings.get("log_level"), log_prompt_bodies=settings.get("log_prompt_bodies"),
    )

    try:
        if args.once:
            worker.process_once()
            return 0

        threading.Thread(
            target=run_backup_scheduler,
            args=(state_dir, config_path, postprocess_targets_path, backup_check_seconds, settings_service),
            daemon=True,
        ).start()

        if args.worker_only:
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
        ).as_handler_context()
        if not args.no_worker:
            threading.Thread(target=worker.run_forever, args=(args.sleep,), daemon=True).start()
        start_web_server(ctx, args.host, args.port, ConsoleAuth.from_env(os.environ, store))
        return 0
    finally:
        telemetry.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
