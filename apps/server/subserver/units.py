"""Actions on a library unit: view cues, correct, retranslate, inspect and revert versions (FR-10, FR-11).

``UnitService`` is what the HTTP layer calls (like ``ConsoleActions`` for the queue); it owns no
HTTP details. ``LookupError`` means "not found" and ``ValueError`` means "bad request".
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from subcore import glossary as g
from subcore import srt
from subcore.style import Style, StyleError
from subcore.translate.prompt import PROMPT_VERSION as SUBCORE_PROMPT_VERSION

from .catalog import pick_source
from .estimates import Estimator
from .glossaries import Glossaries
from .jobs import EXTRACT, FAILED, READING_KINDS, SUGGEST_CALLS, VARIANT, job_name, variant_key
from .library import Library, Owner
from .ports import CURRENT, KIND_EPISODE, KIND_SERIES, PRUNED, Correction, Output, Telemetry, Unit
from .store import Store
from .queue import JobQueue
from .memory import MEMORY_MODES, StoryMemory, parse_anchor, unit_label
from .rules import correction_position
from .telemetry import Telemetry as TelemetrySink
from .versions import CUE_EDIT, TranslationVersions

KINDS = ("term", "address", "note", "cue_edit")
SCOPES = ("episode", "forward", "series")
MAX_CUE_PAGE = 5000
RETRY_ROWS_SHOWN = 200  # like a bulk request: the first rows are named, the rest are counted

_TIME = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?\s*$")


def parse_time_ms(text: str) -> int:
    """``00:23:10``, ``23:10`` or ``1:02:03.5`` as read off a player, in milliseconds."""
    match = _TIME.match(str(text))
    if not match:
        raise ValueError(f"expected a time like 00:23:10, got {text!r}")
    hours, minutes, seconds, frac = match.groups()
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int((frac or "0").ljust(3, "0"))


def format_ms(ms: int) -> str:
    seconds, milli = divmod(max(0, int(ms)), 1000)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _text(payload: dict[str, Any], key: str, what: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what}: '{key}' is required")
    return value.strip()


def _correction_json(c: Correction) -> dict[str, Any]:
    """A correction as the HTTP API shows it."""
    return {
        "id": c.id, "unit_id": c.unit_id, "target_lang": c.target_lang, "kind": c.kind,
        "created_at": c.created_at, "active": c.active, "payload": c.payload,
    }


class UnitService:
    def __init__(
        self,
        library: Library,
        glossaries: Glossaries,
        versions: TranslationVersions,
        jobs: JobQueue,
        refresh_bazarr: Callable[[dict[str, Any]], None] | None = None,
        memory: StoryMemory | None = None,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        usage: Store | None = None,
        telemetry: Telemetry | None = None,
        estimator: Estimator | None = None,
        source_languages: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.telemetry: Telemetry = telemetry or TelemetrySink(usage=usage, synchronous=True)
        self._source_languages = source_languages  # the enabled source languages, to pick a source subtitle from the catalog
        self.library = library
        self.catalog = library.catalog
        self.version_repo = versions.repo
        self.usage = usage
        self.glossaries = glossaries
        self.versions = versions
        self.jobs = jobs
        self._refresh = refresh_bazarr
        self.memory = memory
        self._settings = settings_source or (lambda: {})
        self.estimator = estimator or Estimator(jobs.jobs.store, jobs, self._settings)

    # -- lookups -----------------------------------------------------------------
    def unit(self, unit_id: int) -> Unit:
        unit = self.catalog.get_unit(unit_id)
        if unit is None:
            raise LookupError(f"no unit {unit_id}")
        return unit

    def output(self, unit: Unit, lang: str | None) -> Output:
        outputs = self.catalog.outputs(unit.id)
        if lang:
            for out in outputs:
                if out.target_lang == lang:
                    return out
            raise LookupError(f"this unit has no '{lang}' target")
        if len(outputs) == 1:
            return outputs[0]
        raise ValueError("lang is required: this unit has " + ("no targets" if not outputs else "several targets"))

    def _source(self, unit: Unit) -> list[srt.Cue]:
        return self.library.source_cues(unit)

    def _target(self, out: Output) -> list[srt.Cue]:
        try:
            return srt.parse_file(out.output_path)
        except OSError:
            raise LookupError("the target subtitle is not on disk") from None
        except srt.SrtError as exc:
            raise ValueError(f"the target subtitle cannot be read: {exc}") from None

    REFRESH_ATTEMPTS = 3
    REFRESH_DELAY_SECONDS = 5.0

    def _bazarr_refresh(self, unit: Unit) -> None:
        """Ask Bazarr to rescan the unit in the background (FR-16 `refresh`): the file is already in place, so a
        slow or failing Bazarr neither delays the console nor changes any job's result."""
        if self._refresh is None:
            return
        series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        payload = {
            "media_type": unit.media_type or ("series" if series else "movie"),
            "series_id": series.bazarr_id if series else "",
            "media_id": unit.bazarr_id,
        }
        threading.Thread(target=self._refresh_with_retry, args=(payload,), name="bazarr-refresh", daemon=True).start()

    def _refresh_with_retry(self, payload: dict[str, Any]) -> bool:
        for attempt in range(1, self.REFRESH_ATTEMPTS + 1):
            try:
                self._refresh(payload)  # type: ignore[misc] - checked by _bazarr_refresh
                return True
            except Exception as exc:  # noqa: BLE001 - Bazarr rescans on its own schedule if this never lands
                logging.warning(
                    "Bazarr scan after a subtitle change failed (%s), attempt %d of %d",
                    type(exc).__name__, attempt, self.REFRESH_ATTEMPTS,
                )
                if attempt < self.REFRESH_ATTEMPTS:
                    time.sleep(self.REFRESH_DELAY_SECONDS * attempt)
        return False

    # -- detail ----------------------------------------------------------------------
    def detail(self, unit_id: int) -> dict[str, Any]:
        unit = self.unit(unit_id)
        summary = self.library.unit_summary(unit)
        series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        return {
            **summary,
            "series": {"id": series.id, "title": series.title} if series else None,
            "source_exists": bool(unit.source_path) and Path(unit.source_path).is_file(),
            "memory_applicable": series is not None and self.memory is not None,
        }

    def cues(
        self,
        unit_id: int,
        lang: str | None = None,
        *,
        at: str | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = MAX_CUE_PAGE,
    ) -> dict[str, Any]:
        """Source and translation side by side; ``at`` jumps to a player time, ``q`` searches both texts."""
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        source = self._source(unit)
        try:
            target = self._target(out)
        except LookupError:
            target = []
        locked = {
            int(c.payload["cue"]) for c in self.catalog.corrections(unit_id, out.target_lang) if c.kind == "cue_edit"
        }
        rows = [
            {
                "n": n, "index": cue.index, "start": format_ms(cue.start_ms), "start_ms": cue.start_ms,
                "end_ms": cue.end_ms, "source": cue.text,
                "target": target[n - 1].text if n <= len(target) else None,
                "locked": n in locked,
            }
            for n, cue in enumerate(source, start=1)
        ]
        focus = None
        if at:
            ms = parse_time_ms(at)
            inside = [r for r in rows if r["start_ms"] <= ms <= r["end_ms"]]
            nearest = inside[0] if inside else min(rows, key=lambda r: abs(r["start_ms"] - ms), default=None)
            focus = nearest["n"] if nearest else None
        if q and q.strip():
            needle = q.strip().casefold()
            rows = [r for r in rows if needle in r["source"].casefold() or needle in (r["target"] or "").casefold()]
        total = len(rows)
        limit = max(1, min(int(limit), MAX_CUE_PAGE))
        if focus is not None and not (q and q.strip()):
            offset = max(0, focus - 1 - limit // 2)
        offset = max(0, int(offset))
        return {
            "lang": out.target_lang, "total": total, "offset": offset, "focus": focus,
            "aligned": len(target) == len(source), "has_target": bool(target),
            "rows": rows[offset : offset + limit],
        }

    # -- corrections --------------------------------------------------------------------
    def list_corrections(self, unit_id: int, lang: str | None = None) -> list[dict[str, Any]]:
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        return [_correction_json(c) for c in self.catalog.corrections(unit_id, out.target_lang)]

    def remove_correction(self, unit_id: int, correction_id: int) -> bool:
        correction = self.catalog.get_correction(correction_id)
        if correction is None or correction.unit_id != unit_id:
            raise LookupError(f"no correction {correction_id} for this unit")
        return self.catalog.deactivate_correction(correction_id)

    def add_correction(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Record a correction. ``cue_edit`` publishes a new version at once; the other kinds act on the next retranslation."""
        unit = self.unit(unit_id)
        out = self.output(unit, body.get("lang"))
        lang = out.target_lang
        kind = str(body.get("kind") or "")
        scope = str(body.get("scope") or "episode")
        payload = body.get("payload")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        if kind == "cue_edit":
            if scope != "episode":
                raise ValueError("a cue edit applies to this episode only")
            return self._cue_edits(unit, out, payload)
        if kind == "note" and scope != "episode":
            raise ValueError("a note applies to one episode; use a term or an address rule for a wider scope")
        clean = self._validate_payload(kind, payload)
        if scope == "episode":
            correction_id = self.catalog.add_correction(unit_id, lang, kind, clean)
            return {"ok": True, "scope": scope, "ids": [correction_id]}
        return self._glossary_correction(unit, lang, kind, scope, clean)

    @staticmethod
    def _validate_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        what = f"{kind} correction"
        if kind == "term":
            clean = {"source": _text(payload, "source", what), "target": _text(payload, "target", what),
                     "locked": bool(payload.get("locked", True))}
            if payload.get("note"):
                clean["note"] = str(payload["note"]).strip()
            aliases = payload.get("aliases") or []
            if aliases:
                clean["aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
            return clean
        if kind == "address":
            clean = {"a": _text(payload, "a", what), "b": _text(payload, "b", what)}
            for key in ("a_to_b", "b_to_a", "note"):
                if str(payload.get(key) or "").strip():
                    clean[key] = str(payload[key]).strip()
            if not (clean.get("a_to_b") or clean.get("b_to_a")):
                raise ValueError(f"{what}: give 'a_to_b' and/or 'b_to_a'")
            if payload.get("from_cue") not in (None, ""):
                clean["from_cue"] = int(payload["from_cue"])
            return clean
        return {"text": _text(payload, "text", what)}

    def _glossary_correction(self, unit: Unit, lang: str, kind: str, scope: str, clean: dict[str, Any]) -> dict[str, Any]:
        """``forward``/``series`` corrections live in the series' glossary (FR-11.2.3)."""
        gkind, key = self.library.owner(unit)
        glossary = self.glossaries.load(gkind, key, lang)
        forward = scope == "forward" and unit.series_id is not None
        cue = None
        if kind == "address" and clean.get("from_cue") and forward:
            source = self._source(unit)
            ordinal = int(clean["from_cue"])
            if not 1 <= ordinal <= len(source):
                raise ValueError(f"from_cue must be between 1 and {len(source)}")
            cue = source[ordinal - 1]
        start = correction_position(unit.season, unit.episode, cue) if forward else None
        if kind == "term":
            glossary = glossary.with_term(g.Term(
                source=clean["source"], target=clean["target"], aliases=tuple(clean.get("aliases", ())),
                locked=clean["locked"], note=clean.get("note", ""), start=start,
            ))
        else:
            glossary = glossary.with_address_rule(g.AddressRule(
                a=clean["a"], b=clean["b"], a_to_b=clean.get("a_to_b", ""), b_to_a=clean.get("b_to_a", ""),
                start=start, note=clean.get("note", ""),
            ))
        impact = None
        if unit.series_id is not None:
            impact = self.library.glossary_impact(unit.series_id, lang, glossary)
        self.glossaries.save(gkind, key, lang, glossary, note="correction")
        return {"ok": True, "scope": scope, "ids": [], "impact": impact,
                "from": str(start) if start else None}

    def _cue_edits(self, unit: Unit, out: Output, payload: dict[str, Any]) -> dict[str, Any]:
        edits = payload.get("edits") if isinstance(payload.get("edits"), list) else [payload]
        source, target = self._source(unit), self._target(out)
        if len(target) != len(source):
            raise ValueError("the target has a different number of cues than the source; retranslate the episode first")
        lang, output_path = out.target_lang, out.output_path
        parsed: dict[int, str] = {}
        warnings: list[str] = []
        for edit in edits:
            if not isinstance(edit, dict) or not isinstance(edit.get("text"), str):
                raise ValueError("cue edit: 'cue' and 'text' are required")
            try:
                ordinal = int(edit.get("cue"))
            except (TypeError, ValueError):
                raise ValueError("cue edit: 'cue' must be a cue number") from None
            if not 1 <= ordinal <= len(source):
                raise ValueError(f"cue edit: cue must be between 1 and {len(source)}")
            text = edit["text"].replace("\r\n", "\n").strip("\n")
            if source[ordinal - 1].text.strip() and not text.strip():
                raise ValueError(f"cue {ordinal}: the translation cannot be empty")
            for problem in srt.tag_problems(source[ordinal - 1].text, text):
                warnings.append(f"cue {ordinal}: {problem}")
            parsed[ordinal] = text
        edited = [cue.with_text(parsed.get(n, cue.text)) for n, cue in enumerate(target, start=1)]
        current = self.version_repo.current_version(output_path)
        self.versions.coalesce_seconds = float(self._settings().get("edit_coalesce_minutes", 10)) * 60
        published = self.versions.publish(
            output_path, srt.serialize(edited), trigger=CUE_EDIT,
            source_path=unit.source_path, source_sha256=current.source_sha256 if current else "",
            target_lang=lang, context_hash=current.context_hash if current else "",
            model=current.model if current else "", unit_id=unit.id,
            glossary_hash=current.glossary_hash if current else "",
        )
        ids = []
        for ordinal, text in sorted(parsed.items()):
            for old in self.catalog.corrections(unit.id, lang):
                if old.kind == "cue_edit" and int(old.payload["cue"]) == ordinal:
                    self.catalog.deactivate_correction(old.id)
            ids.append(self.catalog.add_correction(
                unit.id, lang, "cue_edit",
                {"cue": ordinal, "text": text, "source_text": source[ordinal - 1].text},
            ))
            # What the admin changed in a cue is the clearest sign of what the machine got wrong (FR-19.4).
            self.telemetry.signal(
                "cue_edit", unit_id=unit.id, version=published.version, cue=ordinal,
                before=target[ordinal - 1].text, after=text, lang=lang, source=source[ordinal - 1].text,
                model=current.model if current else "", context_hash=current.context_hash if current else "",
            )
        self.telemetry.event(
            "version.cue_edit", unit_id=unit.id, reason=f"{len(parsed)} cue(s) edited by the admin",
            target=Path(output_path).name, version=published.version, cues=sorted(parsed),
        )
        self._bazarr_refresh(unit)
        return {"ok": True, "scope": "episode", "ids": ids, "version": published.version, "warnings": warnings}

    # -- a job waiting for earlier episodes (FR-15.11) ------------------------------------
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

    # -- what Retry would cost (UX 6.4) ---------------------------------------------------
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
                calls = 1 if job.get("kind") == EXTRACT else SUGGEST_CALLS  # a job that reads has no subtitle to count cues in
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

    def compare(self, unit_id: int, version: int, lang: str | None = None) -> dict[str, Any]:
        """The current translation and a candidate side by side, cue by cue (FR-18.8)."""
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        row = self.version_repo.get_version(out.output_path, version)
        if row is None:
            raise LookupError(f"no version {version}")
        source = self._source(unit)
        other = self._version_cues(out, version)
        try:
            current = self._target(out)
        except LookupError:
            current = []
        rows = [
            {
                "n": n, "source": cue.text, "current": current[n - 1].text if n <= len(current) else None,
                "variant": other[n - 1].text if n <= len(other) else None,
            }
            for n, cue in enumerate(source, start=1)
        ]
        for item in rows:
            item["differs"] = item["current"] != item["variant"]
        return {
            "lang": out.target_lang, "version": version, "status": row.status, "params": row.params, "model": row.model,
            "aligned": len(other) == len(source) and len(current) == len(source),
            "differing": sum(1 for r in rows if r["differs"]), "rows": rows,
        }

    def choose(self, unit_id: int, version: int, body: dict[str, Any]) -> dict[str, Any]:
        """The admin's verdict on a variant (FR-18.8): which text is better for some cues or for the whole episode.

        Every verdict is a quality signal (``ab``), so scores add up by style and prompt version (FR-19.5). With
        ``apply`` the verdict is acted on: the chosen variant cues become cue edits of the current translation, and a
        whole-episode win adopts the candidate."""
        unit = self.unit(unit_id)
        out = self.output(unit, body.get("lang"))
        picks = body.get("picks") or {}
        if not isinstance(picks, dict):
            raise ValueError("picks must map a cue number to 'variant' or 'current'")
        whole = body.get("whole")
        if whole not in (None, "variant", "current", "tie"):
            raise ValueError("whole must be 'variant', 'current' or 'tie'")
        view = self.compare(unit_id, version, out.target_lang)
        if view["status"] != "candidate":
            raise ValueError(f"version {version} is {view['status']}, not a candidate to judge")
        by_cue = {r["n"]: r for r in view["rows"]}
        chosen: dict[int, str] = {}
        for key, winner in picks.items():
            try:
                n = int(key)
            except (TypeError, ValueError):
                raise ValueError(f"cue {key!r} is not a number") from None
            if n not in by_cue:
                raise ValueError(f"cue {n} is not in this episode")
            if winner not in ("variant", "current", "tie"):
                raise ValueError(f"cue {n}: the winner must be 'variant', 'current' or 'tie'")
            chosen[n] = winner
        params = view["params"].get("variant") or {}
        meta = dict(
            lang=out.target_lang, variant_version=version, label=params.get("label"), preset=params.get("preset"),
            style=params.get("style"), model=view["model"], prompt_version=SUBCORE_PROMPT_VERSION,
        )
        for n, winner in sorted(chosen.items()):
            self.telemetry.signal(
                "ab", unit_id=unit.id, cue=n, before=by_cue[n]["current"] or "", after=by_cue[n]["variant"] or "",
                winner=winner, **meta,
            )
        if whole:
            self.telemetry.signal("ab", unit_id=unit.id, winner=whole, whole_episode=True, **meta)
        applied: dict[str, Any] = {}
        if body.get("apply"):
            edits = [{"cue": n, "text": by_cue[n]["variant"]} for n, w in sorted(chosen.items())
                     if w == "variant" and by_cue[n]["variant"] and by_cue[n]["differs"]]
            if whole == "variant":
                applied = self.adopt(unit_id, version, out.target_lang, apply_style=bool(body.get("apply_style")))
                applied = {"adopted": applied["adopted"], **({"style": applied["style"]} if "style" in applied else {})}
            elif edits:
                result = self.add_correction(unit_id, {
                    "lang": out.target_lang, "kind": "cue_edit", "payload": {"edits": edits},
                })
                applied = {"edited_cues": [e["cue"] for e in edits], "version": result.get("version")}
        return {"ok": True, "recorded": len(chosen) + (1 if whole else 0), "applied": applied}

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

    # -- versions -------------------------------------------------------------------------
    def list_versions(self, unit_id: int, lang: str | None = None) -> dict[str, Any]:
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        rows = self.version_repo.versions(out.output_path)
        on_disk = Path(out.output_path).is_file()
        return {
            "lang": out.target_lang,
            "modified_outside": self.library.target_info(unit, out)["modified_outside"],
            "versions": [
                {
                    "version": r.version, "trigger": r.trigger, "origin": r.origin, "status": r.status,
                    "parent_version": r.parent_version, "model": r.model, "sha256": r.sha256,
                    "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "created_at": r.created_at,
                    "current": r.status == CURRENT and on_disk, "params": r.params,
                }
                for r in reversed(rows)
            ],
        }

    def adopt(
        self, unit_id: int, version: int, lang: str | None = None, *, apply_style: bool = False, dry_run: bool = False,
    ) -> dict[str, Any]:
        """"Dùng bản này" (FR-16.4): a candidate goes on disk and becomes the current version.

        With ``apply_style`` and a version made by a variant (FR-18.8), the variant's preset and style are also saved
        as the series' (or the movie's) own, in one request; the answer adds ``style`` and how many *other* translations
        that makes stale (this episode is replaced by the variant, so it is not counted). ``dry_run`` only reports that
        and adopts nothing. Nothing is retranslated."""
        if dry_run and not apply_style:
            raise ValueError("dry_run counts what apply_style would flag; add apply_style")
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        if not apply_style:
            published = self.versions.adopt(out.output_path, version)
            self._stamp_adopted_variant(unit, out, version)
            self._bazarr_refresh(unit)
            return {"ok": True, "version": published.version, "adopted": version}
        preset, style = self._variant_style(out, version)
        kind, key = self.library.owner(unit)
        others = [u for u in self._owner_units(unit) if u.id != unit.id]
        impact = self.library.impact_units(
            others, out.target_lang, layers=lambda u: self.library.layers_with(u, preset, style),
        )
        saved_style = {"preset": preset, "style": style.to_doc()}
        if dry_run:
            return {"ok": True, "dry_run": True, "adopted": None, "style": saved_style, **impact}
        published = self.versions.adopt(out.output_path, version)
        self.library.styles.save_owner_style(kind, key, preset, style.to_doc())
        self._stamp_adopted_variant(unit, out, version)  # after the style is saved: the rules are the ones now stored
        self.telemetry.event(
            "style.saved", unit_id=unit.id, preset=preset or None, stale=impact["would_stale"],
            reason="the admin used a variant and kept its style for the whole series; translations made without it are flagged stale, not redone",
        )
        self._bazarr_refresh(unit)
        return {"ok": True, "version": published.version, "adopted": version, "style": saved_style, **impact}

    def _stamp_adopted_variant(self, unit: Unit, out: Output, version: int) -> None:
        """A variant is made with its own style layers (no episode override, its own series style), so it carries a
        rules hash no stored layer has. Once the admin adopts it, the rules in force are the stored ones, and the
        version is stamped with their hash: the choice the admin just made is not shown as stale (R12-8)."""
        row = self.version_repo.get_version(out.output_path, version)
        if row is not None and isinstance(row.params, dict) and row.params.get("variant"):
            self.version_repo.set_version(
                out.output_path, version, glossary_hash=self.library.unit_rules(unit, out.target_lang).hash,
            )

    def _owner_units(self, unit: Unit) -> list[Unit]:
        return self.catalog.list_units(unit.series_id) if unit.series_id is not None else [unit]

    def _variant_style(self, out: Output, version: int) -> tuple[str, Style]:
        """The preset and style a variant version was made with, checked as a style is when it is saved."""
        row = self.version_repo.get_version(out.output_path, version)
        if row is None:
            raise LookupError(f"no version {version}")
        variant = row.params.get("variant") if isinstance(row.params, dict) else None
        if not isinstance(variant, dict):
            raise ValueError(f"version {version} was not made by a variant: there is no style to save")
        preset = str(variant.get("preset") or "")
        if preset and (self.library.styles is None or self.library.styles.preset(preset) is None):
            raise ValueError(f"the preset {preset!r} of this variant no longer exists")
        try:
            return preset, Style.from_doc(variant.get("style") or {}, "variant.style")
        except StyleError as exc:
            raise ValueError(str(exc)) from None

    def remove_output(self, unit_id: int, lang: str | None = None) -> dict[str, Any]:
        """Delete the target subtitle from the console (FR-16.6): the history keeps a removed version and the unit
        is not requested again automatically."""
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        published = self.versions.remove(out.output_path)
        self._bazarr_refresh(unit)
        return {"ok": True, "version": published.version}

    def _version_cues(self, out: Output, version: int) -> list[srt.Cue]:
        row = self.version_repo.get_version(out.output_path, version)
        if row is None:
            raise LookupError(f"no version {version}")
        if row.status == PRUNED:
            raise ValueError(f"version {version} was pruned by the retention settings: its stored copy is gone, so there is nothing to compare")
        try:
            return srt.parse(Path(row.stored_path).read_text(encoding="utf-8-sig"))
        except OSError:
            raise ValueError(f"the stored copy of version {version} is missing") from None

    def diff(self, unit_id: int, version: int, against: int | None = None, lang: str | None = None) -> dict[str, Any]:
        """Cue-level changes from ``against`` (default: the previous version) to ``version``."""
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        if against is None:
            earlier = [r.version for r in self.version_repo.versions(out.output_path) if r.version < version]
            if not earlier:
                raise ValueError(f"version {version} has no earlier version to compare with")
            against = earlier[-1]
        old, new = self._version_cues(out, against), self._version_cues(out, version)

        def row(cues: list[srt.Cue], n: int) -> dict[str, Any]:
            return {"n": n + 1, "index": cues[n].index, "time": format_ms(cues[n].start_ms), "text": cues[n].text}

        key = lambda c: (c.timing, c.text)  # noqa: E731
        matcher = difflib.SequenceMatcher(None, [key(c) for c in old], [key(c) for c in new], autojunk=False)
        changes = [
            {"op": op, "before": [row(old, n) for n in range(a0, a1)], "after": [row(new, n) for n in range(b0, b1)]}
            for op, a0, a1, b0, b1 in matcher.get_opcodes() if op != "equal"
        ]
        return {
            "version": version, "against": against, "lang": out.target_lang,
            "cues_before": len(old), "cues_after": len(new), "changes": changes,
            "changed_cues": sum(max(len(c["before"]), len(c["after"])) for c in changes),
        }

    def revert(self, unit_id: int, version: int, lang: str | None = None) -> dict[str, Any]:
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        row = self.version_repo.get_version(out.output_path, version)
        if row is None:
            raise LookupError(f"no version {version}")
        before = self.version_repo.current_version(out.output_path)
        published = self.versions.revert(out.output_path, version)
        # Going back says the version in force was worse than an older one (FR-19.4).
        self.telemetry.signal(
            "revert", unit_id=unit.id, version=published.version, lang=out.target_lang, restored=version,
            replaced=before.version if before else None, model=before.model if before else "",
        )
        # Cue edits that the restored text no longer contains are undone, so they stop locking their cues.
        restored = self._target(out)
        for correction in self.catalog.corrections(unit.id, out.target_lang):
            if correction.kind != "cue_edit":
                continue
            ordinal = int(correction.payload["cue"])
            if not 1 <= ordinal <= len(restored) or restored[ordinal - 1].text != correction.payload["text"]:
                self.catalog.deactivate_correction(correction.id)
        self._bazarr_refresh(unit)
        return {"ok": True, "version": published.version, "restored": version}

    # -- story memory (P4) ----------------------------------------------------------------
    def _target_or_none(self, unit: Unit, lang: str | None) -> Output | None:
        try:
            return self.output(unit, lang)
        except (LookupError, ValueError):
            return None

    def context(self, unit_id: int, lang: str | None = None) -> dict[str, Any]:
        """What the story memory gave this translation, and what it would give now (FR-5.2, ADR-7 Context tab)."""
        unit = self.unit(unit_id)
        if unit.series_id is None or self.memory is None:
            return {"applicable": False, "enabled": False}
        series = self.catalog.get_series(unit.series_id)
        out = self._target_or_none(unit, lang)
        current = self.version_repo.current_version(out.output_path) if out else None
        now = self.memory.preview(unit, self._settings())
        used = None
        if current and current.memory_hash:
            sections = self.memory.snapshot(current.memory_hash)
            used = {
                "version": current.version,
                "complete": current.context_complete,
                "missing": list(current.missing_units),
                "sections": [{"title": t, "body": b} for t, b in sections] if sections is not None else None,
                "changed": current.memory_hash != now["fingerprint"],
            }
        return {
            "applicable": True,
            "enabled": self.memory.enabled_for(unit, self._settings()),
            "policy": series.memory,
            "specials_after": series.specials_after,
            "label": unit_label(unit),
            "special": (unit.season or 0) == 0,
            "lang": out.target_lang if out else None,
            "used": used,
            "now": now,
        }

    def adopt_catalog_episodes(self, series_id: int) -> int:
        """Make a unit for every episode of the series the catalog lists, with the source subtitle it picks, so the
        episodes of a series whose subtitles were translated before (or by someone else) still show in Context and can
        have their facts read. A unit is otherwise made only when the admin acts on an item or a job runs. Idempotent;
        returns how many units it made or gave a source."""
        if self._source_languages is None:
            return 0
        series = self.catalog.get_series(series_id)
        entry = self.catalog.catalog_lookup(KIND_SERIES, series.bazarr_id) if series and series.bazarr_id else None
        if entry is None:
            return 0
        sources = self._source_languages()
        changed = 0
        for episode in self.catalog.catalog_entries(KIND_EPISODE, parent_id=entry.id):
            if episode.season is None or episode.episode is None:
                continue
            unit = self.library.unit_of(episode)
            picked = pick_source(episode, sources)
            if unit is not None and (unit.source_path or picked is None):
                continue
            path, code, language = (picked[0].path, str(picked[1]["code"]), str(picked[1]["language"])) if picked else ("", "", "")
            self.library.register_entry(episode, entry, source_path=path, source_code=code, source_language=language)
            changed += 1
        return changed

    def adopt_episodes(self, series_id: int) -> dict[str, Any]:
        """``POST /api/series/{id}/memory/adopt``: the admin opened the Context tab of this series, so its episodes get
        the units that tab lists (see ``adopt_catalog_episodes``). Idempotent; ``adopted`` is how many it made."""
        if self.catalog.get_series(series_id) is None or self.memory is None:
            raise LookupError(f"no series {series_id}")
        return {"ok": True, "adopted": self.adopt_catalog_episodes(series_id)}

    def series_memory(self, series_id: int) -> dict[str, Any]:
        series = self.catalog.get_series(series_id)
        if series is None or self.memory is None:
            raise LookupError(f"no series {series_id}")
        settings = self._settings()
        # A read: it makes no unit. The Context tab asks for the units of the catalog's episodes with ``adopt_episodes``
        # when it opens (a POST), so a poll of this answer never writes (R14-3).
        return {
            "series": {"id": series.id, "title": series.title},
            "policy": series.memory,
            "specials_after": series.specials_after,
            "global_enabled": self.memory.options(settings).enabled,
            "units": self.memory.coverage(series_id),
        }

    def set_series_memory(self, series_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Switch memory on or off for one series, or let it follow the global setting; anchor its specials."""
        series = self.catalog.get_series(series_id)
        if series is None or self.memory is None:
            raise LookupError(f"no series {series_id}")
        policy = body.get("policy")
        anchor = body.get("specials_after")
        if policy is not None and policy not in MEMORY_MODES:
            raise ValueError("policy must be '' (follow the global setting), 'on' or 'off'")
        if anchor is not None:
            anchor = str(anchor).strip().upper()
            if anchor and parse_anchor(anchor) is None:
                raise ValueError("specials_after must look like S02E05")
        self.catalog.set_series_memory(series_id, memory=policy, specials_after=anchor)
        return self.series_memory(series_id)

    def unit_facts(self, unit_id: int) -> dict[str, Any]:
        """What the facts stored for this episode say (reads only, no model call), for the Context tab to show."""
        unit = self.unit(unit_id)
        if unit.series_id is None or self.memory is None:
            raise ValueError("story memory applies to episodes of a series only")
        return self.memory.own_facts(unit)

    def refresh_context(self, unit_id: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read the facts of this episode and of the earlier ones that have a source subtitle (spends calls): one
        ``extract`` job per episode, in story order, that the queue runs under the same quota and breaker as a
        translation. The answer is the jobs made; the episodes left out say why (``no-source``: nothing to read;
        ``cached``: the newest facts are those of the source as it is now).

        ``unit_ids`` names exactly the episodes to read instead (of this series; nothing else is read)."""
        body = body or {}
        unit = self.unit(unit_id)
        if unit.series_id is None or self.memory is None:
            raise ValueError("story memory applies to episodes of a series only")
        only = self._picked_units(unit, body.get("unit_ids"))
        series = self.catalog.get_series(unit.series_id)
        wanted: list[dict[str, Any]] = []
        skipped: dict[str, str] = {}
        for target in self.memory.targets(unit, only):
            why = self.memory.needs_reading(target)
            if why:
                skipped[unit_label(target)] = why
                continue
            wanted.append({
                "unit_id": target.id, "label": unit_label(target), "title": series.title if series else target.title,
                # What fairness between series goes by: the same id the series' translation jobs carry.
                "series_id": series.bazarr_id if series else "",
            })
        created, merged = self.jobs.enqueue_extract(wanted)
        return {"ok": True, "job_ids": created, "queued": len(created), "merged": len(merged), "skipped": skipped}

    def _picked_units(self, unit: Unit, raw: Any) -> list[int] | None:
        """The episodes the admin ticked: whole numbers, all of this series. ``None`` when nothing was picked."""
        if raw is None:
            return None
        if not isinstance(raw, list) or not raw:
            raise ValueError("unit_ids must be a list of episode ids")
        ids: list[int] = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("unit_ids must be a list of episode ids")
            ids.append(value)
        own = {u.id for u in self.catalog.list_units(unit.series_id)}
        stray = [i for i in ids if i not in own]
        if stray:
            raise ValueError(f"episode {stray[0]} does not belong to this series")
        return list(dict.fromkeys(ids))

    # -- glossary (in the database; YAML is the exchange format, FR-18.10) --------------------------------
    def _series(self, series_id: int):
        series = self.catalog.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        return series

    def glossary(self, series_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary(self.library.series_owner(series_id), lang)

    def save_glossary(
        self, series_id: int, lang: str, text: str | None = None, *, doc: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.save_owner_glossary(self.library.series_owner(series_id), lang, text, doc=doc, dry_run=dry_run)

    def glossary_history(self, series_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary_history(self.library.series_owner(series_id), lang)

    def restore_glossary(self, series_id: int, lang: str, version: int, *, dry_run: bool = False) -> dict[str, Any]:
        return self.restore_owner_glossary(self.library.series_owner(series_id), lang, version, dry_run=dry_run)

    # The same four for a movie, by the movie's unit id (P12b): the glossary of `movies/<movie_key>`.
    def movie_glossary(self, unit_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary(self.library.movie_owner(unit_id), lang)

    def save_movie_glossary(
        self, unit_id: int, lang: str, text: str | None = None, *, doc: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.save_owner_glossary(self.library.movie_owner(unit_id), lang, text, doc=doc, dry_run=dry_run)

    def movie_glossary_history(self, unit_id: int, lang: str) -> dict[str, Any]:
        return self.owner_glossary_history(self.library.movie_owner(unit_id), lang)

    def restore_movie_glossary(self, unit_id: int, lang: str, version: int, *, dry_run: bool = False) -> dict[str, Any]:
        return self.restore_owner_glossary(self.library.movie_owner(unit_id), lang, version, dry_run=dry_run)

    def owner_glossary(self, owner: Owner, lang: str) -> dict[str, Any]:
        """The glossary as a document (for the form) and as YAML (to export), with the number of stale translations."""
        stored = self.glossaries.stored(owner.kind, owner.key, lang)
        glossary = self.glossaries.load(owner.kind, owner.key, lang)
        impact = self.library.impact_units(owner.units, lang, glossary=glossary)
        return {
            "lang": lang, "exists": stored is not None,
            "doc": g.to_doc(glossary) if stored else {"version": 1}, "yaml": self.glossaries.read_text(owner.kind, owner.key, lang),
            "version": stored.version if stored else 0, "stale_units": impact["stale_now"],
        }

    def save_owner_glossary(
        self, owner: Owner, lang: str, text: str | None = None, *, doc: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Save a glossary given as a document (the form) or as YAML text (an import). A bad one raises ValueError
        naming the offending path; ``dry_run`` only says how many translations the save would make stale."""
        if doc is not None:
            if not isinstance(doc, dict):
                raise ValueError("doc must be an object")
            parsed, note = g.from_doc(doc), "console"  # GlossaryError is a ValueError
        else:
            text = text or ""
            parsed, note = (g.loads(text) if text.strip() else g.Glossary()), "import"
        impact = self.library.impact_units(owner.units, lang, glossary=parsed)
        saved = None
        if not dry_run:
            saved = self.glossaries.save(owner.kind, owner.key, lang, parsed, note=note)
        return {"ok": True, "saved": not dry_run, "version": saved.version if saved else None, **impact}

    def glossary_export(self, kind: str, owner_id: int, lang: str) -> tuple[str, str]:
        """``(file name, YAML text)`` of a series' (``kind`` ``series``) or a movie's glossary, for a download link.
        A glossary that is not stored exports as an empty text, as ``GET …/glossary`` does."""
        owner = self.library.movie_owner(owner_id) if kind == "movies" else self.library.series_owner(owner_id)
        slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", owner.title or "").encode("ascii", "ignore").decode().lower()).strip("-")
        code = re.sub(r"[^A-Za-z0-9_-]+", "", lang) or "xx"
        return f"glossary-{slug or 'untitled'}-{code}.yaml", self.glossaries.read_text(owner.kind, owner.key, lang)

    def owner_glossary_history(self, owner: Owner, lang: str) -> dict[str, Any]:
        """What each save replaced, newest first (the history that stands in for the old backups)."""
        return {
            "lang": lang,
            "items": [
                {"version": r.version, "at": r.at, "note": r.note, "terms": len(r.doc.get("terms") or ()),
                 "characters": len(r.doc.get("characters") or ()), "address_rules": len(r.doc.get("address_rules") or ())}
                for r in self.glossaries.history(owner.kind, owner.key, lang)
            ],
        }

    def restore_owner_glossary(self, owner: Owner, lang: str, version: int, *, dry_run: bool = False) -> dict[str, Any]:
        """Put an earlier glossary back. It changes the rules like any other save, so the answer counts the
        translations it makes stale and ``dry_run`` only counts them."""
        version = int(version)
        impact = self.library.impact_units(
            owner.units, lang, glossary=self.glossaries.restored(owner.kind, owner.key, lang, version),
        )
        if dry_run:
            return {"ok": True, "saved": False, "version": None, "restored": version, **impact}
        restored = self.glossaries.restore(owner.kind, owner.key, lang, version)
        return {"ok": True, "saved": True, "version": restored.version, "restored": version, **impact}

    def copy_glossary(
        self, series_id: int, lang: str, source_series_id: int, *, merge: bool, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Copy another series' glossary into this one: a copy, not a link (Non-goals, v0.12). The answer counts the
        translations of this series the copy makes stale; ``dry_run`` only counts them."""
        series, source = self._series(series_id), self._series(source_series_id)
        if series.id == source.id:
            raise ValueError("choose another series to copy from")
        owner = self.library.series_owner(series.id)
        candidate = self.glossaries.copied(("series", source.key), ("series", series.key), lang, merge=merge)
        impact = self.library.impact_units(owner.units, lang, glossary=candidate)
        origin = source.title or source.key
        if dry_run:
            return {"ok": True, "saved": False, "version": None, "from": origin, **impact}
        result = self.glossaries.copy(("series", source.key), ("series", series.key), lang, merge=merge)
        return {"ok": True, "saved": True, "version": result.version, "from": origin, **impact}
