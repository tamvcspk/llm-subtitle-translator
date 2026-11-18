"""Suggestions and the Review inbox (P13; SPEC FR-4.4, FR-11.1, FR-11.1.5; ARCHITECTURE section 3.6).

Every generator (facts, story memory, the feedback loop, research) hands the service a list of :class:`Proposal`; the
service is the one place that stores them, decides which are new, applies the ones that are accepted and undoes them.
A source has no review logic of its own: ``source`` is only a label on the row.

* **Fingerprint.** The hash of ``(series, language, kind, normalised content)``. A series keeps one row per
  fingerprint, so what was rejected is never proposed again and what was accepted is skipped (FR-11.1.4).
* **Accepting** writes the entry into the glossary in the database (a character also writes its card, in the same
  transaction) through the same documents the Glossary page saves, and says how many translations that makes stale
  (``rules.py`` hashes, the same ``impact_units`` the Glossary page uses). It never retranslates (FR-5.6).
* **auto_accept** (``auto_accept_terms`` / ``auto_accept_address_rules`` / ``auto_accept_characters``) goes through
  the same accept; the row is marked ``auto_accepted`` and shows in the history, where it can be undone.
* **Undo** puts back exactly what the accept replaced, and only while the glossary still has the entry as it was
  written: a later edit is never overwritten.

``LookupError`` means "not found", ``ValueError`` means "bad request" (the HTTP layer maps them to 404 and 400).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Callable
from urllib.parse import urlparse

from subcore import glossary as g
from subcore.style import CharacterCard, StyleError

from .config import SUGGESTION_BOOL_CONFIG_KEYS
from .library import Library, Owner
from .ports import (
    SUGGESTION_ACCEPTED, SUGGESTION_PENDING, SUGGESTION_REJECTED, SUGGESTION_STATUSES, Suggestion, Telemetry,
)
from .store import Store
from .telemetry import NULL

SOURCES = ("facts", "memory", "feedback", "research")
KINDS = ("term", "address_rule", "character")
SECTION = {"term": "terms", "address_rule": "address_rules", "character": "characters"}
AUTO_SETTING = {
    "term": "auto_accept_terms", "address_rule": "auto_accept_address_rules", "character": "auto_accept_characters",
}
MIN_INDEPENDENT_SOURCES = 2  # FR-4.4: a term is applied by itself only when this many independent sources agree
MAX_LISTED = 1000
_LANG = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass(frozen=True)
class Proposal:
    """What a generator proposes: a kind, its content, and why (cues with timecodes, or URLs of a research answer)."""

    kind: str
    payload: dict[str, Any]
    evidence: tuple[Any, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposeReport:
    added: int = 0
    auto_accepted: int = 0
    pending_already: int = 0  # the same content is waiting in the inbox
    accepted_before: int = 0
    rejected_before: int = 0  # the admin said no to exactly this: it is not proposed again
    in_glossary: int = 0  # the glossary already says it
    invalid: tuple[str, ...] = field(default=())
    failed_to_apply: tuple[str, ...] = field(default=())

    def to_json(self) -> dict[str, Any]:
        return {
            "added": self.added, "auto_accepted": self.auto_accepted, "pending_already": self.pending_already,
            "accepted_before": self.accepted_before, "rejected_before": self.rejected_before,
            "in_glossary": self.in_glossary, "invalid": list(self.invalid), "failed_to_apply": list(self.failed_to_apply),
        }


def normalise(kind: str, payload: Any) -> dict[str, Any]:
    """The content of a suggestion as the glossary would store it, or a ``ValueError`` naming what is wrong. A
    character may carry the card of its voice (``card``); the card takes the character's name."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    try:
        if kind == "character":
            entry = {k: v for k, v in payload.items() if k != "card"}
            doc = g.to_doc(g.from_doc({"characters": [entry]}))["characters"][0]
            card = payload.get("card")
            if card is not None:
                if not isinstance(card, dict):
                    raise ValueError("card must be an object")
                doc = {**doc, "card": CharacterCard.from_doc({**card, "name": doc["name"]}, "card").to_doc()}
            return doc
        section = SECTION[kind]
        return g.to_doc(g.from_doc({section: [payload]}))[section][0]
    except (g.GlossaryError, StyleError) as exc:
        raise ValueError(str(exc)) from None


