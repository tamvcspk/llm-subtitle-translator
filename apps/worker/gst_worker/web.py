"""The console's HTTP adapter: parses requests, authenticates, calls use cases, formats responses.

An adapter module (stdlib ``http.server``). Routing to the library pages lives in ``library_api``; queue and
unit logic live in ``ConsoleActions`` and ``UnitService``, reached through the handler context.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import urllib.parse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import auth as console_auth
from . import library_api
from .auth import ConsoleAuth
from .backups import backup_file_path, create_backup, list_backups, restore_backup_archive
from .bazarr import list_wanted_items
from .config import load_app_config, save_app_config, supported_languages
from .connection_tests import test_connection
from .gemini import gemini_models
from .http import describe_http_error
from .logs import clear_logs, read_log_snapshot
from .queue import layer_report, queue_snapshot
from .settings import load_settings, public_app_config, settings_from_payload
from .subtitles import scan_source_subtitles


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

    def parse_request(self) -> bool:
        """Authenticate before any route runs (static, /api/*, any method; NFR-5)."""
        if not super().parse_request():
            return False
        self.auth_via = "none"
        self.refreshed_token: str | None = None
        auth = getattr(self.server, "auth", None)
        sessions = getattr(self.server, "sessions", None)
        throttle = getattr(self.server, "throttle", None)
        authorization = self.headers.get("Authorization")
        path = self.path.split("?", 1)[0]
        # No auth handle on the server means a wiring mistake: fail closed.
        if auth is None:
            decision = console_auth.UNCONFIGURED
        elif not auth.enabled:
            self.auth_via = "off"
            return True
        elif not auth.configured:
            decision = console_auth.UNCONFIGURED
        else:
            decision = console_auth.UNAUTHORIZED
        # The request body is left unread on every refusal, so the connection cannot be reused.
        if decision == console_auth.UNCONFIGURED:
            self.close_connection = True
            self.send_error_json(console_auth.UNCONFIGURED_MESSAGE, status=503)
            return False

        # The only requests that need no credentials: the login page and the login call itself.
        is_login_page = self.command in {"GET", "HEAD"} and path == "/login"
        is_login_call = self.command == "POST" and path == "/api/auth/login"
        if (is_login_page or is_login_call) and sessions is not None:
            if is_login_call and not console_auth.same_origin(self.headers.get("Origin"), self.headers.get("Host")):
                self.close_connection = True
                self.send_error_json("cross-origin login refused", status=403)
                return False
            return True

        # Cookie session (browser). Writes must come from this very host (CSRF on top of SameSite=Strict).
        cookie_token = console_auth.session_token(self.headers.get("Cookie")) if sessions is not None else None
        resolved = sessions.resolve(cookie_token) if sessions is not None else None
        if resolved:
            self.refreshed_token = resolved[1]  # sliding expiry: sent back with whatever this request answers
            if self.command not in {"GET", "HEAD", "OPTIONS"} and not console_auth.same_origin(
                self.headers.get("Origin"), self.headers.get("Host")
            ):
                self.close_connection = True
                self.send_error_json("cross-origin request refused", status=403)
                return False
            self.auth_via = "session"
            return True

        # Basic Auth (scripts), shared lockout with the login form.
        address = self.client_address[0]
        if authorization and throttle is not None:
            wait = throttle.locked_for(address)
            if wait:
                self.close_connection = True
                self.send_json({"error": f"too many failed logins; try again in {wait} s"}, status=429,
                               headers={"Retry-After": str(wait)})
                return False
        if authorization and auth.check(authorization) == console_auth.ALLOW:
            if throttle is not None:
                throttle.success(address)
            self.auth_via = "basic"
            return True

        self.close_connection = True
        if authorization:
            if throttle is not None and throttle.fail(address):
                logging.warning("web %s - locked for %s s after repeated wrong logins", address, console_auth.LOCK_SECONDS)
            logging.info(
                "web %s - login refused for %s: %s (client: %s)",
                self.address_string(), path[:80],
                console_auth.explain_failure(auth, authorization),
                (self.headers.get("User-Agent") or "unknown")[:90],
            )
        wants_page = self.command in {"GET", "HEAD"} and not path.startswith("/api/") \
            and "text/html" in (self.headers.get("Accept") or "")
        # A browser (it sends Sec-Fetch-*, or asks for a page) must never see the Basic prompt.
        browser = wants_page or "Sec-Fetch-Mode" in self.headers or bool(cookie_token)
        if sessions is not None and wants_page and not authorization:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif sessions is not None and browser and not authorization:
            self.send_json({"error": "authentication required"}, status=401)
        else:
            self.send_json(
                {"error": "authentication required"},
                status=401,
                headers={"WWW-Authenticate": f'Basic realm="{console_auth.REALM}", charset="UTF-8"'},
            )
        return False

    def end_headers(self) -> None:
        token = getattr(self, "refreshed_token", None)
        if token:
            self.send_header("Set-Cookie", console_auth.session_cookie(token, secure=self.is_https()))
        super().end_headers()

    def is_https(self) -> bool:
        # TLS ends at a reverse proxy; trust its header only to add the Secure flag.
        return (self.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https"

    def api_login(self, body: dict[str, Any]) -> None:
        """POST /api/auth/login: the one call that runs without credentials (POC of the form login)."""
        auth = self.server.auth  # type: ignore[attr-defined]
        sessions = getattr(self.server, "sessions", None)
        throttle = getattr(self.server, "throttle", None)
        if not auth.enabled:
            self.send_json({"ok": True, "via": "off"})
            return
        if sessions is None or throttle is None:
            self.send_error_json("form login is not available", status=404)
            return
        address = self.client_address[0]
        wait = throttle.locked_for(address)
        if wait:
            self.send_json({"error": f"too many failed logins; try again in {wait} s"}, status=429,
                           headers={"Retry-After": str(wait)})
            return
        user, password = str(body.get("user", "")), str(body.get("password", ""))
        if not auth.verify(user, password):
            if throttle.fail(address):
                logging.warning("web %s - locked for %s s after repeated wrong logins", address, console_auth.LOCK_SECONDS)
            logging.info("web %s - form login refused (wrong user name or password)", address)
            self.send_error_json("Wrong user name or password", status=401)
            return
        throttle.success(address)
        token = sessions.create(auth.name)
        self.send_json({"ok": True, "user": auth.name},
                       headers={"Set-Cookie": console_auth.session_cookie(token, secure=self.is_https())})

    def api_change_password(self, body: dict[str, Any]) -> None:
        """POST /api/auth/password: the caller proves the current password; every session then ends."""
        auth = self.server.auth  # type: ignore[attr-defined]
        sessions = getattr(self.server, "sessions", None)
        throttle = getattr(self.server, "throttle", None)
        if not auth.can_change_password:
            self.send_error_json("The password is not managed by the console (authentication is off)", status=409)
            return
        address = self.client_address[0]
        wait = throttle.locked_for(address) if throttle is not None else 0
        if wait:
            self.send_json({"error": f"too many failed attempts; try again in {wait} s"}, status=429,
                           headers={"Retry-After": str(wait)})
            return
        current, new = str(body.get("current", "")), str(body.get("new", ""))
        # 403, not 401: the browser client treats 401 as "session ended" and leaves the page.
        if not auth.password_matches(current):
            if throttle is not None and throttle.fail(address):
                logging.warning("web %s - locked for %s s after repeated wrong logins", address, console_auth.LOCK_SECONDS)
            logging.info("web %s - password change refused (wrong current password)", address)
            self.send_error_json("The current password is wrong", status=403)
            return
        if not console_auth.MIN_PASSWORD_LENGTH <= len(new) <= console_auth.MAX_PASSWORD_LENGTH:
            self.send_error_json(
                f"The new password must be {console_auth.MIN_PASSWORD_LENGTH}-{console_auth.MAX_PASSWORD_LENGTH} characters",
                status=400,
            )
            return
        if throttle is not None:
            throttle.success(address)
        auth.change_password(new)  # also rotates the session secret: every session, this one too, is dead
        self.refreshed_token = None
        headers: dict[str, str] = {}
        if sessions is not None:
            sessions.revoke_all()
            if self.auth_via == "session":  # the person who changed it stays signed in
                headers["Set-Cookie"] = console_auth.session_cookie(sessions.create(auth.name), secure=self.is_https())
        logging.info("web %s - console password changed; all sessions ended", address)
        self.send_json({"ok": True}, headers=headers)

    def send_json(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"error": message}, status=status)

    def library_api(self, method: str, parsed: urllib.parse.ParseResult, body: dict[str, Any] | None = None) -> bool:
        """Serve the library/episode/glossary routes; False when the path is not one of them."""
        service = self.ctx.get("units")
        if service is None or not library_api.handles(parsed.path):
            return False
        result = library_api.handle(service, method, parsed.path, urllib.parse.parse_qs(parsed.query), body)
        if result is None:
            return False
        self.send_json(result[1], status=result[0])
        return True

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if self.library_api("GET", parsed):
                return
            if parsed.path == "/login":
                self.serve_static("/login.html", no_store=True)
            elif parsed.path == "/api/auth/me":
                auth = self.server.auth  # type: ignore[attr-defined]
                user = auth.name if self.auth_via in {"session", "basic"} else None
                self.send_json({"user": user, "via": self.auth_via, "can_change_password": auth.can_change_password})
            elif parsed.path == "/api/status":
                self.send_json(self.api_status())
            elif parsed.path == "/api/queue":
                self.send_json(queue_snapshot(self.ctx["queue_dir"]))
            elif parsed.path in {"/api/queue/held", "/api/queue/rejected"}:
                jobs = self.ctx["console"].held() if parsed.path.endswith("held") else self.ctx["console"].rejected()
                self.send_json({"count": len(jobs), "items": [asdict(job) for job in jobs]})
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
            if self.library_api("POST", parsed, body):
                return
            if parsed.path == "/api/auth/login":
                self.api_login(body)
            elif parsed.path == "/api/auth/password":
                self.api_change_password(body)
            elif parsed.path == "/api/auth/logout":
                sessions = getattr(self.server, "sessions", None)
                if sessions is not None:
                    sessions.revoke(console_auth.session_token(self.headers.get("Cookie")))
                self.refreshed_token = None
                self.send_json({"ok": True}, headers={"Set-Cookie": console_auth.session_cookie("", secure=self.is_https(), max_age=0)})
            elif parsed.path == "/api/settings":
                saved = save_app_config(self.ctx["config_path"], body, self.ctx["postprocess_targets_path"])
                settings = load_settings(self.ctx["config_path"])
                self.ctx["settings"] = settings
                if self.ctx.get("worker") is not None:
                    self.ctx["worker"].apply_settings(settings)
                self.send_json(public_app_config(self.ctx["config_path"], settings))
            elif parsed.path == "/api/enqueue":
                config = load_app_config(self.ctx["config_path"])
                created = self.ctx["console"].enqueue_subtitle(body, config["target_languages"])
                self.send_json({"created": created, "count": len(created)})
            elif parsed.path == "/api/enqueue-scan":
                config = load_app_config(self.ctx["config_path"])
                items = scan_source_subtitles(
                    config["media_roots"],
                    config["source_languages"],
                    config["target_languages"],
                    limit=int(config["scan_limit"]),
                )
                created = self.ctx["console"].enqueue_found(items)
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
            elif parsed.path in {"/api/queue/approve", "/api/queue/reject", "/api/queue/restore"}:
                action = parsed.path.rsplit("/", 1)[1]
                ids = body.get("job_ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(item, str) for item in ids)):
                    raise ValueError("job_ids must be a list of job ids")
                everything = body.get("all") is True
                if action == "restore":
                    moved = self.ctx["console"].restore(job_ids=ids, everything=everything)
                else:
                    moved = getattr(self.ctx["console"], action)(
                        job_ids=ids, series_id=str(body.get("series_id") or "") or None, everything=everything,
                    )
                self.send_json({"ok": True, "count": len(moved), "jobs": moved})
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

    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if not self.library_api("PUT", parsed, read_json_body(self)):
                self.send_error_json("not found", status=404)
        except ValueError as exc:
            self.send_error_json(str(exc), status=400)
        except Exception as exc:
            logging.exception("PUT %s failed", self.path)
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
            "model_layers": layer_report(self.ctx["queue_dir"]),
        }
        try:
            status["bazarr"] = self.ctx["http"].get_json(
                f"{settings['bazarr_url']}/api/system/ping",
                headers={"X-API-KEY": settings["bazarr_api_key"]},
            )
        except Exception as exc:
            status["bazarr"] = {"error": describe_http_error(exc, "Bazarr")}
        return status

    def serve_static(self, request_path: str, no_store: bool = False) -> None:
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
        self.send_header("Cache-Control", "no-store" if no_store else "no-cache")
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


def start_web_server(ctx: dict[str, Any], host: str, port: int, auth: ConsoleAuth) -> None:
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    server.ctx = ctx  # type: ignore[attr-defined]
    server.auth = auth  # type: ignore[attr-defined]
    server.sessions = console_auth.SessionStore.for_auth(auth)  # type: ignore[attr-defined]
    server.throttle = console_auth.LoginThrottle()  # type: ignore[attr-defined]
    auth.log_startup()
    logging.info("Gemini SRT Translator for Bazarr listening on http://%s:%s", host, port)
    server.serve_forever()
