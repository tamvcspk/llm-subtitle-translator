"""The first generator of the Review inbox: proposals drawn from story facts (P14; SPEC FR-6, FR-18.3).

``subcore.suggest`` decides *what* is worth proposing and asks the model for the target-language forms; this module
gathers its inputs (the stored facts of a series in story order, the series' source cues for evidence, its glossary and
what the inbox already holds) and hands the result to :class:`SuggestionService`, which is the only place that stores
and applies proposals. A generator has no review logic of its own.

* ``address_rule`` and ``term`` proposals have ``source = facts`` (FR-6.1, FR-6.2); character cards ``source = memory``
  (FR-18.3). All of them wait as ``pending``; a facts proposal never carries ``sources``, so ``auto_accept_terms``
  (which needs two independent research sources) can never apply one by itself (FR-4.4).
* What the inbox already holds, in any status, is not asked about again: a candidate with a known identity costs no
  model call, so a rejected proposal is not paid for twice and a run over unchanged facts is free.
* It reads facts, never extracts them, and the hook (:meth:`FactsProposer.after_facts`) swallows every error: a
  generator that fails must never make a translation job slow or fail.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from subcore import srt
from subcore.glossary import Glossary
from subcore.providers import Provider, ProviderError
from subcore.suggest import (
    SUGGEST_PROMPT_VERSION, AddressItem, CharacterItem, Draft, Episode, SuggestError, TermItem,
    address_drafts, address_items, character_drafts, character_items, term_drafts, term_items,
)

from .config import DEFAULT_MODEL, model_for_task
from .memory import StoryMemory, unit_label
from .ports import MissingApiKey, ProviderFactory, Telemetry
from .suggestions import Proposal, SuggestionService, _identity
from .telemetry import NULL
from .translation import redact

ADDRESS_PER_RUN = 40  # pair states asked about in one call; the rest wait for the next run
ProviderFor = Callable[[str], Provider]


@dataclass
class Scan:
    """What a run would ask the model about: the candidates left after the glossary and the inbox are taken out."""

    episodes: int = 0
    unit_ids: dict[str, int] = field(default_factory=dict)  # episode label -> unit id, for the links in the evidence
    address: list[AddressItem] = field(default_factory=list)
    terms: list[TermItem] = field(default_factory=list)
    characters: list[CharacterItem] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return sum(1 for items in (self.address, self.terms, self.characters) if items)

    def to_json(self) -> dict[str, int]:
        return {
            "episodes": self.episodes, "address_rules": len(self.address), "terms": len(self.terms),
            "characters": len(self.characters), "calls": self.calls,
        }


def _pair_key(a: str, b: str, start: str) -> tuple[str, str, str]:
    low, high = sorted((a.casefold(), b.casefold()))
    return (low, high, start)


class FactsProposer:
    def __init__(
        self,
        memory: StoryMemory,
        suggestions: SuggestionService,
        *,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        provider_factory: ProviderFactory | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.memory, self.suggestions = memory, suggestions
        self._settings = settings_source or (lambda: {})
        self._provider_factory = provider_factory
        self.telemetry: Telemetry = telemetry or NULL

    # -- what there is to propose -----------------------------------------------------------------------------
    def scan(self, series_id: int, lang: str) -> Scan:
        """The candidates a run would ask about. Reads the facts, the source cues, the glossary and the inbox; makes no
        model call and writes nothing."""
        owner = self.suggestions.library.series_owner(series_id)
        stored = self.memory.story_facts(series_id)
        episodes = [Episode(unit_label(u), int(u.season or 0), int(u.episode or 0), facts) for u, facts in stored]
        sources = {unit_label(u): u.source_path for u, _ in stored}
        cache: dict[str, list[srt.Cue]] = {}

        def cues_of(label: str) -> list[srt.Cue]:
            if label not in cache:
                path = sources.get(label) or ""
                try:
                    cache[label] = srt.parse_file(path) if path and Path(path).is_file() else []
                except (OSError, srt.SrtError):
                    cache[label] = []
            return cache[label]

        glossary: Glossary = self.suggestions.library.glossaries.load(owner.kind, owner.key, lang)
        known = self._known(series_id, lang)
        address = [
            item for item in address_items(episodes, cues_of, glossary=glossary) if item.key not in known["address_rule"]
        ][:ADDRESS_PER_RUN]
        terms = [item for item in term_items(episodes, cues_of, glossary=glossary) if item.key not in known["term"]]
        characters = [
            item for item in character_items(episodes, cues_of, glossary=glossary) if item.key not in known["character"]
        ]
        return Scan(len(episodes), {unit_label(u): u.id for u, _ in stored}, address, terms, characters)

    def _known(self, series_id: int, lang: str) -> dict[str, set[tuple[str, ...]]]:
        """The identities the inbox holds for this series and language, whatever their status."""
        known: dict[str, set[tuple[str, ...]]] = {"address_rule": set(), "term": set(), "character": set()}
        for row in self.suggestions.store.suggestions(series_id=series_id, target_lang=lang, limit=10_000):
            try:
                identity = _identity(row.kind, row.payload)
            except (KeyError, TypeError):
                continue
            if row.kind == "address_rule":
                identity = _pair_key(row.payload["a"], row.payload["b"], identity[2])
            known.setdefault(row.kind, set()).add(identity)
        return known

    def estimate(self, series_id: int, lang: str) -> dict[str, Any]:
        return {"ok": True, "estimate_only": True, **self.scan(series_id, lang).to_json()}

    # -- running --------------------------------------------------------------------------------------------------
    def run(self, series_id: int, lang: str, provider_for: ProviderFor) -> dict[str, Any]:
        """Ask the model about the candidates and put what it answers in the inbox. Provider errors propagate; an
        answer the model garbles costs only that kind of proposal."""
        scan = self.scan(series_id, lang)
        owner = self.suggestions.library.series_owner(series_id)
        report: dict[str, Any] = {"ok": True, **scan.to_json(), "errors": [], "added": 0, "auto_accepted": 0}
        if not scan.calls:
            return report
        provider = provider_for("extract")
        title = owner.title
        steps: list[tuple[str, str, Callable[[], list[Draft]]]] = []
        if scan.address:
            steps.append(("facts", "address rules", lambda: address_drafts(provider, scan.address, lang=lang)))
        if scan.terms:
            steps.append(("facts", "terms", lambda: term_drafts(provider, scan.terms, lang=lang, title=title)))
        if scan.characters:
            steps.append(("memory", "characters", lambda: character_drafts(provider, scan.characters, lang=lang, title=title)))
        by_source: dict[str, list[Proposal]] = {"facts": [], "memory": []}
        for source, name, ask in steps:
            try:
                drafts = ask()
            except SuggestError as exc:
                report["errors"].append(f"{name}: {exc}")
                continue
            by_source[source] += [
                Proposal(d.kind, d.payload, evidence=tuple(self._linked(e, scan) for e in d.evidence)) for d in drafts
            ]
        for source, proposals in by_source.items():
            if proposals:
                outcome = self.suggestions.propose(series_id, lang, proposals, source=source)
                report["added"] += outcome.added
                report["auto_accepted"] += outcome.auto_accepted
                report.setdefault("outcomes", {})[source] = outcome.to_json()
        self.telemetry.event(
            "suggestions.generated",
            reason=f"{scan.calls} model call(s) over the facts of {scan.episodes} episode(s) of {title} for {lang}",
            series=title, lang=lang, added=report["added"], errors=len(report["errors"]), **scan.to_json(),
        )
        return report

    @staticmethod
    def _linked(evidence: dict[str, Any], scan: Scan) -> dict[str, Any]:
        unit_id = scan.unit_ids.get(str(evidence.get("episode")))
        return {**evidence, "unit_id": unit_id} if unit_id else evidence

    def generate(self, series_id: int, lang: str, *, estimate_only: bool = False) -> dict[str, Any]:
        """The admin's own request: ``estimate_only`` says what a run would ask, otherwise it runs (spends calls)."""
        if estimate_only:
            return self.estimate(series_id, lang)
        settings = self._settings()
        model = str(settings.get("gst_model") or DEFAULT_MODEL)
        providers: dict[str, Provider] = {}
        deadline = time.monotonic() + 600

        def provider_for(task: str) -> Provider:
            name = model_for_task(settings, task, model)
            if name not in providers:
                if self._provider_factory is None:
                    raise RuntimeError("no provider factory is wired into the proposal generator")

                def record(log) -> None:
                    self.telemetry.llm_call("console-suggest", log, prompt_version=f"suggest-{SUGGEST_PROMPT_VERSION}")

                providers[name] = self._provider_factory(settings, name, deadline=deadline, on_call=record)
            return providers[name]

        try:
            return self.run(series_id, lang, provider_for)
        except MissingApiKey as exc:
            raise ValueError(str(exc)) from None
        except ProviderError as exc:
            raise ValueError(redact(f"The provider could not finish ({type(exc).__name__}: {exc})", settings)) from None

    # -- the hook of a translation job ----------------------------------------------------------------------
    def after_facts(self, unit_ids: list[int], lang: str, provider_for: ProviderFor, settings: dict[str, Any]) -> None:
        """Facts of these units were just extracted: look for proposals in their series. Never raises and never waits
        on anything but the model calls themselves, so a job that called it is neither failed nor stalled by it."""
        if not unit_ids or not lang:
            return
        try:
            enabled = settings.get("suggest_from_facts", True)
            if not (enabled is True or str(enabled).strip().lower() in ("1", "true", "on")):
                return
            series_ids = {u.series_id for u in (self.memory.units.get_unit(i) for i in unit_ids) if u and u.series_id}
            for series_id in sorted(series_ids):
                self.run(series_id, lang, provider_for)
        except Exception as exc:  # noqa: BLE001 - by contract nothing here may reach the job
            message = redact(f"{type(exc).__name__}: {exc}", settings)
            logging.warning("Could not look for suggestions in the new facts (%s)", message)
            self.telemetry.event(
                "suggestions.generator_failed", reason="the generator failed; the translation job is not affected",
                lang=lang, error=message[:300],
            )