def _fold_text(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return [_fold_text(v) for v in value]
    if isinstance(value, dict):
        return {k: _fold_text(v) for k, v in value.items()}
    return value


def fingerprint(series_id: int, lang: str, kind: str, payload: dict[str, Any]) -> str:
    """Hash of what a suggestion says, blind to case and spacing: ``Anh`` and ``anh `` are the same proposal."""
    text = json.dumps([series_id, lang, kind, _fold_text(payload)], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def independent_sources(sources: Any) -> int:
    """How many different places a research answer came from: a URL counts by its host (``www.`` is not another one)."""
    seen: set[str] = set()
    for item in sources or ():
        text = item.strip() if isinstance(item, str) else ""
        if not text:
            continue
        host = urlparse(text).netloc.lower().removeprefix("www.") if "//" in text else text.casefold()
        seen.add(host or text.casefold())
    return len(seen)


def _identity(kind: str, entry: dict[str, Any]) -> tuple[str, ...]:
    """What makes two entries of a glossary the same one (the keys of ``Glossary.with_term`` and its siblings)."""
    if kind == "term":
        return (str(entry["source"]).casefold(), str(entry.get("from") or ""))
    if kind == "address_rule":
        return (str(entry["a"]).casefold(), str(entry["b"]).casefold(), str(entry.get("from") or ""))
    return (str(entry["name"]).casefold(),)


@dataclass
class _Folded:
    """The glossary document and the cards of one owner and language after some suggestions are laid on them."""

    doc: dict[str, Any]
    cards: dict[str, CharacterCard]  # by casefolded name
    records: list[dict[str, Any]]  # per suggestion: what it replaced and what it wrote (the undo record)
    cards_changed: bool = False

    @property
    def unchanged(self) -> bool:
        return all(r.get("noop") for r in self.records)


class SuggestionService:
    def __init__(
        self,
        store: Store,
        library: Library,
        *,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.store, self.library = store, library
        self._settings = settings_source or (lambda: {})
        self.telemetry: Telemetry = telemetry or NULL
        self._lock = threading.RLock()  # a glossary is read, changed and written back: one change at a time
        # The generator the admin can ask for by hand (P14, ``FactsProposer``); wired by the composition root.
        self.generator: Any = None
        self.tasks: Any = None  # BackgroundTasks, when the worker has them: a request may run after its answer

    def generate(
        self, series_id: int, lang: str, *, estimate_only: bool = False, background: bool = False,
    ) -> dict[str, Any]:
        """Ask the facts generator for proposals now (``POST /api/suggestions/generate``). ``estimate_only`` says what
        it would ask without spending a call; ``background`` answers at once with a task that Activity follows."""
        if self.generator is None:
            raise ValueError("no proposal generator is wired into this worker")
        if not _LANG.match(lang or ""):
            raise ValueError(f"invalid language code {lang!r}")
        owner = self.library.series_owner(series_id)  # LookupError for a series that is not there
        if not background or estimate_only:
            return self.generator.generate(series_id, lang, estimate_only=estimate_only)
        if self.tasks is None:
            raise ValueError("background work is not available in this worker")

        def work(progress: Callable[..., None]) -> dict[str, Any]:
            progress(0, 1, "asking the model")
            report = self.generator.generate(series_id, lang)
            progress(1, 1, "")
            return report

        task = self.tasks.submit("suggest", f"Look for proposals · {owner.title}", work, series_id=series_id)
        return {"ok": True, "task": task}

    # -- proposing ----------------------------------------------------------------------------------------
    def propose(self, series_id: int, lang: str, proposals: list[Proposal], *, source: str) -> ProposeReport:
        """Store what a generator found. Content already waiting, already decided, or already in the glossary is
        skipped; what is new waits as ``pending`` unless ``auto_accept`` for its kind applies it at once."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        if not _LANG.match(lang or ""):
            raise ValueError(f"invalid language code {lang!r}")
        owner = self.library.series_owner(series_id)
        counts = dict.fromkeys(("added", "auto_accepted", "pending_already", "accepted_before", "rejected_before", "in_glossary"), 0)
        invalid: list[str] = []
        failed: list[str] = []
        for number, proposal in enumerate(proposals):
            try:
                payload = normalise(proposal.kind, proposal.payload)
            except ValueError as exc:
                invalid.append(f"proposal {number}: {exc}")
                continue
            digest = fingerprint(series_id, lang, proposal.kind, payload)
            known = self.store.suggestion_by_fingerprint(series_id, lang, digest)
            if known is not None:
                counts[{SUGGESTION_PENDING: "pending_already", SUGGESTION_ACCEPTED: "accepted_before"}.get(known.status, "rejected_before")] += 1
                continue
            probe = _blank(series_id, lang, proposal.kind, source)
            with self._lock:
                if self._fold(owner, lang, [(probe, payload)]).unchanged:
                    counts["in_glossary"] += 1
                    continue
                stored = self.store.suggestion_add(
                    series_id, lang, proposal.kind, source, digest, payload, list(proposal.evidence), list(proposal.sources),
                )
            if stored is None:  # raced with another proposal of the same content
                counts["pending_already"] += 1
                continue
            counts["added"] += 1
            if self._auto(stored):
                try:
                    self.accept(stored.id, auto=True)
                    counts["auto_accepted"] += 1
                except (ValueError, LookupError) as exc:  # it stays pending: the admin decides
                    failed.append(f"suggestion {stored.id}: {exc}")
        report = ProposeReport(**counts, invalid=tuple(invalid), failed_to_apply=tuple(failed))
        self.telemetry.event(
            "suggestions.proposed", reason=f"a {source} generator proposed {len(proposals)} item(s) for {owner.title}",
            source=source, series=owner.title, lang=lang, **{k: v for k, v in report.to_json().items() if isinstance(v, int)},
        )
        return report

    def _auto(self, suggestion: Suggestion) -> bool:
        key = AUTO_SETTING[suggestion.kind]
        enabled = self._settings().get(key, SUGGESTION_BOOL_CONFIG_KEYS[key])
        if not (enabled is True or str(enabled).strip().lower() in ("1", "true", "on")):
            return False
        if suggestion.kind == "term":  # FR-4.4: only when independent sources agree
            return independent_sources(suggestion.sources) >= MIN_INDEPENDENT_SOURCES
        return True

    # -- reading ------------------------------------------------------------------------------------------
    def listing(
        self, *, series_id: int | None = None, status: str | None = None, source: str | None = None,
        lang: str | None = None, limit: int = 500,
    ) -> dict[str, Any]:
        """Suggestions grouped by series, with how many each status has (``counts``) and each series' own pending count."""
        if status is not None and status not in SUGGESTION_STATUSES:
            raise ValueError(f"status must be one of {', '.join(SUGGESTION_STATUSES)}")
        if source is not None and source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        limit = max(1, min(int(limit), MAX_LISTED))
        titles = {s.id: s.title or s.key for s in self.store.list_series()}
        items = [
            self._view(s, titles)
            for s in self.store.suggestions(series_id=series_id, status=status, source=source, target_lang=lang, limit=limit)
        ]
        return {"items": items, **self._counts(titles)}

    def summary(self) -> dict[str, Any]:
        """What the badge on the nav shows: the pending total and how many each series has."""
        titles = {s.id: s.title or s.key for s in self.store.list_series()}
        return self._counts(titles)

    def _counts(self, titles: dict[int, str]) -> dict[str, Any]:
        counts = dict.fromkeys(SUGGESTION_STATUSES, 0)
        pending: list[dict[str, Any]] = []
        for row in self.store.suggestion_counts():
            counts[row.status] = counts.get(row.status, 0) + row.count
            if row.status == SUGGESTION_PENDING:
                pending.append({"series_id": row.series_id, "series": titles.get(row.series_id, ""), "pending": row.count})
        return {"counts": counts, "pending": counts[SUGGESTION_PENDING], "series": pending}

    @staticmethod
    def _view(s: Suggestion, titles: dict[int, str]) -> dict[str, Any]:
        return {
            "id": s.id, "series_id": s.series_id, "series": titles.get(s.series_id, ""), "lang": s.target_lang,
            "kind": s.kind, "source": s.source, "payload": s.payload, "evidence": s.evidence, "sources": s.sources,
            "status": s.status, "auto_accepted": s.auto_accepted, "decided_at": s.decided_at, "created_at": s.created_at,
            "undoable": s.status == SUGGESTION_ACCEPTED and bool(s.applied) and not s.applied.get("noop"),
            "edited": "edited_from" in s.applied,
        }

    def _get(self, suggestion_id: int) -> Suggestion:
        found = self.store.suggestion(int(suggestion_id))
        if found is None:
            raise LookupError(f"no suggestion {suggestion_id}")
        return found

    # -- deciding -----------------------------------------------------------------------------------------
    def accept(
        self, suggestion_id: int, *, payload: Any = None, dry_run: bool = False, auto: bool = False,
    ) -> dict[str, Any]:
        """Apply a pending suggestion (``payload`` is the content as the admin edited it). ``dry_run`` only says how
        many translations accepting would make stale."""
        with self._lock:
            suggestion = self._get(suggestion_id)
            if suggestion.status != SUGGESTION_PENDING:
                raise ValueError(f"this suggestion was already {suggestion.status}")
            clean = normalise(suggestion.kind, suggestion.payload if payload is None else payload)
            owner = self.library.series_owner(suggestion.series_id)
            folded = self._fold(owner, suggestion.target_lang, [(suggestion, clean)])
            impact = self._impact(owner, suggestion.target_lang, folded)
            if dry_run:
                return {"ok": True, "saved": False, "unchanged": folded.unchanged, **impact}
            self._commit(owner, suggestion.target_lang, folded, note="suggestion")
            applied = dict(folded.records[0])
            if clean != suggestion.payload:
                applied["edited_from"] = suggestion.payload
            decided = self.store.suggestion_decide(suggestion.id, SUGGESTION_ACCEPTED, payload=clean, applied=applied, auto=auto)
        self.telemetry.event(
            "suggestion.accepted" if not auto else "suggestion.auto_accepted",
            reason=(
                f"the {suggestion.source} generator's {suggestion.kind} was applied to the glossary of {owner.title}"
                + (" by auto_accept" if auto else "") + "; translations made without it are flagged stale, not redone"
            ),
            suggestion=suggestion.id, series=owner.title, lang=suggestion.target_lang, suggestion_kind=suggestion.kind,
            source=suggestion.source, edited=clean != suggestion.payload, stale=impact["would_stale"],
        )
        return {"ok": True, "saved": True, "unchanged": folded.unchanged, "suggestion": self._view(decided, {owner.id: owner.title}), **impact}

    def reject(self, suggestion_id: int) -> dict[str, Any]:
        """Say no: the row stays so the same content is never proposed again (FR-11.1.4)."""
        with self._lock:
            suggestion = self._get(suggestion_id)
            if suggestion.status != SUGGESTION_PENDING:
                raise ValueError(f"this suggestion was already {suggestion.status}")
            decided = self.store.suggestion_decide(suggestion.id, SUGGESTION_REJECTED)
        series = self.store.get_series(decided.series_id)
        self.telemetry.event(
            "suggestion.rejected", reason="the admin rejected a suggestion; the same content is not proposed again",
            suggestion=decided.id, series=series.title if series else "", suggestion_kind=decided.kind, source=decided.source,
        )
        return {"ok": True, "suggestion": self._view(decided, {decided.series_id: series.title if series else ""})}

    def undo(self, suggestion_id: int, *, dry_run: bool = False) -> dict[str, Any]:
        """Put back what accepting replaced and return the suggestion to the inbox. Refused when the glossary (or the
        card) was changed after the accept, so a later edit is never overwritten."""
        with self._lock:
            suggestion = self._get(suggestion_id)
            if suggestion.status != SUGGESTION_ACCEPTED:
                raise ValueError("only an accepted suggestion can be undone")
            owner = self.library.series_owner(suggestion.series_id)
            lang, applied = suggestion.target_lang, suggestion.applied
            if not applied or applied.get("noop"):  # it changed nothing: there is nothing to put back
                if not dry_run:
                    self.store.suggestion_decide(suggestion.id, SUGGESTION_PENDING)
                return {"ok": True, "saved": not dry_run, "stale_now": 0, "stale_after": 0, "would_stale": 0}
            doc = g.to_doc(self.library.glossaries.load(owner.kind, owner.key, lang))
            section, after, before = applied["section"], applied["after"], list(applied.get("before") or [])
            items = list(doc.get(section) or [])
            if after not in items:
                raise ValueError("the glossary no longer has this entry as it was accepted; change it on the Glossary page")
            at = items.index(after)
            doc[section] = [*items[:at], *before, *items[at + 1:]]
            if not doc[section]:
                del doc[section]
            cards = {c.name.casefold(): c for c in self.library.styles.cards(owner.kind, owner.key)} if self.library.styles else {}
            card_write: tuple[str, dict[str, Any], str | None] | None = None
            card_delete: str | None = None
            part = applied.get("card")
            if part and not part.get("noop"):
                name = part["name"]
                now = cards.get(name.casefold())
                if now is None or now.to_doc() != part["after"]:
                    raise ValueError(f"the card of {name} was changed after it was accepted; change it on the Characters page")
                if part.get("before") is None:
                    card_delete = now.name
                    del cards[name.casefold()]
                else:
                    card_write = (name, part["before"], None)
                    cards[name.casefold()] = CharacterCard.from_doc(part["before"], "card")
            folded = _Folded(doc=doc, cards=cards, records=[], cards_changed=bool(part and not part.get("noop")))
            impact = self._impact(owner, lang, folded)
            if dry_run:
                return {"ok": True, "saved": False, **impact}
            if card_write is None and card_delete is None:
                self.library.glossaries.save_doc(owner.kind, owner.key, lang, doc, note="undo suggestion")
            else:
                self.library.glossaries.write_character(
                    owner.kind, owner.key, lang, glossary_doc=doc, card=card_write, delete_card=card_delete,
                    note="undo suggestion",
                )
            decided = self.store.suggestion_decide(suggestion.id, SUGGESTION_PENDING)
        self.telemetry.event(
            "suggestion.undone", reason="the admin undid an accepted suggestion; it is back in the inbox",
            suggestion=suggestion.id, series=owner.title, suggestion_kind=suggestion.kind, stale=impact["would_stale"],
        )
        return {"ok": True, "saved": True, "suggestion": self._view(decided, {owner.id: owner.title}), **impact}

    def bulk(
        self, series_id: int, *, lang: str | None = None, expect: Any = None, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Accept every pending suggestion of ONE series (optionally one language). ``expect`` is the number the admin
        saw and confirmed: if the inbox has changed since, nothing is applied. ``dry_run`` counts the translations
        all of them together would make stale."""
        with self._lock:
            owner = self.library.series_owner(series_id)
            pending = self.store.suggestions(
                series_id=series_id, status=SUGGESTION_PENDING, target_lang=lang or None, limit=MAX_LISTED * 10,
            )
            if not dry_run:
                if expect is None:
                    raise ValueError("expect is required: send the number of suggestions the admin confirmed")
                try:
                    expected = int(expect)
                except (TypeError, ValueError):
                    raise ValueError("expect must be a number") from None
                if expected != len(pending):
                    raise ValueError(f"the list changed: {len(pending)} are pending now, not {expected}; nothing was applied")
            if dry_run:
                totals = {"stale_now": 0, "stale_after": 0, "would_stale": 0}
                for language in sorted({s.target_lang for s in pending}):
                    batch = [s for s in pending if s.target_lang == language]
                    folded = self._fold(owner, language, [(s, normalise(s.kind, s.payload)) for s in batch])
                    for key, value in self._impact(owner, language, folded).items():
                        totals[key] += value
                return {"ok": True, "saved": False, "count": len(pending), **totals}
            done, failed = 0, []
            totals = {"would_stale": 0}
            for s in pending:
                try:
                    result = self.accept(s.id)
                    totals["would_stale"] += result["would_stale"]
                    done += 1
                except (ValueError, LookupError) as exc:
                    failed.append({"id": s.id, "error": str(exc)})
        return {"ok": not failed, "saved": True, "count": done, "failed": failed, "series": owner.title, **totals}

    # -- laying suggestions on a glossary ----------------------------------------------------------------------
    def _fold(self, owner: Owner, lang: str, items: list[tuple[Suggestion, dict[str, Any]]]) -> _Folded:
        """The owner's glossary document and cards with ``items`` laid on them in order. Nothing is written."""
        doc = g.to_doc(self.library.glossaries.load(owner.kind, owner.key, lang))
        styles = self.library.styles
        cards = {c.name.casefold(): c for c in styles.cards(owner.kind, owner.key)} if styles else {}
        records: list[dict[str, Any]] = []
        cards_changed = False
        for suggestion, payload in items:
            kind = suggestion.kind
            section = SECTION[kind]
            entry = {k: v for k, v in payload.items() if k != "card"}
            existing = list(doc.get(section) or [])
            before = [e for e in existing if _identity(kind, e) == _identity(kind, entry)]
            record: dict[str, Any] = {"section": section, "before": before, "after": entry}
            if before == [entry]:
                record["noop"] = True
            else:
                doc[section] = [e for e in existing if _identity(kind, e) != _identity(kind, entry)] + [entry]
            card_doc = payload.get("card")
            if card_doc is not None:
                prior = cards.get(str(entry["name"]).casefold())
                part: dict[str, Any] = {
                    "name": entry["name"], "before": prior.to_doc() if prior else None, "after": card_doc,
                }
                if prior is not None and prior.to_doc() == card_doc:
                    part["noop"] = True
                else:
                    cards[str(entry["name"]).casefold()] = CharacterCard.from_doc(card_doc, "card")
                    cards_changed = True
                    record.pop("noop", None)
                record["card"] = part
            records.append(record)
        return _Folded(doc=doc, cards=cards, records=records, cards_changed=cards_changed)

    def _impact(self, owner: Owner, lang: str, folded: _Folded) -> dict[str, int]:
        """How many translations of the owner the folded state would make stale: the same count the Glossary and
        Characters pages show before they save."""
        candidate = g.from_doc(folded.doc)
        layers = None
        if folded.cards_changed:
            cards = tuple(folded.cards.values())
            layers = lambda unit: replace(self.library.style_layers(unit), cards=cards)  # noqa: E731
        return self.library.impact_units(owner.units, lang, glossary=candidate, layers=layers)

    def _commit(self, owner: Owner, lang: str, folded: _Folded, *, note: str) -> None:
        """Write one accepted suggestion: the glossary document and, for a character with a voice, its card, together."""
        if folded.unchanged:
            return
        (record,) = folded.records
        part = record.get("card")
        if part and not part.get("noop"):
            self.library.glossaries.write_character(
                owner.kind, owner.key, lang, glossary_doc=folded.doc, card=(part["name"], part["after"], None), note=note,
            )
        else:
            self.library.glossaries.save_doc(owner.kind, owner.key, lang, folded.doc, note=note)


def _blank(series_id: int, lang: str, kind: str, source: str) -> Suggestion:
    """A suggestion that is not stored, to ask what laying it on a glossary would do."""
    return Suggestion(id=0, series_id=series_id, target_lang=lang, kind=kind, source=source, fingerprint="", payload={})
