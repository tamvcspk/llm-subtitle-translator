"""The versions of a unit's target: compare a candidate, choose, adopt, diff, revert, remove (FR-16, FR-18.8).

``VersionsMixin`` is a part of ``UnitService`` (``units.py``); ``versions.py`` (``TranslationVersions``) does the
writing and these methods are the use cases the Versions and Compare pages call.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from subcore import srt
from subcore.style import Style, StyleError
from subcore.translate.prompt import PROMPT_VERSION as SUBCORE_PROMPT_VERSION

from ..domain.ports import CURRENT, PRUNED, Output, Unit
from ..domain.rules import format_ms


class VersionsMixin:
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
        view = {
            "lang": out.target_lang, "version": version, "status": row.status, "params": row.params, "model": row.model,
            "aligned": len(other) == len(source) and len(current) == len(source),
            "differing": sum(1 for r in rows if r["differs"]), "rows": rows,
        }
        if self.judge is not None:  # advice only: a verdict of the judge sits next to a row, it never picks one
            view["judge"] = self.judge.annotate(unit.id, out.target_lang, version, rows)
        return view

    def ask_judge(self, unit_id: int, version: int, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/units/{id}/versions/{v}/judge``: queue the judging of a candidate (or its price, ``estimate_only``)."""
        if self.judge is None:
            raise ValueError("The judge is not available")
        return self.judge.request(unit_id, version, body)

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
