"""Research grounding: the names and terms of a series looked up on the web (P16; SPEC FR-7, FR-4.4, FR-7.5, FR-7.6).

``subcore.research`` asks the model (a search call, then a call that reads its answer); this module gathers what to ask about
(the terms two or more episodes share that the glossary and the inbox do not have), keeps the budget, turns the pages the
provider reported into links, and hands the result to :class:`SuggestionService`, the only place that stores and applies
proposals. A term the web backs with two independent sites is applied by itself only when ``auto_accept_terms`` says so
(FR-4.4); there is no other way in.

* **A job of its own** (kind ``research``, :meth:`Researcher.request`, run by ``read_jobs.ResearchJob``): the queue's quota,
  breaker and call caps apply, and a failure or an empty quota of the research never makes a translation job fail or wait
  (FR-7.5). :meth:`Researcher.after_facts` is the hook a translation job calls once it has read new facts: it only queues
  that job, above the translations still waiting for the series, and swallows every error.
* **A budget** (``research_max_calls_per_series``, FR-7.6): every call of the ``research`` task made for the series, counted from
  ``llm_calls`` over its whole life. A run stops *before* the call that would pass it (event ``research.budget_reached``, the
  job ends ``done``); nothing is lost that an earlier call brought.
* **Without a web search** (an OpenAI-compatible provider) nothing is queued and a run that is made anyway is skipped, never a
  failure (FR-7.3).
* **Sources are the provider's, never the model's.** A page comes from the grounding metadata; Gemini's own redirect link is
  followed once to the page's address (:mod:`subserver.adapters.redirects`), and when that fails the site's name stands in
  (``https://site/``), so "two independent sources" still counts sites correctly.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from subcore.providers import GroundingSource, Provider
from subcore.research import MAX_TERMS, RESEARCH_PROMPT_VERSION, ResearchError, research_terms
from subcore.suggest import TermItem, term_items

from ..adapters.telemetry import NULL
from ..domain import queue_policy as policy
from ..domain.config import PROVIDER_GEMINI, model_for_task, provider_name
from ..domain.ports import LinkResolver, Telemetry
from ..domain.suggestion_keys import identity_key
from ..domain.translation import redact
from ..jobs.jobs import RESEARCH_CALLS
from ..jobs.queue import JobQueue
from .estimates import Estimator
from .memory import unit_label
from .proposals import FactsProposer
from .suggestions import Proposal, normalise

SKIP_DAYS = 7  # a term the web had no form for is left out of the runs of this many days
SKIP_KIND = "research_term"  # the identity of a skip: apart from the facts generator's, which skips by its own rules
AUTO_PRIO = policy.INTERACTIVE + 1  # above every translation still waiting for the series, the admin's own included
ProviderFor = Callable[[str], Provider]

# Why a run cannot be made now. ``""`` means it can.
BLOCKED_NO_SEARCH, BLOCKED_BUDGET, BLOCKED_NO_TERMS = "no_web_search", "budget", "no_terms"
_EXPLAIN = {
    BLOCKED_NO_SEARCH: "Web research needs Gemini: an OpenAI-compatible provider has no search tool",
    BLOCKED_BUDGET: "Research budget reached for this series: raise research_max_calls_per_series in Settings",
    BLOCKED_NO_TERMS: "No terms to look up: none is shared by two episodes without being in the glossary or the inbox already",
}


class ResearchBudgetReached(Exception):
    """The next call would pass ``research_max_calls_per_series``. Not an error of the provider: the run ends, ``done``."""


def _fold(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _on(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "on")


class _Counted:
    """A provider that counts its calls against the series' budget before making each, and stops when it is spent."""

    def __init__(self, inner: Provider, budget: "_Budget") -> None:
        self.inner, self.budget = inner, budget

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def caps(self):
        return self.inner.caps

    def count_tokens(self, req):
        return self.inner.count_tokens(req)

    def generate(self, req):
        self.budget.spend()
        return self.inner.generate(req)


