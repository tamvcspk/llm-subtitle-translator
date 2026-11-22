"""The feedback loop (P15, FR-19.6): what the admin corrects, again and again, becomes a suggestion; what they corrected
becomes the golden set a prompt or a style is measured against.

* ``FeedbackProposer.run`` reads the cues an admin corrected in a series (``Store.series_cue_edit_pairs``) and looks for
  the same swap of words in at least three different cues (``subcore.feedback``). A swap whose cues share a word of the
  *source* is proposed as a ``term`` (source word -> the admin's wording) with those cues as the evidence; it waits in
  Review as ``pending`` and, having no research ``sources``, is never applied by itself (FR-4.4). It makes no model call.
  What the series already decided about that term is kept by ``SuggestionService.propose``, like for every generator.
* ``golden`` is the corrected cues of a series in the format ``subcore compare --golden`` reads (``subcore.golden``).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from subcore import golden as golden_mod
from subcore.feedback import MIN_REPEATS, EditedCue, find_patterns

from ..domain.ports import CuePair, Telemetry, Unit
from .suggestions import Proposal, SuggestionService
from ..adapters.telemetry import NULL

EVIDENCE_CUES = 8  # cues shown with a suggestion: enough to judge it, few enough to read
SETTING = "suggest_from_edits"


def episode_label(pair: CuePair) -> str:
    if pair.season is None and pair.episode is None:
        return ""
    return f"S{pair.season or 0:02d}E{pair.episode or 0:02d}"


class FeedbackProposer:
    def __init__(
        self,
        suggestions: SuggestionService,
        *,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.suggestions = suggestions
        self.store = suggestions.store
        self._settings = settings_source or (lambda: {})
        self.telemetry: Telemetry = telemetry or NULL

    # -- proposals ---------------------------------------------------------------------------------------------
    def run(self, series_id: int, lang: str) -> dict[str, Any]:
        """Look at the series' corrected cues and propose what repeats. Safe to repeat: what is already in the inbox, was
        decided, or is in the glossary is skipped by ``propose``."""
        self.suggestions.library.series_owner(series_id)  # LookupError for a series that is not there
        pairs = self.store.series_cue_edit_pairs(series_id, lang)
        known = [p for p in pairs if p.before.strip()]
        patterns = find_patterns(
            [
                EditedCue((p.unit_id, p.cue), p.source, p.before, p.after, where=episode_label(p), cue=p.cue)
                for p in known
            ],
            min_repeats=MIN_REPEATS,
        )
        proposals: list[Proposal] = []
        unnamed = 0
        for pattern in patterns:
            if not pattern.source:  # the cues share no word of the source: there is no term to propose
                unnamed += 1
                continue
            evidence = tuple(
                {"episode": c.where, "cue": c.cue, "unit_id": c.key[0], "text": f"{c.before} → {c.after}"}
                for c in pattern.cues[:EVIDENCE_CUES]
            )
            note = f"You changed “{pattern.old}” to “{pattern.new}” in {pattern.count} cues."
            proposals.append(Proposal("term", {"source": pattern.source, "target": pattern.new, "note": note}, evidence))
        report = self.suggestions.propose(series_id, lang, proposals, source="feedback") if proposals else None
        self.telemetry.event(
            "feedback.mined", reason=f"{len(patterns)} repeated correction(s) in {len(known)} corrected cue(s)",
            series=series_id, lang=lang, cues=len(known), patterns=len(patterns), proposed=len(proposals), unnamed=unnamed,
            without_before=len(pairs) - len(known),
        )
        return {
            "cues": len(known), "without_before": len(pairs) - len(known), "patterns": len(patterns),
            "proposed": len(proposals), "unnamed": unnamed, "report": report.to_json() if report else None,
        }

    def after_edit(self, unit: Unit, lang: str) -> None:
        """Called after a cue edit: look again, unless the admin switched this off. Never raises into the edit."""
        enabled = self._settings().get(SETTING, True)
        if unit.series_id is None or not (enabled is True or str(enabled).strip().lower() in ("1", "true", "on")):
            return
        try:
            self.run(unit.series_id, lang)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Looking for repeated corrections in series %s failed: %s", unit.series_id, exc)

    # -- the golden set -----------------------------------------------------------------------------------------
    def golden(self, series_id: int, lang: str) -> tuple[str, str]:
        """``(file name, golden.json text)`` of the series' corrected cues in ``lang``."""
        owner = self.suggestions.library.series_owner(series_id)
        pairs = self.store.series_cue_edit_pairs(series_id, lang)
        cues = [golden_mod.GoldenCue(p.season, p.episode, p.cue, p.source, p.after) for p in pairs]
        name = "".join(ch if ch.isalnum() else "-" for ch in owner.key.lower()).strip("-") or f"series-{series_id}"
        return f"{name}.{lang.lower()}.golden.json", golden_mod.dumps(cues, series=owner.title, lang=lang)
