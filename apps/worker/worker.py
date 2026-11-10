#!/usr/bin/env python3
from __future__ import annotations

import argparse
import mimetypes
import json
import logging
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from gst_worker.backups import (
    BackupMaintenance,
    backup_file_path,
    create_backup,
    create_scheduled_backup_if_due,
    list_backups,
    purge_old_backups,
    restore_backup_archive,
)
from gst_worker.bazarr import BazarrIntegration, list_wanted_items, read_bazarr_api_key, refresh_bazarr
from gst_worker.config import (
    SECRET_MASK,
    SECRET_CONFIG_KEYS,
    GST_BOOL_CONFIG_KEYS,
    GST_STRING_CONFIG_KEYS,
    load_app_config,
    normalize_app_config,
    save_app_config,
    supported_languages,
)
from gst_worker.connection_tests import test_connection
from gst_worker.gemini import gemini_models
from gst_worker.logs import clear_logs, configure_logging, read_log_snapshot
from gst_worker.http import HTTPClient, HTTPTransport, JsonFileCache, MemoryCache
from gst_worker.queue import (
    JobQueue,
    queue_snapshot,
    should_skip_job,
    sync_daily_quota_pause,
)
from gst_worker.console import ConsoleActions
from gst_worker.queue_policy import configured_models
from gst_worker.translation_attempt import DEFAULT_ATTEMPT
from gst_worker.subtitles import scan_source_subtitles
from gst_worker.runtime import RuntimeContext
from gst_worker.tmdb import build_tmdb_description


def load_settings(config_path: str | None = None) -> dict[str, Any]:
    app_config = load_app_config(config_path) if config_path else normalize_app_config({})
    bazarr_config_path = os.getenv("BAZARR_CONFIG_PATH", "/bazarr-config/config.yaml")
    bazarr_url = (
        str(app_config.get("bazarr_url") or "").strip()
        or os.getenv("BAZARR_URL", "http://bazarr:6767")
    ).rstrip("/")
    settings: dict[str, Any] = {
        "bazarr_url": bazarr_url,
        "bazarr_api_key": (
            str(app_config.get("bazarr_api_key") or "").strip()
            or os.getenv("BAZARR_API_KEY")
            or read_bazarr_api_key(bazarr_config_path)
        ),
        "gemini_api_key": (
            str(app_config.get("gemini_api_key") or "").strip()
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GEMINI_API_KEY1")
            or ""
        ),
        "gemini_api_key2": str(app_config.get("gemini_api_key2") or "").strip() or os.getenv("GEMINI_API_KEY2", ""),
        "tmdb_api_key": str(app_config.get("tmdb_api_key") or "").strip() or os.getenv("TMDB_API_KEY", ""),
    }
    for key in GST_STRING_CONFIG_KEYS:
        settings[key] = str(app_config.get(key) or "").strip()
    for key in GST_BOOL_CONFIG_KEYS:
        settings[key] = bool(app_config.get(key))
    settings["gst_batch_size"] = int(app_config.get("gst_batch_size") or 500)
    settings["gst_retry_batch_size"] = int(app_config.get("gst_retry_batch_size", 300))
    settings["gst_model"] = str(app_config.get("gst_model") or os.getenv("GST_MODEL", "gemini-flash-latest"))
    settings["job_settle_seconds"] = int(app_config.get("job_settle_seconds") or 0)
    if os.getenv("GST_BATCH_SIZE") and not app_config.get("gst_batch_size"):
        settings["gst_batch_size"] = int(os.getenv("GST_BATCH_SIZE", "500"))
    return settings


def public_app_config(config_path: str, settings: dict[str, str] | None = None) -> dict[str, Any]:
    runtime_settings = settings or load_settings(config_path)
    config = load_app_config(config_path)
    config["bazarr_url"] = runtime_settings["bazarr_url"]
    for key in SECRET_CONFIG_KEYS:
        config[key] = SECRET_MASK if runtime_settings.get(key, "") else ""
        config[f"{key}_configured"] = bool(runtime_settings.get(key, ""))
    return config


