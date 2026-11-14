"""Actions on a library unit: view cues, correct, retranslate, inspect and revert versions (FR-10, FR-11).

``UnitService`` is what the HTTP layer calls (like ``ConsoleActions`` for the queue); it owns no
HTTP details. ``LookupError`` means "not found" and ``ValueError`` means "bad request".
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

from subcore import glossary as g
from subcore import srt

from subcore.providers import ProviderError

from .config import DEFAULT_MODEL
from .glossaries import GlossaryFiles
from .library import Library
from .ports import CURRENT, Correction, JobQueue, MissingApiKey, Output, ProviderFactory, Unit, UsageLog
from .memory import MEMORY_MODES, StoryMemory, parse_anchor, unit_label
from .rules import correction_position
from .translation import redact
from .versions import CUE_EDIT, TranslationVersions

KINDS = ("term", "address", "note", "cue_edit")
SCOPES = ("episode", "forward", "series")
MAX_CUE_PAGE = 5000

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
        glossaries: GlossaryFiles,
        versions: TranslationVersions,
        jobs: JobQueue,
        refresh_bazarr: Callable[[dict[str, Any]], None] | None = None,
        memory: StoryMemory | None = None,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        provider_factory: ProviderFactory | None = None,
        usage: UsageLog | None = None,
    ) -> None:
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
        self._provider_factory = provider_factory

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

    def _bazarr_refresh(self, unit: Unit) -> None:
        if self._refresh is None:
            return
        series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        try:
            self._refresh({
                "media_type": unit.media_type or ("series" if series else "movie"),
                "series_id": series.bazarr_id if series else "",
                "media_id": unit.bazarr_id,
            })
        except Exception as exc:  # noqa: BLE001 - the file is already published; Bazarr rescans on its own schedule
            logging.warning("Bazarr scan after a subtitle change failed (%s)", type(exc).__name__)

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
        """``forward``/``series`` corrections live in the glossary YAML (FR-11.2.3)."""
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
        self.glossaries.save(gkind, key, lang, glossary)
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
        self._bazarr_refresh(unit)
        return {"ok": True, "scope": "episode", "ids": ids, "version": published.version, "warnings": warnings}

    # -- retranslation ------------------------------------------------------------------
    def retranslate(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        unit = self.unit(unit_id)
        out = self.output(unit, body.get("lang"))
        lang = out.target_lang
        source = self._source(unit)  # also proves the source is still there
        window = self._range(body.get("range"), source)
        if window and not Path(out.output_path).is_file():
            raise ValueError("there is no target subtitle yet; retranslate the whole episode")

        created: list[int] = []
        for new in body.get("new_corrections") or []:
            if not isinstance(new, dict):
                raise ValueError("new_corrections must be a list of corrections")
            created.extend(self.add_correction(unit_id, {**new, "lang": lang}).get("ids", []))

        ids = body.get("correction_ids")
        if ids is not None:
            valid = {c.id for c in self.catalog.corrections(unit_id, lang)}
            wanted = [int(i) for i in ids] + created
            unknown = [i for i in wanted if i not in valid]
            if unknown:
                raise ValueError(f"unknown or inactive correction(s): {', '.join(map(str, unknown))}")
            ids = sorted(set(wanted))

        series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        job_id = self.jobs.enqueue_retranslation({
            "provider": "gemini-console",
            "video_path": unit.video_path, "subtitle_path": unit.source_path,
            "output_path": out.output_path, "source_code": unit.source_code,
            "source_language": unit.source_language or unit.source_code,
            "target_code": lang, "target_language": out.target_language or lang,
            "media_type": unit.media_type or ("series" if series else "movie"),
            "series_id": series.bazarr_id if series else "", "media_id": unit.bazarr_id,
            "unit_id": unit.id, "retranslate": {"range": window, "correction_ids": ids},
        })
        return {"ok": True, "job_id": job_id, "range": window, "correction_ids": ids}

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
                    "version": r.version, "trigger": r.trigger, "status": r.status,
                    "parent_version": r.parent_version, "model": r.model, "sha256": r.sha256,
                    "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "created_at": r.created_at,
                    "current": r.status == CURRENT and on_disk,
                }
                for r in reversed(rows)
            ],
        }

    def _version_cues(self, out: Output, version: int) -> list[srt.Cue]:
        row = self.version_repo.get_version(out.output_path, version)
        if row is None:
            raise LookupError(f"no version {version}")
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
        published = self.versions.revert(out.output_path, version)
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

    def series_memory(self, series_id: int) -> dict[str, Any]:
        series = self.catalog.get_series(series_id)
        if series is None or self.memory is None:
            raise LookupError(f"no series {series_id}")
        settings = self._settings()
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

    def refresh_context(self, unit_id: int) -> dict[str, Any]:
        """Extract facts now for this episode and the earlier ones that have a source subtitle (spends calls)."""
        unit = self.unit(unit_id)
        if unit.series_id is None or self.memory is None:
            raise ValueError("story memory applies to episodes of a series only")
        settings = self._settings()
        model = str(settings.get("gst_model") or DEFAULT_MODEL)
        providers: dict[str, Any] = {}

        def provider_for(task: str):
            name = model if task == "translate" else (str(settings.get("memory_model") or "").strip() or model)
            if name not in providers:
                if self._provider_factory is None:
                    raise RuntimeError("no provider factory is wired into the unit service")
                def record(log) -> None:
                    if self.usage is not None:
                        self.usage.record_llm_call(
                            "console-context", provider=log.provider, model=log.model, task=log.task,
                            tokens_in=log.tokens_in, tokens_out=log.tokens_out, latency_ms=log.latency_ms,
                            outcome=log.outcome,
                        )
                providers[name] = self._provider_factory(settings, name, deadline=time.monotonic() + 600, on_call=record)
            return providers[name]

        try:
            result = self.memory.refresh(unit, settings, provider_for)
        except MissingApiKey as exc:
            raise ValueError(str(exc)) from None
        except ProviderError as exc:
            raise ValueError(redact(f"The provider could not finish ({type(exc).__name__}: {exc})", settings)) from None
        return {"ok": True, **result}

    # -- glossary -------------------------------------------------------------------------
    def glossary(self, series_id: int, lang: str) -> dict[str, Any]:
        series = self.catalog.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        text = self.glossaries.read_text("series", series.key, lang)
        impact = self.library.glossary_impact(series_id, lang, g.loads(text) if text.strip() else g.Glossary())
        return {"lang": lang, "exists": bool(text.strip()), "yaml": text, "stale_units": impact["stale_now"]}

    def save_glossary(self, series_id: int, lang: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
        series = self.catalog.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        parsed = g.loads(text) if text.strip() else g.Glossary()  # GlossaryError is a ValueError
        impact = self.library.glossary_impact(series_id, lang, parsed)
        if not dry_run:
            self.glossaries.write("series", series.key, lang, text)
        return {"ok": True, "saved": not dry_run, **impact}