class _Budget:
    def __init__(self, cap: int, used: int) -> None:
        self.cap, self.used, self.made = cap, used, 0  # cap 0 = no cap

    def spend(self) -> None:
        if self.cap > 0 and self.used + self.made >= self.cap:
            raise ResearchBudgetReached(f"{self.used + self.made} of {self.cap} research calls are used")
        self.made += 1


class Researcher:
    def __init__(
        self,
        proposer: FactsProposer,
        *,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        telemetry: Telemetry | None = None,
        resolver: LinkResolver | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.proposer = proposer
        self._settings = settings_source or (lambda: {})
        self.telemetry: Telemetry = telemetry or NULL
        self.resolver = resolver
        self._clock = clock

    # -- what there is to look up ------------------------------------------------------------------------------
    @property
    def _store(self):
        return self.proposer.suggestions.store

    @property
    def jobs(self) -> JobQueue | None:
        return self.proposer.jobs

    def candidates(self, series_id: int, lang: str) -> list[TermItem]:
        """The terms two or more episodes share that the glossary does not have, minus what the inbox already holds (in any
        status, from any source) and what the web recently had no form for. Reads only; most widespread first."""
        found = self.proposer.inputs(series_id, lang)
        held: set[tuple[str, ...]] = set()
        keys = {row.identity or identity_key(row.kind, row.payload) for row in self._store.suggestions(series_id=series_id, target_lang=lang, limit=10_000)}
        keys |= self._store.suggestion_skips(series_id, lang, RESEARCH_PROMPT_VERSION, self._clock() - SKIP_DAYS * 86400)
        for key in keys:
            try:
                kind, *parts = json.loads(key)
            except ValueError:
                continue
            if kind in ("term", SKIP_KIND):
                held.add(tuple(parts))
        return term_items(found.episodes, found.cues_of, glossary=found.glossary, exclude=held, limit=1000)

    # -- the budget ----------------------------------------------------------------------------------------------
    def cap(self, settings: dict[str, Any] | None = None) -> int:
        try:
            return max(0, int((settings if settings is not None else self._settings()).get("research_max_calls_per_series", 10)))
        except (TypeError, ValueError):
            return 10

    def used(self, series_id: int) -> int:
        return self._store.calls_for_series(series_id, "research")

    # -- may a run be made, and what would it cost -----------------------------------------------------------------
    def blocked(self, series_id: int, lang: str, items: list[TermItem] | None = None) -> str:
        """Why a run cannot be made now (one of the ``BLOCKED_*``), or ``""``. The one answer the estimate, the button, the
        request and the hook all agree on."""
        settings = self._settings()
        if provider_name(settings) != PROVIDER_GEMINI:
            return BLOCKED_NO_SEARCH
        cap = self.cap(settings)
        if cap > 0 and cap - self.used(series_id) < RESEARCH_CALLS:  # a search with no call left to read it is a waste
            return BLOCKED_BUDGET
        return BLOCKED_NO_TERMS if not (items if items is not None else self.candidates(series_id, lang)) else ""

    @staticmethod
    def explain(blocked: str) -> str:
        return _EXPLAIN.get(blocked, blocked)

    def estimate(self, series_id: int, lang: str) -> dict[str, Any]:
        """What a run would ask and spend (``estimate_only``): reads, calls no model, writes nothing."""
        settings = self._settings()
        items = self.candidates(series_id, lang)
        blocked = self.blocked(series_id, lang, items)
        cap, used = self.cap(settings), self.used(series_id)
        calls = RESEARCH_CALLS if items and not blocked else 0
        out: dict[str, Any] = {
            "ok": True, "estimate_only": True, "terms": len(items), "asked": min(len(items), MAX_TERMS), "calls": calls,
            "search_model": model_for_task(settings, "research"), "read_model": model_for_task(settings, "extract"),
            "budget": {"cap": cap, "used": used, "after": used + calls},
            "auto_accept": _on(settings.get("auto_accept_terms", True)),
            "blocked": blocked, "reason": self.explain(blocked) if blocked else "",
            "calls_left_today": None, "paused_until": None, "minutes": None, "finish_at": None,
        }
        if self.jobs is not None:
            known = Estimator(self._store, self.jobs, self._settings, clock=self._clock).of(1 if calls else 0, 0, calls)
            out.update(calls_left_today=known.calls_left_today, paused_until=known.paused_until, minutes=known.minutes, finish_at=known.finish_at)
        return out

    # -- asking for a run: a job, never a thread of the caller's -----------------------------------------------------
    def request(
        self, series_id: int, lang: str, *, manual: bool = True, auto_accept: bool | None = None,
    ) -> tuple[str, bool]:
        """Queue the run for this series and language as a ``research`` job (FR-7.5): the queue runs it under the day's quota
        and the breaker, one open per series and language. The admin's own request is the interactive class; the one a
        translation leaves behind (``manual=False``) is above the translations still waiting for the series. Returns
        ``(job id, whether it is new)``."""
        if self.jobs is None:
            raise ValueError("no job queue is wired into the research step")
        series = self.proposer.memory.units.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        if manual:
            return self.jobs.enqueue_research(
                series_id, lang, title=series.title, bazarr_id=series.bazarr_id or "", auto_accept=auto_accept,
            )
        return self.jobs.enqueue_research(
            series_id, lang, title=series.title, bazarr_id=series.bazarr_id or "", origin="facts", prio=AUTO_PRIO,
        )

    # -- the hook of a translation job ---------------------------------------------------------------------------------
    def after_facts(self, unit_ids: list[int], lang: str, settings: dict[str, Any]) -> None:
        """Facts of these units were just extracted: when ``research_enabled`` is on, queue a research job for their series if
        there is something to look up and budget to do it. It only makes a job (the run happens on its own turn and budget)
        and never raises, so a research that cannot be queued costs the translation job nothing (FR-7.5)."""
        if not unit_ids or not lang or not _on(settings.get("research_enabled", False)):
            return
        try:
            if provider_name(settings) != PROVIDER_GEMINI:
                return
            series_ids = {u.series_id for u in (self.proposer.memory.units.get_unit(i) for i in unit_ids) if u and u.series_id}
            for series_id in sorted(series_ids):
                if not self.blocked(series_id, lang):
                    self.request(series_id, lang, manual=False)
        except Exception as exc:  # noqa: BLE001 - by contract nothing here may reach the job
            message = redact(f"{type(exc).__name__}: {exc}", settings)
            logging.warning("Could not queue the research of a series (%s)", message)
            self.telemetry.event(
                "research.queue_failed", reason="the research could not be queued; the translation job is not affected",
                lang=lang, error=message[:300],
            )

    # -- running -----------------------------------------------------------------------------------------------------
    def run(
        self, series_id: int, lang: str, provider_for: ProviderFor, report: dict[str, Any] | None = None,
        *, auto_accept: bool | None = None,
    ) -> dict[str, Any]:
        """Look the terms up (two calls) and put what the web backs in the inbox. ``report`` (when given) is filled in as the
        run goes, so what was stored shows even when a call fails after it. Provider errors propagate, typed."""
        report = report if report is not None else {}
        report.update({
            "ok": True, "terms": 0, "calls": 0, "pages": 0, "added": 0, "auto_accepted": 0, "queries": [], "errors": [],
            "budget_reached": False, "skipped": "",
        })
        settings = self._settings()
        if provider_name(settings) != PROVIDER_GEMINI:
            report["skipped"] = BLOCKED_NO_SEARCH  # FR-7.3: no search tool, no research, and no fault
            return report
        found = self.proposer.inputs(series_id, lang)
        items = self.candidates(series_id, lang)[:MAX_TERMS]
        report["terms"] = len(items)
        if not items:
            return report
        title = found.owner.title
        budget = _Budget(self.cap(settings), self.used(series_id))
        if budget.cap > 0 and budget.cap - budget.used < RESEARCH_CALLS:  # a search with no call left to read it is a waste
            report["budget_reached"] = True
            self._budget_event(title, series_id, budget)
            return report
        search, read = _Counted(provider_for("research"), budget), _Counted(provider_for("extract"), budget)
        try:
            outcome = research_terms(search, read, items, lang=lang, title=title)
        except ResearchBudgetReached:
            report["budget_reached"] = True
            report["calls"] = budget.made
            self._budget_event(title, series_id, budget)
            return report
        except ResearchError as exc:  # nothing usable came back: not a reason to remember the terms as unanswerable
            report["errors"].append(str(exc))
            report["calls"] = budget.made
            return report
        report.update(calls=budget.made, pages=outcome.pages, queries=list(outcome.queries))
        unit_ids = {unit_label(u): u.id for u, _ in found.stored}
        pages: dict[str, str] = {}  # the links of this run, each followed once
        proposals: list[Proposal] = []
        display_id = 0  # made on the first proposal: a search whose result is not used has nothing to show
        for draft in outcome.drafts:
            links = list(dict.fromkeys(url for url in (self._link(s, pages) for s in draft.sources) if url))
            try:
                normalise("term", draft.payload)
            except ValueError:
                continue
            if links:  # a proposal with no page behind it is not research
                evidence = tuple(
                    {**e, "unit_id": unit_ids[str(e.get("episode"))]} if str(e.get("episode")) in unit_ids else e
                    for e in draft.evidence
                )
                if not display_id and outcome.search_suggestions:
                    display_id = self._store.search_display_add(series_id, lang, outcome.search_suggestions, outcome.queries, now=self._clock())
                proposals.append(Proposal("term", draft.payload, evidence=evidence, sources=tuple(links), display_id=display_id))
        if proposals:
            stored = self.proposer.suggestions.propose(series_id, lang, proposals, source="research", auto_accept=auto_accept)
            report["added"], report["auto_accepted"] = stored.added, stored.auto_accepted
            report["outcome"] = stored.to_json()
            if display_id and not stored.added:  # every proposal was already in the inbox: nothing points at this display
                self._store.search_display_drop(display_id)
        proposed = {_fold(p.payload["source"]) for p in proposals}
        # What the web had no form for is remembered, or it would head the list of every run.
        self._store.suggestion_skip(
            series_id, lang, [json.dumps([SKIP_KIND, _fold(i.source), ""], ensure_ascii=False) for i in items if _fold(i.source) not in proposed],
            RESEARCH_PROMPT_VERSION, at=self._clock(),
        )
        self.telemetry.event(
            "research.finished",
            reason=f"{budget.made} model call(s) looked up {len(items)} term(s) of {title} for {lang}: {report['added']} proposed",
            series=title, lang=lang, terms=len(items), calls=budget.made, pages=outcome.pages, added=report["added"],
            auto_accepted=report["auto_accepted"],
        )
        return report

    def _link(self, source: GroundingSource, done: dict[str, str]) -> str:
        """The address of the page a grounding source stands for: what the resolver says (a real address as it is, Google's
        redirect followed one hop), else the site's name as an address (``https://site/``), else nothing."""
        if source.url not in done:
            page = self.resolver.resolve(source.url) if self.resolver is not None else None
            done[source.url] = page or (f"https://{source.domain}/" if source.domain else "")
        return done[source.url]

    def _budget_event(self, title: str, series_id: int, budget: _Budget) -> None:
        self.telemetry.event(
            "research.budget_reached", level="WARNING",
            reason=f"{title or 'the series'} has used {budget.used + budget.made} of its {budget.cap} research calls; the run stops here",
            series=title, series_id=series_id, cap=budget.cap, used=budget.used + budget.made,
        )


__all__ = ["BLOCKED_BUDGET", "BLOCKED_NO_SEARCH", "BLOCKED_NO_TERMS", "ResearchBudgetReached", "Researcher"]
