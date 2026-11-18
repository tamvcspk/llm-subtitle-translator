"""Work the admin asked for by hand that takes long (reading the facts of many episodes, asking for proposals) and must
not hold an HTTP request open: a box that spends model calls can be closed while the work goes on, and Activity shows
how far it is.

This is not the job queue. A job is a translation, it lives in the database and survives a restart; a background task
is one of the admin's own long requests, runs one at a time on one thread (so two requests never double the calls per
minute), and lives in memory only. A restart forgets the list, which costs nothing: what a task stored (facts, proposals)
is stored per item as it goes, so asking again picks up where it stopped and pays only for what is left.

Nothing here knows what a task does. The caller hands ``work(progress)``; ``progress(done, total, message)`` says how far
it is and raises :class:`Cancelled` once the admin asked to stop, so a task stops between its items, never inside a call.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
ACTIVE = (QUEUED, RUNNING)
KEEP_FINISHED = 30  # the latest finished tasks stay listed; older ones are forgotten


class Cancelled(Exception):
    """Raised by ``progress`` when the admin stopped the task: the work ends at the next item."""


@dataclass
class Task:
    id: str
    kind: str  # extract-facts | suggest
    title: str
    series_id: int | None
    created_at: float
    state: str = QUEUED
    done: int = 0
    total: int = 0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested: bool = field(default=False, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "series_id": self.series_id, "state": self.state,
            "done": self.done, "total": self.total, "message": self.message, "result": self.result,
            "error": self.error, "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "cancel_requested": self.cancel_requested,
        }


Progress = Callable[..., None]
Work = Callable[[Progress], dict[str, Any] | None]


class BackgroundTasks:
    def __init__(
        self, *, clock: Callable[[], float] = time.time, redact: Callable[[str], str] | None = None,
        keep: int = KEEP_FINISHED,
    ) -> None:
        self._clock = clock
        self._redact = redact or (lambda text: text)
        self._keep = keep
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._tasks: dict[str, Task] = {}
        self._work: dict[str, Work] = {}
        self._line: deque[str] = deque()
        self._thread: threading.Thread | None = None

    # -- what the HTTP layer calls ----------------------------------------------------------------------------
    def submit(self, kind: str, title: str, work: Work, *, series_id: int | None = None) -> dict[str, Any]:
        """Queue ``work``; it starts when the tasks before it are done. Returns the task as listed."""
        task = Task(uuid.uuid4().hex[:12], kind, title, series_id, self._clock())
        with self._lock:
            self._tasks[task.id] = task
            self._work[task.id] = work
            self._line.append(task.id)
            self._forget_old()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="background-tasks", daemon=True)
                self._thread.start()
            self._wake.notify_all()
            return task.to_json()

    def tasks(self) -> list[dict[str, Any]]:
        """Active tasks first (running, then queued, in line order), then the finished ones, newest first."""
        with self._lock:
            active = [t for t in self._tasks.values() if t.state in ACTIVE]
            active.sort(key=lambda t: (t.state != RUNNING, t.created_at))
            finished = sorted((t for t in self._tasks.values() if t.state not in ACTIVE), key=lambda t: -(t.finished_at or 0))
            return [t.to_json() for t in (*active, *finished)]

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise LookupError("no such task: it finished long ago or the worker restarted")
            return task.to_json()

    def cancel(self, task_id: str) -> dict[str, Any]:
        """Stop a queued task now, or a running one at its next item. A finished task is left as it is."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise LookupError("no such task: it finished long ago or the worker restarted")
            if task.state == QUEUED:
                self._line.remove(task_id)
                self._work.pop(task_id, None)
                task.state, task.finished_at, task.message = CANCELLED, self._clock(), "stopped before it started"
            elif task.state == RUNNING:
                task.cancel_requested = True
            return task.to_json()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until nothing is queued or running (for tests and for a clean exit)."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while any(t.state in ACTIVE for t in self._tasks.values()):
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._wake.wait(left)
        return True

    # -- the thread -------------------------------------------------------------------------------------------------
    def _forget_old(self) -> None:
        finished = sorted((t for t in self._tasks.values() if t.state not in ACTIVE), key=lambda t: t.finished_at or 0)
        for task in finished[: max(0, len(finished) - self._keep)]:
            del self._tasks[task.id]

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._line:
                    self._thread = None
                    return
                task = self._tasks[self._line.popleft()]
                work = self._work.pop(task.id)
                task.state, task.started_at = RUNNING, self._clock()
            self._execute(task, work)
            with self._lock:
                self._forget_old()
                self._wake.notify_all()

    def _execute(self, task: Task, work: Work) -> None:
        def progress(done: int, total: int, message: str = "") -> None:
            with self._lock:
                task.done, task.total = int(done), int(total)
                if message:
                    task.message = message
                if task.cancel_requested:
                    raise Cancelled()

        outcome: dict[str, Any] | None = None
        state, error, message = DONE, "", ""
        try:
            outcome = work(progress)
        except Cancelled:
            state, message = CANCELLED, "stopped"
        except Exception as exc:  # noqa: BLE001 - a task fails on its own; it never takes the thread down
            state = FAILED
            # A ValueError is a message written for the admin (a use case's own); anything else gets its type named.
            error = self._redact(str(exc) if isinstance(exc, (ValueError, LookupError)) else f"{type(exc).__name__}: {exc}")
            logging.warning("Background task %s (%s) failed: %s", task.id, task.kind, error)
        with self._lock:
            task.state, task.error, task.result, task.finished_at = state, error, outcome, self._clock()
            if message:
                task.message = message
            if state == DONE and task.total:
                task.done = task.total
