"""The routes that are not a page's own table: status, queue, settings, languages, models, logs, events, backups, clean-up
and the connection test (docs/API.md). Each is a thin step: parse the request, call a use case, answer; the work itself is
in ``ConsoleActions``, ``UnitService``, ``SettingsService`` and the adapters named below.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from fastapi import APIRouter, Body, Request
from starlette.responses import Response

from ..adapters.backups import backup_file_path, create_backup, list_backups, restore_backup_archive
from ..adapters.connection_tests import test_connection
from ..adapters.gemini import gemini_models, openai_models
from ..adapters.http import describe_http_error
from ..adapters.logs import clear_logs, read_log_snapshot
from ..adapters.telemetry import events_page
from ..domain.config import KEY_SLOTS, PROVIDER_OPENAI_COMPAT, SECRET_MASK, supported_languages
from .common import binary_body, blocking, ctx_of, error_response, json_response, payload, query_of
from .schemas import (
    BackupBody, CleanupBody, ConnectionBody, DeleteJobBody, JobBody, PrerequisitesBody, RequestObject, RetryBody,
)


def live_settings(ctx: dict[str, Any]) -> dict[str, Any]:
    """The settings as saved right now (a change needs no restart), remembered for the routes that read it."""
    settings = ctx["settings_service"].settings()
    ctx["settings"] = settings
    return settings


def api_status(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = live_settings(ctx)
    snapshot = ctx["console"].snapshot()
    status = {
        "queue": snapshot["counts"],
        "daily_quota_pause": snapshot["daily_quota_pause"],
        "providers": snapshot["providers"],
        "settings": ctx["settings_service"].public(settings),
        "bazarr_url": settings["bazarr_url"],
        "model_layers": ctx["console"].layer_report(),
    }
    try:
        status["bazarr"] = ctx["http"].get_json(
            f"{settings['bazarr_url']}/api/system/ping",
            headers={"X-API-KEY": settings["bazarr_api_key"]},
        )
    except Exception as exc:  # noqa: BLE001
        status["bazarr"] = {"error": describe_http_error(exc, "Bazarr")}
    return status


def test_connection_payload(ctx: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/test-connection: try the key typed in the form, or the stored one when the field is left alone. The
    stored key's status follows the answer (FR-20.4)."""
    service = ctx["settings_service"]
    kind = str(body.get("kind", ""))
    field = "bazarr_api_key" if kind == "bazarr" else kind
    typed = str(body.get(field) or "").strip()
    result = test_connection(kind, service.with_payload(body), ctx["http"])
    if field in KEY_SLOTS and typed in {"", SECRET_MASK}:
        service.record_slot_test(field, result)
    return result


def models_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = live_settings(ctx)
    if settings.get("llm_provider") == PROVIDER_OPENAI_COMPAT:  # the models of the server in use
        items = openai_models(ctx["http"], settings, str(settings.get("openai_compat_api_key") or ""))
    else:
        items = gemini_models(ctx["http"], settings)
    return {"items": items}


def job_spec(ctx: dict[str, Any], job_id: str) -> Response:
    """GET /api/jobs/{id}/spec: the bundle of the job's latest translation attempt (FR-21.2)."""
    job_id = urllib.parse.unquote(job_id)
    if not re.fullmatch(r"[^/\\\x00-\x1f]{1,256}", job_id):
        return error_response("unknown job", status=404)
    spec = ctx["console"].job_spec(job_id)
    if spec is None:
        return error_response("this job has no bundle yet: it has not translated", status=404)
    return json_response(spec)


def retry(ctx: dict[str, Any], body: dict[str, Any]) -> Any:
    if body.get("estimate_only") is True:  # what Retry (or Retry All Failed) would cost; retries nothing
        return ctx["units"].estimate_retry(body)
    if body.get("all") is True or body.get("job_ids") is not None:  # Retry All Failed, or a list of jobs
        return ctx["units"].retry_failed(body)
    return {"ok": ctx["console"].retry(str(body.get("job_id", "")))}


