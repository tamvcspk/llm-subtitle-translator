"""Insights: the figures of FR-19.5, computed from SQLite and never from a file (P15, ADR-15).

The raw records (``llm_calls``, ``quality_signals``, the ``translation.finished`` events) are rolled up into
``metrics_daily``, one row per UTC day, figure name and labels (model, prompt version...). The report adds those rows
up, so a figure survives the clean-up of the events it came from; what a sum cannot give (the median wait, the 90th
percentile of a latency, the state of the queue now) is read from the raw tables or the live queue.

Rates are ``None`` when there is nothing to divide by: "no data" is not "0 %".

Figures and where they come from
================================
Operations: queue depth and provider state (the live queue), wait before a job starts (``jobs``), calls used today
against the limits (``llm_calls``), the share of calls answered 429 and 503 (``outcome``), escalation and rescue
(``JobQueue.layer_report``), tokens and cost per episode, latency, calls by task and what research cost, the days the daily
quota stopped the queue, breaker opens, how jobs ended (``jobs``) and retries per episode (a ``job.waiting`` for the provider).
Quality: calls that were repairs, **the share of cues an admin corrected**, retranslations, cues over the reading speed
or too long (the review of each translation), the A/B score of a style, all by model and prompt version; the same five
rates for the window just before (``previous``) so the page can say better or worse.
Corrections: how many cues of each series and language the admin corrected (the size of its golden set).

A figure added later (quota pauses, breaker opens, retries) is only rolled up from the day the worker that knows it ran: an
earlier day reads 0 for it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from ..domain.ports import MetricRow, QualitySignal
from ..domain.queue_policy import next_utc_midnight
from ..adapters.store import Store

DAY = 86_400
MAX_DAYS = 365
MARK_DAY = "0000-00-00"  # the rows of this day are roll-up markers, not figures
PAGE = 1_000  # what ``Store.events`` returns at most

LLM_NAMES = ("calls", "tokens_in", "tokens_out", "cost", "latency_ms", "repair_cues")
EVENT_NAMES = ("episodes", "cues_translated", "review", "quota_pauses", "breaker_opens", "retries")
WAITS_FOR_THE_PROVIDER = ("rate", "provider")  # a ``job.waiting`` for one of these is a retry; a wait for the quota or a time is not
JOB_KINDS = ("publish", "variant")  # the jobs whose wait and ending Insights counts: the ones that make a translation
SIGNAL_NAMES = ("cue_edits", "retranslations", "reverts", "validate", "ab")
REPAIR_KINDS = ("repair", "split")
FAILED = ("rate_limited", "quota_exhausted", "unavailable", "bad_output", "auth_error", "model_not_found", "error")


def _day(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d")


def _floor(at: float) -> float:
    return float(int(at // DAY) * DAY)


def _rate(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole > 0 else None


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def _row(day: str, name: str, value: float, **labels: str) -> MetricRow:
    return MetricRow(day, name, tuple(sorted((k, str(v)) for k, v in labels.items())), float(value))


class Metrics:
    def __init__(
        self,
        store: Store,
        *,
        console: Any = None,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        limits: Callable[[], tuple[int, int, int]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.console = store, console
        self._settings = settings_source or (lambda: {})
        self._limits = limits or (lambda: (0, 0, 0))
        self._clock = clock

    # -- roll-up -------------------------------------------------------------------------------------------
    def rollup(self, days: int = 30) -> None:
        """Bring ``metrics_daily`` up to date for the last ``days`` days. Safe to repeat: a day is replaced as a whole, and
        only the days since the last roll-up (and the one before it, for what arrived late) are redone."""
        now = self._clock()
        window_start = _floor(now) - (max(1, min(int(days), MAX_DAYS)) - 1) * DAY
        retention = int(self._settings().get("events_retention_days") or 0)
        events_floor = _floor(now - retention * DAY) + DAY if retention > 0 else 0.0
        for source, names, build, floor in (
            ("llm", LLM_NAMES, self._llm_rows, 0.0),
            ("events", EVENT_NAMES, self._event_rows, events_floor),
            ("signals", SIGNAL_NAMES, self._signal_rows, 0.0),
        ):
            start = self._start_of(source, window_start, now)
            if self._known_from(source) > 0:
                # A day the clean-up has begun to prune is never recomputed from what is left: its figures were rolled
                # while its events were whole. (The first roll-up has nothing earlier to protect.)
                start = max(start, floor)
            if start > now:
                continue
            self.store.metrics_replace(_day(start), names, build(start))
            self.store.metrics_replace(MARK_DAY, (f"_through.{source}", f"_from.{source}"), [
                _row(MARK_DAY, f"_through.{source}", now),
                _row(MARK_DAY, f"_from.{source}", min(window_start, self._known_from(source) or window_start)),
            ])

    def _known_from(self, source: str) -> float:
        return self.store.metric_marker(f"_from.{source}")

    def _start_of(self, source: str, window_start: float, now: float) -> float:
        through = self.store.metric_marker(f"_through.{source}")
        known_from = self._known_from(source)
        if through <= 0 or known_from <= 0 or window_start < known_from:
            return window_start  # never rolled, or a longer window than before: from its first day
        return max(window_start, _floor(through) - DAY)

    def _llm_rows(self, start: float) -> list[MetricRow]:
        rows: list[MetricRow] = []
        for c in self.store.call_days(start):
            labels = dict(model=c.model, prompt_version=c.prompt_version, task=c.task, kind=c.kind, outcome=c.outcome)
            rows += [
                _row(c.day, "calls", c.calls, **labels), _row(c.day, "tokens_in", c.tokens_in, **labels),
                _row(c.day, "tokens_out", c.tokens_out, **labels), _row(c.day, "cost", c.cost, **labels),
                _row(c.day, "latency_ms", c.latency_ms, **labels), _row(c.day, "repair_cues", c.repairs, **labels),
            ]
        return rows

    def _paged(self, read: Callable[..., list[Any]], start: float, **filters: Any) -> Iterable[Any]:
        before = None
        while True:
            page = read(since=start, before_id=before, limit=PAGE, **filters)
            yield from page
            if len(page) < PAGE:
                return
            before = page[-1].id

    def _prompts(self, job_ids: Iterable[str]) -> dict[str, str]:
        return {p.job_id: p.prompt_version for p in self.store.job_prompts(job_ids)}

    def _event_rows(self, start: float) -> list[MetricRow]:
        finished = list(self._paged(self.store.events, start, kind="translation.finished"))
        prompts = self._prompts(e.job_id for e in finished)
        total: dict[tuple[str, str, tuple[tuple[str, str], ...]], float] = {}

        def add(day: str, name: str, value: float, **labels: str) -> None:
            key = (day, name, tuple(sorted(labels.items())))
            total[key] = total.get(key, 0.0) + value

        for event in finished:
            day, model = _day(event.at), str(event.data.get("model") or "")
            prompt = prompts.get(event.job_id, "")
            add(day, "episodes", 1, model=model, prompt_version=prompt)
            add(day, "cues_translated", _count(event.data.get("cues")), model=model, prompt_version=prompt)
            review = event.data.get("review")
            for kind, n in (review.items() if isinstance(review, dict) else ()):
                add(day, "review", _count(n), kind=str(kind), model=model, prompt_version=prompt)
        # The daily quota is spent once a day however many jobs wait for it, so a pause is a day on which one did.
        paused_days = {_day(e.at) for e in self._paged(self.store.events, start, kind="quota.paused")}
        for day in paused_days:
            add(day, "quota_pauses", 1)
        for event in self._paged(self.store.events, start, kind="breaker.opened"):
            add(_day(event.at), "breaker_opens", 1, model=str(event.data.get("model") or ""))
        for event in self._paged(self.store.events, start, kind="job.waiting"):
            if event.data.get("wait") in WAITS_FOR_THE_PROVIDER:
                add(_day(event.at), "retries", 1)
        return [MetricRow(day, name, labels, value) for (day, name, labels), value in total.items()]

    def _signal_rows(self, start: float) -> list[MetricRow]:
        signals: list[QualitySignal] = []
        for kind in ("cue_edit", "retranslate", "revert", "validate", "ab"):
            signals += self._paged(self.store.signals, start, kind=kind)
        prompts = self._prompts(str(s.meta.get("job_id") or "") for s in signals if s.kind == "cue_edit")
        total: dict[tuple[str, str, tuple[tuple[str, str], ...]], float] = {}
        edited: set[tuple[str, Any, Any, str, str]] = set()

        def add(day: str, name: str, **labels: str) -> None:
            key = (day, name, tuple(sorted(labels.items())))
            total[key] = total.get(key, 0.0) + 1

        for s in signals:
            day, meta = _day(s.at), s.meta
            if s.kind == "cue_edit":
                model, prompt = str(meta.get("model") or ""), prompts.get(str(meta.get("job_id") or ""), "")
                if (day, s.unit_id, s.cue, model, prompt) not in edited:  # a cue edited twice in a day is one cue
                    edited.add((day, s.unit_id, s.cue, model, prompt))
                    add(day, "cue_edits", model=model, prompt_version=prompt)
            elif s.kind == "retranslate":
                add(day, "retranslations", model=str(meta.get("model") or ""),
                    scope="episode" if meta.get("whole_episode") else "range")
            elif s.kind == "revert":
                add(day, "reverts")
            elif s.kind == "validate":
                add(day, "validate", issue=str(meta.get("issue") or ""), model=str(meta.get("model") or ""))
            elif s.kind == "ab":
                style = meta.get("label") or meta.get("preset") or meta.get("style") or ""
                add(day, "ab", style=str(style), prompt_version=str(meta.get("prompt_version") or ""),
                    winner=str(meta.get("winner") or ""), scope="episode" if meta.get("whole_episode") else "cue")
        return [MetricRow(day, name, labels, value) for (day, name, labels), value in total.items()]

    # -- the report ------------------------------------------------------------------------------------------
    def report(self, days: int = 30) -> dict[str, Any]:
        days = max(1, min(int(days), MAX_DAYS))
        before = min(days, MAX_DAYS - days)  # the window before this one, as long as it, as far back as a roll-up goes
        self.rollup(days + before)
        now = self._clock()
        window_start = _floor(now) - (days - 1) * DAY
        first = _day(window_start)
        rows = self.store.metric_rows(_day(window_start - before * DAY))
        this = _Figures([r for r in rows if r.day >= first])
        previous = _Figures([r for r in rows if r.day < first]) if before > 0 else None
        return {
            "window": {"days": days, "since": window_start, "until": now},
            "operations": self._operations(this, window_start, now),
            "quality": self._quality(this),
            "previous": self._rates(self._quality(previous)) if previous is not None else self._rates(None),
            "by_model": this.breakdown("model"),
            "by_prompt_version": this.breakdown("prompt_version"),
            "by_model_prompt": this.breakdown("model", "prompt_version"),
            "ab": this.ab(),
            "daily": this.daily(first, days),
            "corrections": [
                {"series_id": c.series_id, "lang": c.lang, "edits": c.edits} for c in self.store.cue_edit_counts()
            ],
        }

    @staticmethod
    def _rates(quality: dict[str, Any] | None) -> dict[str, float | None]:
        """The five rates of Quality, to put next to the window before: ``None`` for each when there is no such window."""
        if quality is None:
            return dict.fromkeys(("cue_edit_rate", "repair_rate", "retranslate_rate", "cps_rate", "too_long_rate"))
        return {
            "cue_edit_rate": quality["cue_edits"]["rate"], "repair_rate": quality["repair"]["rate"],
            "retranslate_rate": quality["retranslations"]["rate"], "cps_rate": quality["review"]["cps_rate"],
            "too_long_rate": quality["review"]["too_long_rate"],
        }

    def _operations(self, f: "_Figures", since: float, now: float) -> dict[str, Any]:
        calls = f.total("calls")
        by_outcome = f.group("calls", "outcome")
        limited = by_outcome.get("rate_limited", 0) + by_outcome.get("quota_exhausted", 0)
        failed = sum(v for k, v in by_outcome.items() if k != "ok")
        episodes = f.total("episodes")
        translate = {"task": "translate"}
        rpm, tpm, rpd = self._limits()
        daily_cap = int(self._settings().get("max_calls_per_day") or 0)
        today_start = next_utc_midnight(now) - DAY
        snapshot = self.console.snapshot(limit=1) if self.console is not None else {}
        layers = self.console.layer_report() if self.console is not None else {}
        waits = self.store.job_waits(since, JOB_KINDS)
        ended = self.store.job_outcomes(since, JOB_KINDS)
        latencies = [float(v) for v in self.store.latencies(since)]
        escalated = (layers.get("escalated") or {})
        finished = sum(v.get("done", 0) + v.get("failed", 0) for v in (layers.get("by_model") or {}).values())
        counts = snapshot.get("counts") or {}
        return {
            "queue": {"counts": counts, "depth": sum(counts.get(k, 0) for k in ("pending", "processing", "deferred"))},
            "providers": snapshot.get("providers") or [],
            "daily_quota_pause": snapshot.get("daily_quota_pause"),
            "wait_seconds": {
                "jobs": len(waits), "median": _round(_percentile(waits, 0.5)), "p90": _round(_percentile(waits, 0.9)),
            },
            "usage": {
                "calls_today": self.store.calls_since(today_start), "calls_last_minute": self.store.calls_since(now - 60),
                "limits": {"rpm": rpm, "tpm": tpm, "rpd": rpd, "max_calls_per_day": daily_cap},
            },
            "calls": {
                "total": int(calls), "by_outcome": {k: int(v) for k, v in sorted(by_outcome.items())},
                "failed": int(failed), "rate_429": _rate(limited, calls), "rate_503": _rate(by_outcome.get("unavailable", 0), calls),
            },
            "escalation": {
                "jobs": finished, "escalated": int(escalated.get("total", 0)), "rescued": int(escalated.get("done", 0)),
                "rate": _rate(escalated.get("total", 0), finished), "rescue_rate": _rate(escalated.get("done", 0), escalated.get("total", 0)),
                "window_days": (layers.get("window") or {}).get("done_days"),
            },
            "per_episode": {
                "episodes": int(episodes),
                "tokens_in": _round(_safe(f.total("tokens_in", **translate), episodes)),
                "tokens_out": _round(_safe(f.total("tokens_out", **translate), episodes)),
                "cost": _round(_safe(f.total("cost", **translate), episodes), 4),
            },
            "tokens": {"in": int(f.total("tokens_in")), "out": int(f.total("tokens_out")), "cost": round(f.total("cost"), 4)},
            "calls_by_task": {k: int(v) for k, v in sorted(f.group("calls", "task").items())},
            "research_cost": round(f.total("cost", task="research"), 4),
            "quota_pauses": int(f.total("quota_pauses")),
            "breaker_opens": int(f.total("breaker_opens")),
            "jobs": {
                "done": ended.done, "failed": ended.failed, "success_rate": _rate(ended.done, ended.done + ended.failed),
                "retries_per_episode": _round(_safe(f.total("retries"), episodes), 2),
            },
            "latency_ms": {
                "calls": len(latencies), "mean": _round(sum(latencies) / len(latencies)) if latencies else None,
                "median": _round(_percentile(latencies, 0.5)), "p90": _round(_percentile(latencies, 0.9)),
            },
        }

    def _quality(self, f: "_Figures") -> dict[str, Any]:
        cues, episodes = f.total("cues_translated"), f.total("episodes")
        by_kind = f.group("calls", "kind", task="translate")
        repair_calls = sum(by_kind.get(k, 0) for k in REPAIR_KINDS)
        review = f.group("review", "kind")
        edits = f.total("cue_edits")
        retranslated = f.total("retranslations")
        return {
            "cues_translated": int(cues), "episodes": int(episodes),
            "repair": {
                "calls": int(repair_calls), "translate_calls": int(by_kind.get("translate", 0)),
                "rate": _rate(repair_calls, by_kind.get("translate", 0)),
                "cues": int(f.total("repair_cues")), "cue_rate": _rate(f.total("repair_cues"), cues),
            },
            "cue_edits": {"cues": int(edits), "rate": _rate(edits, cues)},
            "retranslations": {
                "count": int(retranslated), "rate": _rate(retranslated, episodes),
                "reverts": int(f.total("reverts")),
            },
            "review": {
                "by_kind": {k: int(v) for k, v in sorted(review.items())},
                "cps": int(review.get("cps", 0)), "cps_rate": _rate(review.get("cps", 0), cues),
                "too_long": int(review.get("line_length", 0)), "too_long_rate": _rate(review.get("line_length", 0), cues),
            },
            "validate": {k: int(v) for k, v in sorted(f.group("validate", "issue").items())},
        }


def _count(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _safe(part: float, whole: float) -> float | None:
    return part / whole if whole > 0 else None


class _Figures:
    """The rows of ``metrics_daily`` of one window, added up by name and label."""

    def __init__(self, rows: list[MetricRow]) -> None:
        self.rows = rows

    def _matching(self, name: str, filters: dict[str, str]) -> Iterable[MetricRow]:
        for row in self.rows:
            if row.name == name and all(dict(row.labels).get(k) == v for k, v in filters.items()):
                yield row

    def total(self, name: str, **filters: str) -> float:
        return sum(row.value for row in self._matching(name, filters))

    def group(self, name: str, label: str, **filters: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in self._matching(name, filters):
            key = dict(row.labels).get(label, "")
            out[key] = out.get(key, 0.0) + row.value
        return out

    def breakdown(self, *labels: str) -> list[dict[str, Any]]:
        """One line per model, per prompt version, or per model and prompt version together: what it did and how much of it
        the admin had to correct."""
        keys = sorted({
            tuple(dict(r.labels).get(label, "") for label in labels)
            for r in self.rows if all(label in dict(r.labels) for label in labels)
        })
        out = []
        for key in keys:
            only = dict(zip(labels, key))
            cues, episodes = self.total("cues_translated", **only), self.total("episodes", **only)
            translate_calls = self.total("calls", kind="translate", task="translate", **only)
            repair_calls = sum(self.total("calls", kind=k, task="translate", **only) for k in REPAIR_KINDS)
            edits = self.total("cue_edits", **only)
            review = self.group("review", "kind", **only)
            # A retranslation names the model that wrote the version it replaces, not a prompt version.
            retranslations = self.total("retranslations", model=key[0]) if labels == ("model",) else None
            out.append({
                **only, "calls": int(self.total("calls", **only)), "episodes": int(episodes), "cues": int(cues),
                "tokens_in": int(self.total("tokens_in", **only)), "tokens_out": int(self.total("tokens_out", **only)),
                "cost": round(self.total("cost", **only), 4),
                "repair_rate": _rate(repair_calls, translate_calls),
                "cue_edits": int(edits), "cue_edit_rate": _rate(edits, cues),
                "cps_rate": _rate(review.get("cps", 0), cues), "too_long_rate": _rate(review.get("line_length", 0), cues),
                "retranslations": None if retranslations is None else int(retranslations),
            })
        return out

    def ab(self) -> list[dict[str, Any]]:
        """The verdicts on variants, added up by style and prompt version: a win counts 1, a tie a half."""
        tally: dict[tuple[str, str], dict[str, float]] = {}
        for row in self._matching("ab", {"scope": "cue"}):
            labels = dict(row.labels)
            cell = tally.setdefault((labels.get("style", ""), labels.get("prompt_version", "")), {})
            cell[labels.get("winner", "")] = cell.get(labels.get("winner", ""), 0.0) + row.value
        out = []
        for (style, prompt), cell in sorted(tally.items()):
            variant, current, tie = cell.get("variant", 0.0), cell.get("current", 0.0), cell.get("tie", 0.0)
            judged = variant + current + tie
            out.append({
                "style": style, "prompt_version": prompt, "variant": int(variant), "current": int(current), "tie": int(tie),
                "judged": int(judged), "score": _rate(variant + tie / 2, judged),
            })
        return out

    def daily(self, first_day: str, days: int) -> list[dict[str, Any]]:
        start = datetime.strptime(first_day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        by_day: dict[str, dict[str, float]] = {}
        for row in self.rows:
            cell = by_day.setdefault(row.day, {})
            cell[row.name] = cell.get(row.name, 0.0) + row.value
            if row.name == "calls" and dict(row.labels).get("outcome") != "ok":
                cell["failed"] = cell.get("failed", 0.0) + row.value
        out = []
        for n in range(days):
            day = _day(start + n * DAY)
            cell = by_day.get(day, {})
            out.append({
                "day": day, "episodes": int(cell.get("episodes", 0)), "calls": int(cell.get("calls", 0)),
                "failed": int(cell.get("failed", 0)),
                "tokens": int(cell.get("tokens_in", 0) + cell.get("tokens_out", 0)), "cost": round(cell.get("cost", 0.0), 4),
                "cues_translated": int(cell.get("cues_translated", 0)), "cue_edits": int(cell.get("cue_edits", 0)),
            })
        return out
