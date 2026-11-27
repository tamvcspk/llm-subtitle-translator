"""The bootstrap of ``auto_request`` (P13; SPEC FR-15.4, FR-15.12; ARCHITECTURE ADR-10).

A series with nothing approved yet would be translated start to finish without anyone having looked at its names, its
forms of address or its characters. So while it has no glossary, no character card and no decided suggestion, the
policy asks for only its first ``auto_request_bootstrap`` episodes (story order) and leaves the rest Missing, with the
readable reason ``bootstrap`` on the Library (it is a reason, never a job state). The series is let go

* when the admin releases it after the Estimate box (``series.bootstrap_released``), or
* when it has had something approved and nothing waits for review: a glossary or a card exists and no suggestion is
  ``pending``, or every suggestion has been decided.

An admin's own request is never held back: only the policy waits. ``auto_request_bootstrap = 0`` switches the wait off.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from .catalog import pick_source
from .glossaries import safe_name
from ..domain.ports import CatalogEntry, Series, SeriesSeed
from ..adapters.store import Store


@dataclass(frozen=True)
class BootstrapState:
    """Where a series stands: held (``locked``), how many first episodes it may have meanwhile (``limit``), which
    ones (``allowed``, catalog ids), and how many suggestions wait for review (``pending``)."""

    locked: bool = False
    limit: int = 0
    allowed: frozenset[int] = field(default_factory=frozenset)
    pending: int = 0
    released: bool = False
    #: Would holding the series again hold anything? (``is_locked`` with ``released = false``.) A series that has had
    #: suggestions decided and none waiting is let go whatever the admin says, so "Hold again" there would do nothing.
    relockable: bool = False
    #: Nothing approved yet: no glossary entry and no character card (the Estimate box warns about it).
    glossary_empty: bool = True


def is_locked(*, limit: int, released: bool, seed: SeriesSeed) -> bool:
    """Pure: is a series held back? Not when the wait is off, not after Continue, not once something was approved and
    nothing is left to review. A series that has had suggestions decided and none left waiting is let go too."""
    if limit <= 0 or released:
        return False
    approved = seed.glossary or seed.cards > 0
    if not approved and seed.decided == 0:
        return True  # nothing has been looked at yet
    return seed.pending > 0 and not approved  # something was rejected, the rest still waits, nothing was approved


def story_order(episodes: list[CatalogEntry]) -> list[CatalogEntry]:
    """Season 0 (specials) sits outside the chain: after every numbered episode; an unnumbered one last of all."""
    def key(entry: CatalogEntry) -> tuple[int, int, int, int]:
        if entry.season is None or entry.episode is None:
            return (2, 0, 0, entry.id or 0)
        return (1 if entry.season == 0 else 0, entry.season, entry.episode, entry.id or 0)

    return sorted(episodes, key=key)


class Bootstrap:
    def __init__(self, store: Store, settings_source: Callable[[], dict[str, Any]]) -> None:
        self.store = store
        self._settings = settings_source

    def limit(self) -> int:
        try:
            return max(0, int(self._settings().get("auto_request_bootstrap") or 0))
        except (TypeError, ValueError):
            return 0

    def state(
        self, series: CatalogEntry, episodes: list[CatalogEntry], stored: Series | None = None,
        targets: Collection[str] | None = None, sources: list[dict[str, Any]] | None = None, *,
        released: bool | None = None,
    ) -> BootstrapState:
        """The state of one catalog series; ``episodes`` are the ones the catalog lists for it. With ``targets`` (the
        enabled target languages) the first episodes are the first that still *need* a translation, plus the ones this
        worker already translated (they keep their place, so the wait does not move on by itself): an episode that
        already has a subtitle from elsewhere is not one of them (R14-2).

        The episodes it lets through are only ones the policy can actually ask for: with ``sources`` (the enabled source
        languages) one that has a source subtitle, and never one that is ignored, removed or whose last job failed or
        was cancelled (``policy_blocked``, rule D4), or the series would be held on episodes that never get a job
        (FR-15.12). ``released`` overrides the stored choice: "what would be held if the admin held it again?"."""
        limit = self.limit()
        stored = stored if stored is not None else self.store.series_by_key(series.series_key)
        seed = self.store.series_seed(safe_name(series.series_key), stored.id if stored else None)
        if released is None:
            released = bool(stored and stored.bootstrap_released)
        common = dict(
            limit=limit, pending=seed.pending, released=released,
            relockable=is_locked(limit=limit, released=False, seed=seed),
            glossary_empty=not (seed.glossary or seed.cards > 0),
        )
        if not is_locked(limit=limit, released=released, seed=seed):
            return BootstrapState(**common)
        pool = [e for e in episodes if e.id is not None]
        if targets is not None:
            wanted = {code.lower() for code in targets}
            ours = self.store.translated_episodes(series.series_key)
            blocked = self.store.policy_blocked(series.series_key)

            def needs(entry: CatalogEntry) -> bool:
                open_langs = wanted & set(entry.missing)
                if sources is not None and pick_source(entry, sources) is None:
                    return False  # no source subtitle: it can only be Missing, never requested
                return any((entry.season, entry.episode, lang) not in blocked for lang in open_langs)

            pool = [e for e in pool if needs(e) or (e.season, e.episode) in ours]
        first = story_order(pool)[:limit]
        return BootstrapState(locked=True, allowed=frozenset(e.id for e in first if e.id is not None), **common)
