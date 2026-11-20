"""The first generator of the Review inbox: proposals drawn from story facts (P14; SPEC FR-6, FR-18.3).

``subcore.suggest`` decides *what* is worth proposing and asks the model for the target-language forms; this module
gathers its inputs (the stored facts of a series in story order, the series' source cues for evidence, its glossary and
what the inbox already holds) and hands the result to :class:`SuggestionService`, which is the only place that stores
and applies proposals. A generator has no review logic of its own.

* ``address_rule`` and ``term`` proposals have ``source = facts`` (FR-6.1, FR-6.2); character cards ``source = memory``
  (FR-18.3). All of them wait as ``pending``; a facts proposal never carries ``sources``, so ``auto_accept_terms``
  (which needs two independent research sources) can never apply one by itself (FR-4.4).
* What the inbox already holds, in any status, is not asked about again: a candidate with a known identity costs no
  model call, so a rejected proposal is not paid for twice and a run over unchanged facts is free. A candidate the
  model had nothing usable for is remembered for ``SKIP_DAYS`` (and for the prompt version it was asked under); both
  are taken out before each kind's limit, so they cannot crowd out the candidates behind them (R13-5).
* Each kind (address rules, terms, characters) is stored as soon as it comes back, before the next is asked: a provider
  error in a later step propagates typed (the queue maps it) and loses nothing already paid for (R13-4). A run is safe to
  repeat. A character proposal reads the facts only up to the episode where it qualified (R13-2, ``subcore.suggest``).
* It reads facts, never extracts them. A run is a ``suggest`` job (:meth:`FactsProposer.request`, run by ``read_jobs.SuggestJob``)
  so the queue's quota, breaker and call caps apply; the hook (:meth:`FactsProposer.after_facts`) only queues that job and
  swallows every error: a generator that fails must never make a translation job slow or fail.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from subcore import srt
from subcore.glossary import Glossary
from subcore.providers import Provider
from subcore.suggest import (
    SUGGEST_PROMPT_VERSION, AddressItem, CharacterItem, Draft, Episode, SuggestError, TermItem,
    address_drafts, address_items, character_drafts, character_items, term_drafts, term_items,
)

from . import queue_policy as policy
from .memory import StoryMemory, unit_label
from .ports import Telemetry
from .queue import JobQueue
from .suggestion_keys import identity_key
from .suggestions import Proposal, SuggestionService, normalise
from .telemetry import NULL
from .translation import redact

ADDRESS_PER_RUN = 40  # pair states asked about in one call; the rest wait for the next run
SKIP_DAYS = 7  # a candidate the model could not answer is left out of the runs of this many days (R13-5)
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


class FactsProposer:
    def __init__(
        self,
        memory: StoryMemory,
        suggestions: SuggestionService,
        *,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.memory, self.suggestions = memory, suggestions
        self._settings = settings_source or (lambda: {})
        self.telemetry: Telemetry = telemetry or NULL
        # The queue a run is asked through (``request``); wired by the composition root once the queue exists.
        self.jobs: JobQueue | None = None

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

        library = self.suggestions.library
        glossary: Glossary = library.glossaries.load(owner.kind, owner.key, lang)
        cards = library.styles.cards(owner.kind, owner.key) if library.styles else []
        held = self._held(series_id, lang)  # taken out BEFORE each kind's limit, or they would crowd out the rest
        address = address_items(episodes, cues_of, glossary=glossary, exclude=held["address_rule"])[:ADDRESS_PER_RUN]
        terms = term_items(episodes, cues_of, glossary=glossary, exclude=held["term"])
        characters = character_items(
            episodes, cues_of, glossary=glossary, exclude=held["character"],
            cards=[name for card in cards for name in card.names],
        )
        return Scan(len(episodes), {unit_label(u): u.id for u, _ in stored}, address, terms, characters)

    def _held(self, series_id: int, lang: str) -> dict[str, set[tuple[str, ...]]]:
        """What a run must not ask about for this series and language: every identity the inbox holds, whatever its
        status, and every candidate the model recently had no answer for (R13-5)."""
        store = self.suggestions.store
        keys = {row.identity or identity_key(row.kind, row.payload) for row in store.suggestions(series_id=series_id, target_lang=lang, limit=10_000)}
        keys |= store.suggestion_skips(series_id, lang, SUGGEST_PROMPT_VERSION, time.time() - SKIP_DAYS * 86400)
        held: dict[str, set[tuple[str, ...]]] = {"address_rule": set(), "term": set(), "character": set()}
        for key in keys:
            try:
                kind, *parts = json.loads(key)
            except ValueError:
                continue
            held.setdefault(kind, set()).add(tuple(parts))
        return held

    def estimate(self, series_id: int, lang: str) -> dict[str, Any]:
        return {"ok": True, "estimate_only": True, **self.scan(series_id, lang).to_json()}

    # -- running --------------------------------------------------------------------------------------------------
    def run(
        self, series_id: int, lang: str, provider_for: ProviderFor, report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask the model about the candidates and put what it answers in the inbox, one kind at a time: what a kind
        brought is stored before the next kind is asked, so a provider error in a later step (it propagates, typed)
        never loses what an earlier step was paid for, and a run again asks only about what is left (R13-4). An answer
        the model garbles costs only that kind of proposal. ``report`` (when given) is filled in as the run goes."""
        scan = self.scan(series_id, lang)
        owner = self.suggestions.library.series_owner(series_id)
        report = report if report is not None else {}
        report.update({"ok": True, **scan.to_json(), "errors": [], "added": 0, "auto_accepted": 0})
        if not scan.calls:
            return report
        provider = provider_for("extract")
        title = owner.title
        steps: list[tuple[str, str, str, Sequence[Any], Callable[[], list[Draft]]]] = []
        if scan.address:
            steps.append(("facts", "address rules", "address_rule", scan.address, lambda: address_drafts(provider, scan.address, lang=lang)))
        if scan.terms:
            steps.append(("facts", "terms", "term", scan.terms, lambda: term_drafts(provider, scan.terms, lang=lang, title=title)))
        if scan.characters:
            steps.append((
                "memory", "characters", "character", scan.characters,
                lambda: character_drafts(provider, scan.characters, lang=lang, title=title),
            ))
        store = self.suggestions.store
        for source, name, kind, items, ask in steps:
            try:
                drafts = ask()
            except SuggestError as exc:  # nothing useful came back: not a reason to remember the candidates as unanswerable
                report["errors"].append(f"{name}: {exc}")
                continue
            usable: list[Draft] = []
            for draft in drafts:
                try:
                    normalise(draft.kind, draft.payload)
                except ValueError:
                    continue
                usable.append(draft)
            answered = {identity_key(d.kind, d.payload) for d in usable}
            if usable:
                outcome = self.suggestions.propose(
                    series_id, lang,
                    [Proposal(d.kind, d.payload, evidence=tuple(self._linked(e, scan) for e in d.evidence)) for d in usable],
                    source=source,
                )
                report["added"] += outcome.added
                report["auto_accepted"] += outcome.auto_accepted
                counts = report.setdefault("outcomes", {}).setdefault(source, {})
                for field_name, value in outcome.to_json().items():
                    counts[field_name] = (counts.get(field_name, [] if isinstance(value, list) else 0)) + value
            # What the model answered nothing usable for is remembered, or it would head the list of every run (R13-5).
            store.suggestion_skip(
                series_id, lang, [k for k in (json.dumps([kind, *item.key], ensure_ascii=False) for item in items) if k not in answered],
                SUGGEST_PROMPT_VERSION,
            )
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

    # -- asking for a run: a job, never a thread of the caller's ----------------------------------------------
    def request(self, series_id: int, lang: str, *, manual: bool = True) -> tuple[str, bool]:
        """Queue the run for this series and language as a ``suggest`` job (F7): the queue runs it under the day's quota
        and the breaker, one open per series and language. The admin's own request is interactive; the one a translation
        leaves behind (``manual=False``) is the lowest class. Returns ``(job id, whether it is new)``."""
        if self.jobs is None:
            raise ValueError("no job queue is wired into the proposal generator")
        series = self.memory.units.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        if manual:
            return self.jobs.enqueue_suggest(series_id, lang, title=series.title, bazarr_id=series.bazarr_id or "")
        return self.jobs.enqueue_suggest(
            series_id, lang, title=series.title, bazarr_id=series.bazarr_id or "", origin="facts", prio=policy.AUTO,
        )

    # -- the hook of a translation job ----------------------------------------------------------------------
    def after_facts(self, unit_ids: list[int], lang: str, settings: dict[str, Any]) -> None:
        """Facts of these units were just extracted: queue a look for proposals in their series. It only makes a job (the
        run happens later, on its own turn and budget), and never raises, so the translation job that called it ends as
        soon as it is done and its own calls are not spent on this."""
        if not unit_ids or not lang:
            return
        try:
            enabled = settings.get("suggest_from_facts", True)
            if not (enabled is True or str(enabled).strip().lower() in ("1", "true", "on")):
                return
            series_ids = {u.series_id for u in (self.memory.units.get_unit(i) for i in unit_ids) if u and u.series_id}
            for series_id in sorted(series_ids):
                self.request(series_id, lang, manual=False)
        except Exception as exc:  # noqa: BLE001 - by contract nothing here may reach the job
            message = redact(f"{type(exc).__name__}: {exc}", settings)
            logging.warning("Could not queue a look for suggestions in the new facts (%s)", message)
            self.telemetry.event(
                "suggestions.generator_failed", reason="the generator could not be queued; the translation job is not affected",
                lang=lang, error=message[:300],
            )