def settings_from_payload(config_path: str, body: dict[str, Any]) -> dict[str, str]:
    settings = load_settings(config_path)
    bazarr_url = str(body.get("bazarr_url") or "").strip().rstrip("/")
    if bazarr_url:
        settings["bazarr_url"] = bazarr_url
    for key in SECRET_CONFIG_KEYS:
        value = str(body.get(key) or "").strip()
        if value and value != SECRET_MASK:
            settings[key] = value
    return settings


def seed_app_config_from_settings(
    config_path: str,
    settings: dict[str, str],
    postprocess_targets_path: str | None = None,
) -> bool:
    config = load_app_config(config_path)
    changed = False
    if not config.get("bazarr_url") and settings.get("bazarr_url"):
        config["bazarr_url"] = settings["bazarr_url"]
        changed = True
    for key in SECRET_CONFIG_KEYS:
        if not config.get(key) and settings.get(key):
            config[key] = settings[key]
            changed = True
    if changed:
        save_app_config(config_path, config, postprocess_targets_path)
    return changed


def process_job(
    job: dict[str, Any],
    settings: dict[str, Any],
    cache: MemoryCache,
    http: HTTPClient,
    status_callback: Callable[[str], None] | None = None,
) -> str:
    if should_skip_job(job):
        logging.info("Skipping job for %s", job.get("subtitle_path"))
        return "skipped"

    if status_callback:
        status_callback("Resolving movie details")
    description = build_tmdb_description(
        job,
        bazarr=http,
        tmdb=http,
        bazarr_url=settings["bazarr_url"],
        bazarr_api_key=settings["bazarr_api_key"],
        tmdb_api_key=settings["tmdb_api_key"],
        cache=cache,
    )
    if status_callback:
        status_callback("Sending subtitle batches to Gemini")
    # The queue picks primary or fallback per attempt; the configured model is the default.
    if job.get("gst_model"):
        settings = {**settings, "gst_model": str(job["gst_model"])}
    status = DEFAULT_ATTEMPT.run(job, description, settings)
    if status_callback:
        status_callback("Refreshing Bazarr")
    refresh_bazarr(job, http=http, bazarr_url=settings["bazarr_url"], api_key=settings["bazarr_api_key"])
    return status


class QueueWorker:
    """Bind the queue's execution callback to the configured translation workflow."""

    def __init__(self, queue_dir: str, settings: dict[str, Any], cache: MemoryCache, http: HTTPClient) -> None:
        self.queue = JobQueue(queue_dir)
        self.settings = settings
        self.cache = cache
        self.http = http
        self.console = ConsoleActions(queue_dir)
        self.queue.recover_interrupted_jobs()

    def models(self) -> tuple[str, ...]:
        return configured_models(
            str(self.settings.get("gst_model") or ""), str(self.settings.get("gst_fallback_model") or "")
        )

    def apply_settings(self, settings: dict[str, Any]) -> None:
        """Adopt new settings; a newly configured model with quota left lifts the daily quota pause."""
        self.settings = settings
        sync_daily_quota_pause(str(self.queue.queue_dir), self.models())

    def process_once(self, now: float | None = None) -> bool:
        return self.queue.process_once(
            lambda job, update_status: process_job(job, self.settings, self.cache, self.http, update_status),
            settle_seconds=max(0, int(self.settings.get("job_settle_seconds") or 0)),
            now=now,
            models=self.models(),
        )

    def run_forever(self, sleep_seconds: int) -> None:
        while True:
            if not self.process_once():
                time.sleep(sleep_seconds)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def read_binary_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        raise ValueError("request body is empty")
    return handler.rfile.read(length)


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


STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "GeminiSRTConsole/1.0"

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        try:
            status_code = int(code)
        except (TypeError, ValueError):
            super().log_request(code, size)
            return
        if status_code >= 400:
            super().log_request(code, size)

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("web %s - %s", self.address_string(), fmt % args)

    @property
    def ctx(self) -> dict[str, Any]:
        return self.server.ctx  # type: ignore[attr-defined]

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"error": message}, status=status)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/status":
                self.send_json(self.api_status())
            elif parsed.path == "/api/queue":
                self.send_json(queue_snapshot(self.ctx["queue_dir"]))
            elif parsed.path == "/api/settings":
                self.send_json(public_app_config(self.ctx["config_path"], self.ctx["settings"]))
            elif parsed.path == "/api/languages":
                settings = load_settings(self.ctx["config_path"])
                self.ctx["settings"] = settings
                self.send_json({"items": supported_languages(self.ctx["http"], settings["bazarr_url"], settings["bazarr_api_key"])})
            elif parsed.path == "/api/scan":
                config = load_app_config(self.ctx["config_path"])
                params = urllib.parse.parse_qs(parsed.query)
                limit = int(params.get("limit", [config["scan_limit"]])[0])
                self.send_json(
                    {
                        "items": scan_source_subtitles(
                            config["media_roots"],
                            config["source_languages"],
                            config["target_languages"],
                            limit=limit,
                        )
                    }
                )
            elif parsed.path == "/api/wanted":
                config = load_app_config(self.ctx["config_path"])
                settings = load_settings(self.ctx["config_path"])
                self.ctx["settings"] = settings
                self.send_json(
                    {
                        "items": list_wanted_items(
                            self.ctx["http"],
                            settings["bazarr_url"],
                            settings["bazarr_api_key"],
                            config["source_languages"],
                            config["target_languages"],
                            limit=int(config["scan_limit"]),
                        )
                    }
                )
            elif parsed.path == "/api/logs":
                self.send_json(read_log_snapshot(self.ctx["log_dir"]))
            elif parsed.path == "/api/gemini-models":
                settings = load_settings(self.ctx["config_path"])
                self.ctx["settings"] = settings
                self.send_json({"items": gemini_models(self.ctx["http"], settings)})
            elif parsed.path == "/api/backups":
                self.send_json({"items": list_backups(self.ctx["state_dir"])})
            elif parsed.path == "/api/backups/download":
                params = urllib.parse.parse_qs(parsed.query)
                backup = backup_file_path(self.ctx["state_dir"], params.get("name", [""])[0])
                self.send_file(backup, content_type="application/zip", attachment=True)
            else:
                self.serve_static(parsed.path)
        except ValueError as exc:
            self.send_error_json(str(exc), status=400)
        except Exception as exc:
            logging.exception("GET %s failed", self.path)
            self.send_error_json(str(exc), status=500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/backups/import":
                result = restore_backup_archive(
                    read_binary_body(self),
                    self.ctx["state_dir"],
                    self.ctx["config_path"],
                    self.ctx["postprocess_targets_path"],
                )
                settings = load_settings(self.ctx["config_path"])
                self.ctx["settings"] = settings
                if self.ctx.get("worker") is not None:
                    self.ctx["worker"].apply_settings(settings)
                self.send_json({"ok": True, **result})
                return

            body = read_json_body(self)
            if parsed.path == "/api/settings":
                saved = save_app_config(self.ctx["config_path"], body, self.ctx["postprocess_targets_path"])
                settings = load_settings(self.ctx["config_path"])
                self.ctx["settings"] = settings
                if self.ctx.get("worker") is not None:
                    self.ctx["worker"].apply_settings(settings)
                self.send_json(public_app_config(self.ctx["config_path"], settings))
            elif parsed.path == "/api/enqueue":
                config = load_app_config(self.ctx["config_path"])
                targets = config["target_languages"]
                target_codes = body.get("target_codes")
                if isinstance(target_codes, list) and target_codes:
                    wanted = {str(code) for code in target_codes}
                    targets = [target for target in targets if str(target["code"]) in wanted]
                created = self.ctx["console"].enqueue(
                    {
                        "video_path": body.get("video_path", ""),
                        "subtitle_path": body.get("subtitle_path", ""),
                        "provider": body.get("provider", "gemini-console"),
                        "source_code": body.get("source_code") or body.get("language") or "en",
                        "source_language": body.get("source_language") or "English",
                        "series_id": body.get("series_id", ""),
                        "media_id": body.get("media_id", ""),
                        "media_type": body.get("media_type") or body.get("type") or "movie",
                    },
                    targets,
                )
                self.send_json({"created": created, "count": len(created)})
            elif parsed.path == "/api/enqueue-scan":
                config = load_app_config(self.ctx["config_path"])
                items = scan_source_subtitles(
                    config["media_roots"],
                    config["source_languages"],
                    config["target_languages"],
                    limit=int(config["scan_limit"]),
                )
                created: list[str] = []
                for item in items:
                    created.extend(
                        self.ctx["console"].enqueue(
                            {
                                "video_path": item["video_path"],
                                "subtitle_path": item["subtitle_path"],
                                "provider": "gemini-console",
                                "source_code": item["source_code"],
                                "source_language": item["source_language"],
                                "media_type": "movie",
                            },
                            item["missing_targets"],
                        )
                    )
                self.send_json({"created": created, "count": len(created)})
            elif parsed.path == "/api/queue/retry":
                self.send_json({"ok": self.ctx["console"].retry(str(body.get("job_id", "")))})
            elif parsed.path == "/api/queue/cancel":
                self.send_json({"ok": self.ctx["console"].cancel(str(body.get("job_id", "")))})
            elif parsed.path == "/api/queue/delete":
                self.send_json(
                    {
                        "ok": self.ctx["console"].delete(str(body.get("state", "")), str(body.get("job_id", "")))
                    }
                )
            elif parsed.path == "/api/backups":
                self.send_json(
                    {
                        "backup": create_backup(
                            self.ctx["state_dir"],
                            self.ctx["config_path"],
                            self.ctx["postprocess_targets_path"],
                            reason="manual",
                        )
                    }
                )
            elif parsed.path == "/api/test-connection":
                settings = settings_from_payload(self.ctx["config_path"], body)
                self.send_json(test_connection(str(body.get("kind", "")), settings, self.ctx["http"]))
            elif parsed.path == "/api/logs/clear":
                self.send_json({"ok": clear_logs(self.ctx["log_dir"])})
            else:
                self.send_error_json("not found", status=404)
        except ValueError as exc:
            self.send_error_json(str(exc), status=400)
        except Exception as exc:
            logging.exception("POST %s failed", self.path)
            self.send_error_json(str(exc), status=500)

    def api_status(self) -> dict[str, Any]:
        settings = load_settings(self.ctx["config_path"])
        self.ctx["settings"] = settings
        snapshot = queue_snapshot(self.ctx["queue_dir"])
        status = {
            "queue": snapshot["counts"],
            "daily_quota_pause": snapshot["daily_quota_pause"],
            "settings": public_app_config(self.ctx["config_path"], settings),
            "bazarr_url": settings["bazarr_url"],
        }
        try:
            status["bazarr"] = self.ctx["http"].get_json(
                f"{settings['bazarr_url']}/api/system/ping",
                headers={"X-API-KEY": settings["bazarr_api_key"]},
            )
        except Exception as exc:
            status["bazarr"] = {"error": str(exc)}
        return status

    def serve_static(self, request_path: str) -> None:
        static_dir = Path(self.ctx["static_dir"])
        rel = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (static_dir / rel).resolve()
        if not str(path).startswith(str(static_dir.resolve())) or not path.exists() or not path.is_file():
            path = static_dir / "index.html"
        # Module scripts require a JavaScript MIME type; the Windows registry can
        # map .js to text/plain, so the console's own asset types are fixed here.
        content_type = STATIC_CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str, attachment: bool = False) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if attachment:
            name = path.name.replace('"', "")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(body)


def start_web_server(ctx: dict[str, Any], host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    server.ctx = ctx  # type: ignore[attr-defined]
    logging.info("Gemini SRT Translator for Bazarr listening on http://%s:%s", host, port)
    server.serve_forever()


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
    worker = QueueWorker(queue_dir=queue_dir, settings=settings, cache=cache, http=http)
    bazarr = BazarrIntegration(http, settings["bazarr_url"], settings["bazarr_api_key"])
    backups = BackupMaintenance(state_dir, config_path, postprocess_targets_path)

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
    ).as_handler_context()
    if not args.no_worker:
        threading.Thread(target=worker.run_forever, args=(args.sleep,), daemon=True).start()
    start_web_server(ctx, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
