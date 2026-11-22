"""The bootstrap of ``auto_request`` (P13; SPEC FR-15.4, FR-15.12; ARCHITECTURE ADR-10).

A series with nothing approved yet would be translated start to finish without anyone having looked at its names, its
forms of address or its characters. So while it has no glossary, no character card and no decided suggestion, the
policy asks for only its first ``auto_request_bootstrap`` episodes (story order) and leaves the rest Missing, with the
readable reason ``bootstrap`` on the Library (it is a reason, never a job state). The series is let go

* when the admin presses **Continue** (``series.bootstrap_released``), or
* when it has had something approved and nothing waits for review: a glossary or a card exists and no suggestion is
  ``pending``, or every suggestion has been decided.

An admin's own request is never held back: only the policy waits. ``auto_request_bootstrap = 0`` switches the wait off.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

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
        targets: Collection[str] | None = None,
    ) -> BootstrapState:
        """The state of one catalog series; ``episodes`` are the ones the catalog lists for it. With ``targets`` (the
        enabled target languages) the first episodes are the first that still *need* a translation, plus the ones this
        worker already translated (they keep their place, so the wait does not move on by itself): an episode that
        already has a subtitle from elsewhere is not one of them (R14-2)."""
        limit = self.limit()
        stored = stored if stored is not None else self.store.series_by_key(series.series_key)
        seed = self.store.series_seed(safe_name(series.series_key), stored.id if stored else None)
        released = bool(stored and stored.bootstrap_released)
        if not is_locked(limit=limit, released=released, seed=seed):
            return BootstrapState(limit=limit, pending=seed.pending, released=released)
        pool = [e for e in episodes if e.id is not None]
        if targets is not None:
            wanted = {code.lower() for code in targets}
            ours = self.store.translated_episodes(series.series_key)
            pool = [e for e in pool if wanted & set(e.missing) or (e.season, e.episode) in ours]
        first = story_order(pool)[:limit]
        return BootstrapState(
            locked=True, limit=limit, allowed=frozenset(e.id for e in first if e.id is not None), pending=seed.pending,
        )
