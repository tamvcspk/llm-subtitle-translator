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
from gst_worker.config import normalize_app_config, save_app_config
from gst_worker.logs import configure_logging
from gst_worker.http import HTTPClient, HTTPTransport, JsonFileCache, MemoryCache
from gst_worker.config import ADMISSION_AUTO, ADMISSION_HOLD
from gst_worker.queue import FileJobQueue, JobQueue, skip_reason, stamp_open_jobs, sync_daily_quota_pause
from gst_worker.console import ConsoleActions
from gst_worker.glossaries import GlossaryFiles
from gst_worker.library import Library, enrich_job_identity
from gst_worker.memory import StoryMemory
from gst_worker.queue_policy import configured_models
from gst_worker.store import Store
from gst_worker.llm import build_provider
from gst_worker.translation_attempt import TranslationAttempt
from gst_worker.units import UnitService
from gst_worker.versions import TranslationVersions
from gst_worker.runtime import RuntimeContext
from gst_worker.ports import MediaLibrary, Metadata
from gst_worker.tmdb import TmdbMetadata
# The settings functions and the HTTP adapter live in their own modules; they stay importable from here.
from gst_worker.settings import load_settings, public_app_config, seed_app_config_from_settings, settings_from_payload  # noqa: F401
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
) -> str:
    # The ports default to the adapters over the settings this job runs with (composition done here, not in core).
    media = media or BazarrMedia(http, lambda: settings)
    metadata = metadata or TmdbMetadata(http, cache, lambda: settings)
    reason = skip_reason(job)
    if reason:
        logging.info("Skipping job for %s: %s", job.get("subtitle_path"), reason)
        return "skipped"
    # Bazarr decides what is wanted: when it says the language is not missing, spend no model call.
    if job.get("trigger") != "retranslate" and media.target_still_missing(job) is False:
        logging.info("Skipping job for %s: Bazarr does not list %s as missing",
                     job.get("subtitle_path"), job.get("target_code"))
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
    ) -> None:
        self.queue = JobQueue(queue_dir)
        self.settings = settings
        self.cache = cache
        self.http = http
        self.attempt = attempt
        self.media = media
        self.metadata = metadata
        self.console = ConsoleActions(FileJobQueue(queue_dir))
        self.queue.recover_interrupted_jobs()

    def models(self) -> tuple[str, ...]:
        return configured_models(
            str(self.settings.get("gst_model") or ""), str(self.settings.get("gst_fallback_model") or "")
        )

    def apply_settings(self, settings: dict[str, Any]) -> None:
        """Adopt new settings; a newly configured model with quota left lifts the daily quota pause."""
        was = self.admission_mode()
        if was != ADMISSION_HOLD and str(settings.get("admission_mode") or "") == ADMISSION_HOLD:
            # Switching to `hold` leaves the line as it is (FR-12.8): what the script queued before now counts as admitted.
            stamp_open_jobs(str(self.queue.queue_dir))
        self.settings = settings
        sync_daily_quota_pause(str(self.queue.queue_dir), self.models())

    def admission_mode(self) -> str:
        return ADMISSION_HOLD if str(self.settings.get("admission_mode") or "") == ADMISSION_HOLD else ADMISSION_AUTO

    def process_once(self, now: float | None = None) -> bool:
        self.queue.admit_new_jobs(self.admission_mode())
        return self.queue.process_once(
            lambda job, update_status: process_job(
                job, self.settings, self.cache, self.http, update_status, self.attempt,
                media=self.media, metadata=self.metadata,
            ),
            settle_seconds=max(0, int(self.settings.get("job_settle_seconds") or 0)),
            now=now,
            models=self.models(),
        )

    def run_forever(self, sleep_seconds: int) -> None:
        while True:
            if not self.process_once():
                time.sleep(sleep_seconds)


def backup_maintenance_once(state_dir: str, config_path: str, postprocess_targets_path: str) -> dict[str, Any] | None:
    return create_scheduled_backup_if_due(state_dir, config_path, postprocess_targets_path)


def run_backup_scheduler(state_dir: str, config_path: str, postprocess_targets_path: str, sleep_seconds: int) -> None:
    while True:
        try:
            backup = backup_maintenance_once(state_dir, config_path, postprocess_targets_path)
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
    if not Path(config_path).exists():
        save_app_config(config_path, normalize_app_config({}), postprocess_targets_path)
    settings = load_settings(config_path)
    if seed_app_config_from_settings(config_path, settings, postprocess_targets_path):
        settings = load_settings(config_path)
    purge_old_backups(state_dir)

    cache = JsonFileCache(os.getenv("TMDB_CACHE_PATH", f"{state_dir}/cache/tmdb_cache.json"))
    http = HTTPClient(timeout=int(os.getenv("HTTP_TIMEOUT_SECONDS", "20")))
    data_dir = os.getenv("DATA_DIR", state_dir)
    store = Store(os.getenv("DB_PATH", f"{data_dir}/app.db"))
    versions = TranslationVersions(store, f"{data_dir}/backups/subtitles")
    glossaries = GlossaryFiles(data_dir)
    def live_settings() -> dict[str, Any]:
        return worker.settings  # the worker adopts new settings when they are saved

    media = BazarrMedia(http, live_settings)
    metadata = TmdbMetadata(http, cache, live_settings)
    memory = StoryMemory(store, store, settings_source=live_settings, media=media)
    library = Library(store, glossaries, versions, memory=memory)
    attempt = TranslationAttempt(
        versions=versions, usage=store, provider_factory=build_provider, library=library, memory=memory
    )
    worker = QueueWorker(
        queue_dir=queue_dir, settings=settings, cache=cache, http=http, attempt=attempt,
        media=media, metadata=metadata,
    )
    bazarr = BazarrIntegration(http, settings["bazarr_url"], settings["bazarr_api_key"])
    units = UnitService(
        library, glossaries, versions, worker.console.jobs, refresh_bazarr=media.refresh,
        memory=memory, settings_source=live_settings, usage=store, provider_factory=build_provider,
    )
    backups = BackupMaintenance(state_dir, config_path, postprocess_targets_path)

    try:
        if args.once:
            worker.process_once()
            return 0

        threading.Thread(
            target=run_backup_scheduler,
            args=(state_dir, config_path, postprocess_targets_path, backup_check_seconds),
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
        ).as_handler_context()
        if not args.no_worker:
            threading.Thread(target=worker.run_forever, args=(args.sleep,), daemon=True).start()
        start_web_server(ctx, args.host, args.port, ConsoleAuth.from_env(os.environ, store))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
