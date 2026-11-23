"""The trace the worker leaves of what it decides and what it asks the model (FR-19, NFR-11, ADR-15).

``Telemetry`` takes events (decisions with their reason), quality signals, model-call records and the bodies of
those calls, and writes them through the ports on **one background thread**, so the job never waits for the
disk and never sits inside a database transaction of its own. The queue between the job and the thread is
bounded: when it is full, or a write fails (a full disk, a locked database), the record is dropped, counted
(``dropped``) and mentioned in the log now and then. Nothing here raises into the caller (NFR-11).

Every text on its way out passes the shared redactor, so no key, password, cookie or header reaches the
database, the log or a body file (FR-19.3).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from .bodies import DEFAULT_BODY_MAX_BYTES, MAX_SEGMENT_BYTES, BodyLog
from .logs import normalize_log_level, set_log_level
from .store import Store
from ..domain.redaction import DEFAULT, Redactor

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
SAMPLE_EVERY = 10  # `log_prompt_bodies = sample` keeps failures and one call in this many
MAINTENANCE_SECONDS = 3600.0
QUEUE_SIZE = 2000
_events_log = logging.getLogger("gst.event")
_module_log = logging.getLogger(__name__)


def events_page(telemetry: "Telemetry", query: Mapping[str, list[str]]) -> dict[str, Any]:
    """The answer of ``GET /api/events``: newest first, filtered by ``job``, ``unit``, ``kind`` (a trailing dot
    matches a family: ``job.``), ``level`` (that level and worse) and ``since`` (epoch seconds); ``before`` is
    the id of the last row of the previous page. Raises ``ValueError`` for a malformed number."""

    def one(name: str) -> str:
        values = query.get(name)
        return values[0].strip() if values else ""

    def number(name: str, cast: Callable[[str], Any]) -> Any:
        raw = one(name)
        if not raw:
            return None
        try:
            return cast(raw)
        except ValueError:
            raise ValueError(f"{name} must be a number") from None

    limit = number("limit", int) or 100
    floor = _LEVELS.get(one("level").upper(), 0)
    if telemetry.repo is None:
        return {"items": [], "next_before": None, "telemetry": telemetry.stats()}
    rows = telemetry.repo.events(
        job_id=one("job") or None, unit_id=number("unit", int), kind=one("kind") or None,
        since=number("since", float), before_id=number("before", int), limit=limit,
    )
    items = [
        {"id": e.id, "at": e.at, "level": e.level, "kind": e.kind, "job_id": e.job_id, "unit_id": e.unit_id,
         "reason": e.reason, "data": e.data}
        for e in rows if _LEVELS.get(e.level, 0) >= floor
    ]
    return {
        "items": items,
        "next_before": rows[-1].id if len(rows) >= max(1, min(limit, 1000)) else None,
        "telemetry": telemetry.stats(),
    }


class NullTelemetry:
    """Records nothing. The default wherever telemetry is optional, so core code never tests for None."""

    def event(self, kind: str, **_: Any) -> None:
        return None

    def signal(self, kind: str, **_: Any) -> None:
        return None

    def llm_call(self, *_: Any, **__: Any) -> None:
        return None


NULL = NullTelemetry()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Telemetry:
    """See the module docstring. ``synchronous`` runs each write in the caller (no thread): for the plain
    call record of a bare attempt and for tests that want no waiting."""

    def __init__(
        self,
        repo: Store | None = None,
        *,
        usage: Store | None = None,
        bodies: BodyLog | None = None,
        settings_source: Callable[[], Mapping[str, Any]] | None = None,
        redactor: Redactor | None = None,
        clock: Callable[[], float] = time.time,
        queue_size: int = QUEUE_SIZE,
        synchronous: bool = False,
    ) -> None:
        self.repo = repo
        self.usage = usage
        self.bodies = bodies
        self._settings = settings_source or (lambda: {})
        self.redactor = redactor or DEFAULT
        self._clock = clock
        self._synchronous = synchronous
        self._queue: deque[Callable[[], None]] = deque()
        self._capacity = max(1, queue_size)
        self._cv = threading.Condition()
        self._pending = 0  # queued or being written
        self._stop = False
        self._thread: threading.Thread | None = None
        self._calls = 0
        self.dropped = 0
        self.written = 0
        self._last_warning = 0.0
        self._last_maintenance = 0.0
        self._bytes_since_prune = 0

    # -- settings --------------------------------------------------------------------------
    def setting(self, name: str, default: Any = None) -> Any:
        try:
            value = self._settings().get(name, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    def apply_settings(self, settings: Mapping[str, Any]) -> None:
        """Take effect at once: the log level, and the secrets the redactor must hide."""
        set_log_level(settings.get("log_level"))
        self.redactor.register_settings(dict(settings))

    @property
    def level(self) -> str:
        return normalize_log_level(self.setting("log_level", "INFO"))

    # -- entry points (never raise) ----------------------------------------------------------
    def event(
        self, kind: str, *, level: str = "INFO", job_id: str = "", unit_id: int | None = None, reason: str = "",
        **data: Any,
    ) -> None:
        try:
            name = normalize_log_level(level) if level.upper() in _LEVELS else "INFO"
            reason = self.redactor.redact(str(reason or ""))
            clean = self.redactor.clean(data)
            _events_log.log(
                _LEVELS[name],
                "EVENT %s",
                json.dumps(
                    {"kind": kind, "job": job_id or None, "unit": unit_id, "reason": reason or None, **clean},
                    ensure_ascii=False, sort_keys=True, default=str,
                ),
            )
            if _LEVELS[name] < _LEVELS[self.level] or self.repo is None:
                return
            repo, at = self.repo, self._clock()
            self._submit(lambda: repo.add_event(
                kind, level=name, job_id=str(job_id or ""), unit_id=unit_id, reason=reason, data=clean, at=at,
            ))
        except Exception:  # noqa: BLE001
            self._count_drop("event")

    def signal(
        self, kind: str, *, unit_id: int | None = None, version: int | None = None, cue: int | None = None,
        before: str = "", after: str = "", **meta: Any,
    ) -> None:
        if self.repo is None:
            return
        try:
            repo, at = self.repo, self._clock()
            before_text = self.redactor.redact(str(before or ""), loose=False)
            after_text = self.redactor.redact(str(after or ""), loose=False)
            clean = self.redactor.clean(meta)
            self._submit(lambda: repo.add_signal(
                kind, unit_id=unit_id, version=version, cue=cue, before=before_text, after=after_text, meta=clean, at=at,
            ))
        except Exception:  # noqa: BLE001
            self._count_drop("signal")

    def llm_call(
        self, job_id: str, log: Any, *, prompt_version: str = "", glossary_hash: str = "", bundle_hash: str = "",
        style_hash: str = "", series_id: int | None = None,
    ) -> None:
        """Record one provider call (``log`` is a ``subcore`` ``CallLog``) and, per ``log_prompt_bodies``, its body.
        ``series_id`` is the series the job is for (the research budget counts calls by it)."""
        if self.usage is None:
            return
        try:
            usage, at = self.usage, self._clock()
            price_in = _float(self.setting("llm_cost_in_per_mtok", 0.0))
            price_out = _float(self.setting("llm_cost_out_per_mtok", 0.0))
            cost = (log.tokens_in * price_in + log.tokens_out * price_out) / 1_000_000.0
            fields = dict(
                provider=log.provider, model=log.model, task=log.task, tokens_in=log.tokens_in,
                tokens_out=log.tokens_out, latency_ms=log.latency_ms, outcome=log.outcome, at=at,
                kind=getattr(log, "kind", "") or "", prompt_version=prompt_version, bundle_hash=bundle_hash,
                style_hash=style_hash, glossary_hash=glossary_hash, batch=getattr(log, "batch", 0) or 0,
                http_status=getattr(log, "http_status", None), finish_reason=getattr(log, "finish_reason", "") or "",
                retries=getattr(log, "retry", 0) or 0,
                repairs=(getattr(log, "targets", 0) or 0) if getattr(log, "kind", "") == "repair" else 0,
                cost_est=cost, series_id=series_id,
            )
            keep = self._keep_body(log.outcome)
            body = self._body_source(job_id, log, fields) if keep else None
            max_bytes = _int(self.setting("log_body_max_bytes", DEFAULT_BODY_MAX_BYTES), DEFAULT_BODY_MAX_BYTES)
            limit_mb = _int(self.setting("log_max_mb", 0), 0)
            segment = min(MAX_SEGMENT_BYTES, max(65_536, limit_mb * 1024 * 1024 // 4)) if limit_mb > 0 else MAX_SEGMENT_BYTES
            max_bytes = min(max_bytes, segment)
            self._submit(lambda: self._write_call(usage, job_id, fields, body, max_bytes, segment))
        except Exception:  # noqa: BLE001
            self._count_drop("llm_call")

    # -- bodies ------------------------------------------------------------------------------
    def _keep_body(self, outcome: str) -> bool:
        if self.bodies is None or self.repo is None:
            return False
        mode = str(self.setting("log_prompt_bodies", "all")).strip().lower()
        if mode == "off":
            return False
        if mode == "all":
            return True
        if outcome != "ok":
            return True
        if mode == "sample":
            with self._cv:
                self._calls += 1
                return self._calls % SAMPLE_EVERY == 0
        return False  # "errors": only failed calls

    @staticmethod
    def _body_source(job_id: str, log: Any, fields: Mapping[str, Any]) -> dict[str, Any] | None:
        request = getattr(log, "request", None)
        if request is None:
            return None
        return {
            "at": fields["at"], "job": job_id, "provider": log.provider, "model": log.model, "task": log.task,
            "kind": fields["kind"], "batch": fields["batch"], "retry": fields["retries"], "outcome": log.outcome,
            "http_status": fields["http_status"], "finish_reason": fields["finish_reason"],
            "tokens_in": log.tokens_in, "tokens_out": log.tokens_out,
            "system": getattr(request, "system", ""), "user": getattr(request, "user", ""),
            "response": getattr(log, "response_text", ""),
        }

    def _write_call(
        self, usage: Store, job_id: str, fields: dict[str, Any], body: dict[str, Any] | None, max_bytes: int,
        segment_bytes: int,
    ) -> None:
        call_id = usage.record_llm_call(job_id, **fields)
        if body is None or self.bodies is None or self.repo is None:
            return
        # Subtitle text is dialogue: only the secret shapes go, not "password: ..." as a word (loose=False).
        record = {"call": call_id, **{k: (self.redactor.redact(v, loose=False) if isinstance(v, str) else v) for k, v in body.items()}}
        name, offset, length = self.bodies.write(record, max_bytes=max_bytes, segment_bytes=segment_bytes)
        self.repo.add_body_ref(int(call_id), name, offset, length)
        self._bytes_since_prune += length
        if self._bytes_since_prune >= segment_bytes:
            self._bytes_since_prune = 0
            self._prune_bodies()

    # -- the writer ---------------------------------------------------------------------------
    def _submit(self, task: Callable[[], Any]) -> None:
        if self._synchronous:
            self._run(task)
            return
        with self._cv:
            if len(self._queue) >= self._capacity or self._stop:
                self._count_drop("queue full")
                return
            self._queue.append(task)
            self._pending += 1
            self._cv.notify_all()
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._cv:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="telemetry", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait()
                if not self._queue and self._stop:
                    return
                task = self._queue.popleft()
            try:
                self._run(task)
            finally:
                with self._cv:
                    self._pending -= 1
                    self._cv.notify_all()

    def _run(self, task: Callable[[], Any]) -> None:
        try:
            task()
            self.written += 1
        except Exception as exc:  # noqa: BLE001 - a full disk or a busy database must never reach the job
            self._count_drop(f"{type(exc).__name__}")
        now = self._clock()
        if now - self._last_maintenance >= MAINTENANCE_SECONDS:
            self.maintain(now)

    def _count_drop(self, why: str) -> None:
        self.dropped += 1
        now = time.monotonic()
        if self.dropped == 1 or now - self._last_warning >= 60.0:
            self._last_warning = now
            _module_log.warning("Telemetry dropped %s record(s) so far (latest cause: %s)", self.dropped, why)

    def stats(self) -> dict[str, int]:
        """How many records were written, and how many were dropped (a full queue or a failed write; NFR-11)."""
        return {"written": self.written, "dropped": self.dropped, "queued": len(self._queue)}

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until everything submitted so far has been written (or dropped). False on timeout."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._pending > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def close(self, timeout: float = 10.0) -> None:
        self.flush(timeout)
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        if self.bodies is not None:
            try:
                self.bodies.close_active()
            except Exception:  # noqa: BLE001
                pass

    # -- housekeeping ----------------------------------------------------------------------------
    def maintain(self, now: float | None = None) -> None:
        """Delete events past ``events_retention_days`` and body segments past their limits. Best effort."""
        self._last_maintenance = self._clock() if now is None else now
        moment = self._last_maintenance
        try:
            days = _int(self.setting("events_retention_days", 90), 90)
            if self.repo is not None and days > 0:
                self.repo.prune_events(moment - days * 86400)
        except Exception:  # noqa: BLE001
            self._count_drop("prune events")
        self._prune_bodies(moment)

    def _prune_bodies(self, now: float | None = None) -> list[str]:
        if self.bodies is None:
            return []
        try:
            removed = self.bodies.prune(
                max_bytes=_int(self.setting("log_max_mb", 0), 0) * 1024 * 1024,
                retention_days=_int(self.setting("log_retention_days", 0), 0),
                now=now,
            )
            if removed and self.repo is not None:
                self.repo.mark_bodies_rotated(removed)
            return removed
        except Exception:  # noqa: BLE001
            self._count_drop("prune bodies")
            return []
