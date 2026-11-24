"""Corrections of a unit: a cue edit, a term, an address rule or a note (FR-11, FR-19.4).

``CorrectionsMixin`` is a part of ``UnitService`` (``units.py``) and uses what it holds (``catalog``, ``library``,
``versions``, ``telemetry``, ...). ``LookupError`` means "not found" and ``ValueError`` "bad request".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from subcore import glossary as g
from subcore import srt

from ..domain.ports import Correction, Output, Unit
from ..domain.rules import correction_position
from .versions import CUE_EDIT


KINDS = ("term", "address", "note", "cue_edit")
SCOPES = ("episode", "forward", "series")


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


class CorrectionsMixin:
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
        if not edits:  # nothing to change must not make a version, an event and a rescan
            raise ValueError("cue edit: give at least one cue to edit")
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
            # What the text was made with does not change because a cue was edited: the Context tab still shows it, a later
            # change of the story memory still makes the episode stale, and an incomplete one stays incomplete.
            memory_hash=current.memory_hash if current else "",
            context_complete=current.context_complete if current else True,
            missing_units=current.missing_units if current else (),
        )
        ids = []
        written_by = self._written_by(output_path, current)
        for ordinal, text in sorted(parsed.items()):
            replaced = [
                old for old in self.catalog.corrections(unit.id, lang)
                if old.kind == "cue_edit" and int(old.payload["cue"]) == ordinal
            ]
            # What the machine wrote stays with the correction through later edits of the same cue: it is the "before" of
            # the feedback loop (FR-19.6). An older correction that never kept it leaves it unknown.
            if replaced:
                before = str(replaced[-1].payload.get("before_text") or "")
            else:
                before = target[ordinal - 1].text
            for old in replaced:
                self.catalog.deactivate_correction(old.id)
            ids.append(self.catalog.add_correction(
                unit.id, lang, "cue_edit",
                {"cue": ordinal, "text": text, "source_text": source[ordinal - 1].text,
                 **({"before_text": before} if before else {})},
            ))
            # What the admin changed in a cue is the clearest sign of what the machine got wrong (FR-19.4).
            self.telemetry.signal(
                "cue_edit", unit_id=unit.id, version=published.version, cue=ordinal,
                before=target[ordinal - 1].text, after=text, lang=lang, source=source[ordinal - 1].text,
                model=current.model if current else "", context_hash=current.context_hash if current else "",
                job_id=written_by,  # the job that wrote the cue: it names the prompt version (Insights)
            )
        self.telemetry.event(
            "version.cue_edit", unit_id=unit.id, reason=f"{len(parsed)} cue(s) edited by the admin",
            target=Path(output_path).name, version=published.version, cues=sorted(parsed),
        )
        self._after_edit(unit, lang)
        self._bazarr_refresh(unit)
        return {"ok": True, "scope": "episode", "ids": ids, "version": published.version, "warnings": warnings}

    def _written_by(self, output_path: str, current: Any) -> str:
        """The job that wrote the text of ``current``: edits made by hand carry no job, so follow the versions it came
        from (its parent, then that one's) to the first a job made. Not the newest version with a job: that can be a
        variant nobody has adopted."""
        if current is None:
            return ""
        by_number = {v.version: v for v in self.version_repo.versions(output_path)}
        seen: set[int] = set()
        version = current
        while version is not None and version.version not in seen:
            if version.job_id:
                return version.job_id
            seen.add(version.version)
            version = by_number.get(version.parent_version) if version.parent_version is not None else None
        return ""

    def _after_edit(self, unit: Unit, lang: str) -> None:
        """Let the feedback loop look at the series' edits (FR-19.6). It costs no model call and never fails the edit."""
        hook = self.feedback
        if hook is None:
            return
        try:
            hook.after_edit(unit, lang)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Looking for patterns in the edits of %s failed: %s", unit.title, exc)