def register(router: APIRouter) -> None:
    @router.get("/api/status")
    async def status(request: Request) -> Response:
        return json_response(await blocking(api_status, ctx_of(request)))

    @router.get("/api/queue")
    async def queue(request: Request) -> Response:
        return json_response(await blocking(ctx_of(request)["console"].snapshot))

    @router.get("/api/jobs/{job_id:path}/spec")
    async def spec(request: Request, job_id: str) -> Response:
        return await blocking(job_spec, ctx_of(request), job_id)

    @router.get("/api/settings")
    async def settings(request: Request) -> Response:
        ctx = ctx_of(request)
        return json_response(await blocking(lambda: ctx["settings_service"].public(live_settings(ctx))))

    @router.post("/api/settings")
    async def save_settings(request: Request, body: RequestObject | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)

        def save() -> dict[str, Any]:
            ctx["settings_service"].save(body)  # live at once: the worker is a subscriber (FR-20.1)
            return ctx["settings_service"].public(live_settings(ctx))

        return json_response(await blocking(save))

    @router.get("/api/languages")
    async def languages(request: Request) -> Response:
        ctx = ctx_of(request)

        def read() -> dict[str, Any]:
            settings = live_settings(ctx)
            return {"items": supported_languages(ctx["http"], settings["bazarr_url"], settings["bazarr_api_key"])}

        return json_response(await blocking(read))

    @router.get("/api/gemini-models")
    async def models(request: Request) -> Response:
        return json_response(await blocking(models_payload, ctx_of(request)))

    @router.get("/api/logs")
    async def logs(request: Request) -> Response:
        return json_response(await blocking(read_log_snapshot, ctx_of(request)["log_dir"]))

    @router.post("/api/logs/clear")
    async def logs_clear(request: Request, body: RequestObject | None = Body(default=None)) -> Response:
        return json_response({"ok": await blocking(clear_logs, ctx_of(request)["log_dir"])})

    @router.get("/api/events")
    async def events(request: Request) -> Response:
        telemetry = ctx_of(request).get("telemetry")
        if telemetry is None or getattr(telemetry, "repo", None) is None:
            return json_response({"items": [], "next_before": None, "telemetry": None})
        return json_response(await blocking(events_page, telemetry, query_of(request)))

    # --- the queue's own buttons ---------------------------------------------------------------------------------

    @router.post("/api/queue/retry")
    async def queue_retry(request: Request, body: RetryBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        return json_response(await blocking(retry, ctx, body))

    @router.post("/api/queue/cancel")
    async def queue_cancel(request: Request, body: JobBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        return json_response({"ok": await blocking(ctx["console"].cancel, str(body.get("job_id", "")))})

    @router.post("/api/queue/request-prerequisites")
    async def queue_prerequisites(request: Request, body: PrerequisitesBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        return json_response(await blocking(
            ctx["units"].request_prerequisites, str(body.get("job_id", "")), estimate_only=body.get("estimate_only") is True,
        ))

    @router.post("/api/queue/stop")
    async def queue_stop(request: Request, body: JobBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        outcome = await blocking(ctx["console"].stop, str(body.get("job_id", "")))
        return json_response({"ok": outcome is not None, "outcome": outcome})

    @router.post("/api/queue/delete")
    async def queue_delete(request: Request, body: DeleteJobBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        return json_response({"ok": await blocking(ctx["console"].delete, str(body.get("state", "")), str(body.get("job_id", "")))})

    # --- backups, clean-up, connection test --------------------------------------------------------------------

    @router.get("/api/backups")
    async def backups(request: Request) -> Response:
        return json_response({"items": await blocking(list_backups, ctx_of(request)["state_dir"])})

    @router.post("/api/backups")
    async def backup_create(request: Request, body: BackupBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        backup = await blocking(
            create_backup, ctx["state_dir"], ctx["config_path"], ctx["postprocess_targets_path"],
            reason="manual", settings=ctx["settings_service"], include_keys=body.get("include_keys") is True,
        )
        return json_response({"backup": backup})

    @router.get("/api/backups/download")
    async def backup_download(request: Request) -> Response:
        backup = backup_file_path(ctx_of(request)["state_dir"], query_of(request).get("name", [""])[0])
        name = backup.name.replace('"', "")
        return Response(
            await blocking(backup.read_bytes), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @router.post("/api/backups/import")
    async def backup_import(request: Request) -> Response:
        ctx, raw = ctx_of(request), await binary_body(request)

        def restore() -> dict[str, Any]:
            result = restore_backup_archive(
                raw, ctx["state_dir"], ctx["config_path"], ctx["postprocess_targets_path"],
                settings=ctx["settings_service"],  # the settings subscribers (the worker) hear of the change
            )
            live_settings(ctx)
            return {"ok": True, **result}

        return json_response(await blocking(restore))

    @router.post("/api/cleanup")
    async def cleanup(request: Request, body: CleanupBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        service = ctx.get("cleanup")
        if service is None:
            return error_response("file cleanup is not available", status=404)
        # A preview unless the request says `"dry_run": false` in so many words (FR-13.4).
        report = await blocking(service.run, dry_run=body.get("dry_run") is not False)
        return json_response(report.as_dict())

    @router.post("/api/test-connection")
    async def connection(request: Request, body: ConnectionBody | None = Body(default=None)) -> Response:
        ctx, body = ctx_of(request), payload(body)
        return json_response(await blocking(test_connection_payload, ctx, body))
