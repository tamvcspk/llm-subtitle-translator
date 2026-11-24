"""What an admin's action on an episode asks the queue for: retranslation, variants, retrying failed jobs and the
earlier episodes a waiting job needs (FR-10, FR-15.11, FR-18.8, UX 6.4).

``RequestsMixin`` is a part of ``UnitService`` (``units.py``). Each method either queues a job or, with
``estimate_only``, says what it would cost and makes nothing.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from subcore import srt

from ..jobs.jobs import (
    EXTRACT, FAILED, JUDGE, READING_KINDS, RESEARCH, RESEARCH_CALLS, SUGGEST_CALLS, VARIANT, job_name, variant_key,
)
from ..domain.ports import Output, Unit
from .memory import unit_label
from ..domain.rules import parse_time_ms


RETRY_ROWS_SHOWN = 200  # like a bulk request: the first rows are named, the rest are counted


class RequestsMixin:
    def request_prerequisites(self, job_id: str, *, estimate_only: bool = False) -> dict[str, Any]:
        """The "request that episode" button of a job that waits for story facts: queue the earlier episodes it
        waits for, those whose source subtitle is on disk. The others cannot be translated yet and are named, so
        the admin knows what is still missing; the job itself goes on waiting (or runs without them after
        ``memory_gap_wait_seconds``, FR-5.4). With ``estimate_only`` nothing is queued: the answer says what asking
        would cost, in the shape of a bulk request's estimate (UX 6.4)."""
        job = self.jobs.jobs.get(job_id)
        if job is None or job.get("wait_reason") != "context":
            raise ValueError("the job is not waiting for earlier episodes")
        lang = str(job.get("target_code") or "")
        language = str(job.get("target_language") or lang)
        requested: list[str] = []
        unavailable: list[str] = []
        planned: list[dict[str, Any]] = []
        for unit_id in job.get("missing_unit_ids") or []:
            unit = self.catalog.get_unit(int(unit_id))
            if unit is None:
                continue
            if not unit.source_path or not Path(unit.source_path).is_file():
                unavailable.append(unit_label(unit))
                continue
            if estimate_only:
                try:
                    source = srt.parse_file(unit.source_path)
                except (OSError, srt.SrtError):
                    unavailable.append(unit_label(unit))
                    continue
                planned.append({"title": unit_label(unit), "lang": lang, "cues": len(source), "calls": self.estimator.calls_for(source, None)})
                continue
            series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
            made = self.jobs.enqueue({
                "provider": "gemini-console", "video_path": unit.video_path, "subtitle_path": unit.source_path,
                "source_code": unit.source_code or str(job.get("source_code") or "en"),
                "source_language": unit.source_language or unit.source_code,
                "media_type": unit.media_type or ("series" if series else "movie"),
                "series_id": series.bazarr_id if series else "", "media_id": unit.bazarr_id, "unit_id": unit.id,
            }, [{"code": lang, "language": language, "enabled": True}])
            requested.extend(made)
        if estimate_only:
            estimate = self.estimator.of(len(planned), sum(r["cues"] for r in planned), sum(r["calls"] for r in planned))
            return {"estimate_only": True, "estimate": asdict(estimate), "planned_total": len(planned), "planned": planned,
                    "skipped_total": len(unavailable), "skipped_by_reason": {"no source subtitle": len(unavailable)} if unavailable else {},
                    "unavailable": unavailable}
        self.telemetry.event(
            "job.prerequisites_requested", job_id=job_id, reason="the admin asked for the earlier episodes the job waits for",
            requested=requested, unavailable=unavailable,
        )
        return {"requested": requested, "unavailable": unavailable}

    # -- retranslation ------------------------------------------------------------------
    def retranslate(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        unit = self.unit(unit_id)
        out = self.output(unit, body.get("lang"))
        lang = out.target_lang
        source = self._source(unit)  # also proves the source is still there
        window = self._range(body.get("range"), source)
        if window and not Path(out.output_path).is_file():
            raise ValueError("there is no target subtitle yet; retranslate the whole episode")

        estimate_only = body.get("estimate_only") is True  # says what it would cost; makes no job and no correction
        created: list[int] = []
        for new in body.get("new_corrections") or []:
            if not isinstance(new, dict):
                raise ValueError("new_corrections must be a list of corrections")
            if not estimate_only:
                created.extend(self.add_correction(unit_id, {**new, "lang": lang}).get("ids", []))

        ids = body.get("correction_ids")
        if ids is not None:
            valid = {c.id for c in self.catalog.corrections(unit_id, lang)}
            wanted = [int(i) for i in ids] + created
            unknown = [i for i in wanted if i not in valid]
            if unknown:
                raise ValueError(f"unknown or inactive correction(s): {', '.join(map(str, unknown))}")
            ids = sorted(set(wanted))

        job = {**self._job_payload(unit, out), "retranslate": {"range": window, "correction_ids": ids}}
        if estimate_only:
            blocker = self.jobs.retranslation_blocker(job)
            if blocker:
                raise ValueError(blocker)
            ordinals = list(range(window["first"], window["last"] + 1)) if window else None
            cues = len(ordinals) if ordinals else len(source)
            return {
                "ok": True, "estimate_only": True, "range": window, "correction_ids": ids,
                "estimate": asdict(self.estimator.of(1, cues, self.estimator.calls_for(source, ordinals))),
            }
        job_id = self.jobs.enqueue_retranslation(job)
        # An admin asking for a retranslation says the current translation was not good enough (FR-19.4).
        current = self.version_repo.current_version(out.output_path)
        self.telemetry.signal(
            "retranslate", unit_id=unit.id, version=current.version if current else None,
            cue=window["first"] if window else None, lang=lang, range=window, correction_ids=ids,
            job_id=job_id, whole_episode=window is None, model=current.model if current else "",
        )
        return {"ok": True, "job_id": job_id, "range": window, "correction_ids": ids}

    def _job_payload(self, unit: Unit, out: Output) -> dict[str, Any]:
        """The job an admin's action on one episode makes: where its files are and what to translate into."""
        series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        return {
            "provider": "gemini-console",
            "video_path": unit.video_path, "subtitle_path": unit.source_path,
            "output_path": out.output_path, "source_code": unit.source_code,
            "source_language": unit.source_language or unit.source_code,
            "target_code": out.target_lang, "target_language": out.target_language or out.target_lang,
            "media_type": unit.media_type or ("series" if series else "movie"),
            "series_id": series.bazarr_id if series else "", "media_id": unit.bazarr_id,
            "unit_id": unit.id,
        }

    # -- variants and A/B (FR-18.8) -------------------------------------------------------
    def variant(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Queue the whole episode translated with another style, to be kept as a candidate and compared with the
        current translation. Nothing goes on disk; the style is checked now, not when the job runs."""
        unit = self.unit(unit_id)
        out = self.output(unit, body.get("lang"))
        source = self._source(unit)  # also proves the source is still there
        style = body.get("style") or {}
        if not isinstance(style, dict):
            raise ValueError("style must be an object")
        preset = str(body.get("preset") or "")
        styles = self.library.styles
        if preset and (styles is None or styles.preset(preset) is None):
            raise LookupError(f"no preset {preset!r}")
        variant = {"label": str(body.get("label") or "").strip()[:80] or "variant", "preset": preset, "style": style}
        self.library.variant_layers(unit, variant)  # raises ValueError naming the bad field
        job = {**self._job_payload(unit, out), "variant": variant}
        if body.get("estimate_only") is True:  # the whole episode is translated again: its cost, nothing queued
            key = variant_key(job["subtitle_path"], job["output_path"], job["target_code"])
            if self.jobs.jobs.open_for(key, VARIANT) is not None:
                raise ValueError("A variant of this episode is already queued")
            return {
                "ok": True, "estimate_only": True, "label": variant["label"],
                "estimate": asdict(self.estimator.of(1, len(source), self.estimator.calls_for(source))),
            }
        job_id = self.jobs.enqueue_variant(job)
        return {"ok": True, "job_id": job_id, "label": variant["label"]}

    def _retry_ids(self, body: dict[str, Any]) -> list[str]:
        """The jobs a retry names: exactly one of ``job_id``, ``job_ids`` or ``all`` (every failed job)."""
        given = [body.get("job_id") not in (None, ""), body.get("job_ids") is not None, body.get("all") is True]
        if sum(given) != 1:
            raise ValueError("Name the jobs with exactly one of job_id, job_ids or all")
        if body.get("all") is True:
            return [str(job["job_id"]) for job in self.jobs.jobs.by_state(FAILED)]
        if body.get("job_ids") is not None:
            ids = body["job_ids"]
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("job_ids must be a list of job ids")
            return ids
        return [str(body["job_id"])]

    def retry_failed(self, body: dict[str, Any]) -> dict[str, Any]:
        """Retry All Failed, or a list of failed jobs: put each back in line with a fresh retry cycle. A job that is not
        failed, or whose file already has an open job, is left alone and counted under ``skipped``."""
        ids = list(dict.fromkeys(self._retry_ids(body)))
        retried = sum(1 for job_id in ids if self.jobs.retry(job_id))
        return {"ok": retried > 0 or not ids, "count": retried, "skipped": len(ids) - retried}

    def estimate_retry(self, body: dict[str, Any]) -> dict[str, Any]:
        """The cost of putting failed jobs back in line, before the admin confirms. Name them with exactly one of
        ``job_id``, ``job_ids`` or ``all`` (every failed job: Retry All Failed). A job that is not failed, or whose
        file already has an open job, is listed under ``skipped`` with the reason, as ``retry`` would do nothing for it.
        A retranslation retried over a range costs that range only."""
        store = self.jobs.jobs
        ids = self._retry_ids(body)
        planned: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for job_id in dict.fromkeys(ids):
            job = store.get(job_id)
            row: dict[str, Any] = {"job_id": job_id, "title": "", "lang": "", "cues": 0, "calls": 0, "skip": "", "reason": ""}
            if job is not None:
                row.update(title=job_name(job), lang=str(job.get("target_code") or ""))
            reason = (
                "no such job" if job is None else "the job is not failed" if job["state"] != FAILED
                else "already requested" if store.has_open_twin(job_id) else ""
            )
            cues = calls = 0
            if not reason and job is not None and job.get("kind") in READING_KINDS:
                # a job that reads has no subtitle to count cues in: its calls are fixed, or a judge job says how many it needs
                kind = job.get("kind")
                calls = (
                    1 if kind == EXTRACT else max(1, int(job.get("calls") or 0)) if kind == JUDGE
                    else RESEARCH_CALLS if kind == RESEARCH else SUGGEST_CALLS
                )
            elif not reason and job is not None:
                try:
                    source = srt.parse_file(str(job.get("subtitle_path") or ""))
                except (OSError, srt.SrtError):
                    reason = "the source subtitle cannot be read"
                else:
                    window = (job.get("retranslate") or {}).get("range") if isinstance(job.get("retranslate"), dict) else None
                    ordinals = None
                    if isinstance(window, dict) and 1 <= int(window["first"]) <= int(window["last"]) <= len(source):
                        ordinals = list(range(int(window["first"]), int(window["last"]) + 1))
                    cues = len(ordinals) if ordinals else len(source)
                    calls = self.estimator.calls_for(source, ordinals)
            if reason:
                skipped.append({**row, "skip": reason, "reason": reason})
            else:
                planned.append({**row, "cues": cues, "calls": calls})
        estimate = self.estimator.of(len(planned), sum(r["cues"] for r in planned), sum(r["calls"] for r in planned))
        counts: dict[str, int] = {}
        for row in skipped:
            counts[row["reason"]] = counts.get(row["reason"], 0) + 1
        return {
            "estimate_only": True, "estimate": asdict(estimate),
            "planned_total": len(planned), "planned": planned[:RETRY_ROWS_SHOWN],
            "skipped_total": len(skipped), "skipped_by_reason": counts, "skipped": skipped[:RETRY_ROWS_SHOWN],
        }

    @staticmethod
    def _range(spec: Any, source: list[srt.Cue]) -> dict[str, int] | None:
        if not spec:
            return None
        if not isinstance(spec, dict):
            raise ValueError("range must be {\"cues\": [first, last]} or {\"time\": [start, end]}")
        if spec.get("cues"):
            try:
                first, last = (int(v) for v in spec["cues"])
            except (TypeError, ValueError):
                raise ValueError("range.cues must be [first, last]") from None
        elif spec.get("time"):
            try:
                begin, end = (parse_time_ms(v) for v in spec["time"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"range.time: {exc}") from None
            if end < begin:
                raise ValueError("range.time: the end is before the start")
            hit = [n for n, c in enumerate(source, start=1) if c.end_ms >= begin and c.start_ms <= end]
            if not hit:
                raise ValueError("range.time: no cue falls inside that time range")
            first, last = hit[0], hit[-1]
        else:
            return None
        if not 1 <= first <= last <= len(source):
            raise ValueError(f"range.cues must satisfy 1 <= first <= last <= {len(source)}")
        return {"first": first, "last": last}
