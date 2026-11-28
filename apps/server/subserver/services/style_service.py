"""Use cases of the Style page: style layers, character cards, genre presets and the trial of a style (FR-18).

``StyleService`` is what the HTTP layer calls for the style routes (like ``UnitService`` for the library). It owns no
HTTP details: ``LookupError`` means "not found" and ``ValueError`` means "bad request". Saving a style or a card says
first, and without saving, how many translations of the series the change would make stale (FR-18.6); it never
translates anything by itself (FR-5.6). The trial of a style ("Thử style", FR-18.7) translates about twenty cues with
the default model and writes nothing: no file, no version.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable

from subcore import glossary as g
from subcore import srt
from subcore.memory import is_generic_address
from subcore.providers import ProviderError
from subcore.style import CharacterCard, Style, StyleError, resolve_style, language_style
from subcore.translate import TranslationError, run
from subcore.translate.prompt import PROMPT_VERSION

from ..domain.config import DEFAULT_MODEL
from ..domain.gather import gather
from .library import Library, Owner
from ..domain.ports import MissingApiKey, Metadata, ProviderFactory, Telemetry
from ..domain.rules import StyleLayers
from ..adapters.store import Store
from .styles import PresetView, Styles
from ..adapters.telemetry import Telemetry as TelemetrySink
from ..domain.translation import redact

PREVIEW_CUES = 20  # FR-18.7: about twenty sample cues
PREVIEW_MAX_CUES = 40  # what a hand-picked list may hold
PREVIEW_NEIGHBOURS = 2  # lines around each sample, sent as context so who speaks to whom is still visible
PREVIEW_TIMEOUT_SECONDS = 300


def _preset_json(p: PresetView) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "genres": list(p.genres), "style": p.style.to_doc(), "builtin": p.builtin}


def _card_json(card: CharacterCard) -> dict[str, Any]:
    return card.to_doc()


def _refuse_new_generic_aliases(name: str, aliases: Any, before: Any, lang: str | None) -> None:
    """A form of address ("Mom", "Sir") is not another name for a character (D-02, D-04). Only an alias that is new
    against what was stored for the same character is refused, so data written before P19b can still be saved as it is."""
    held = {str(a).casefold() for a in before}
    for alias in aliases:
        if str(alias).casefold() not in held and is_generic_address(str(alias), lang):
            raise ValueError(
                f"{alias!r} is a form of address, not another name for {name}: it changes with who is speaking, "
                "so it would send every other \"" + str(alias) + "\" to this character"
            )


def pick_samples(texts: list[str], count: int) -> list[int]:
    """Ordinals (1-based) of ``count`` cues spread evenly over an episode, skipping cues without text and one-word
    interjections when there are enough others. Pure."""
    candidates = [n for n, t in enumerate(texts, start=1) if t.strip()]
    wordy = [n for n in candidates if len(texts[n - 1].split()) >= 3]
    pool = wordy if len(wordy) >= count else candidates
    if len(pool) <= count:
        return pool
    step = len(pool) / count
    return [pool[int(i * step)] for i in range(count)]


class StyleService:
    def __init__(
        self,
        library: Library,
        styles: Styles,
        *,
        metadata: Metadata | None = None,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        provider_factory: ProviderFactory | None = None,
        telemetry: Telemetry | None = None,
        usage: Store | None = None,
    ) -> None:
        self.library, self.styles = library, styles
        self.catalog = library.catalog
        self.metadata = metadata
        self._settings = settings_source or (lambda: {})
        self._provider_factory = provider_factory
        self.telemetry: Telemetry = telemetry or TelemetrySink(usage=usage, synchronous=True)

    # -- lookups ------------------------------------------------------------------------
    def _series(self, series_id: int):
        series = self.catalog.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        return series

    def _layers_with(self, unit, preset: str | None, style: Style | None) -> StyleLayers:
        """The unit's stored layers with the series' preset and style swapped for a candidate."""
        return self.library.layers_with(unit, preset, style)

    # -- the series' style -----------------------------------------------------------------------
    def overview(self, series_id: int, lang: str = "vi") -> dict[str, Any]:
        """Everything the Style page shows: the series' preset and style, what is in force once the language and the
        preset are laid under it, the presets to choose from and the characters' cards. No network call is made."""
        return self.owner_overview(self.library.series_owner(series_id), lang)

    def movie_overview(self, unit_id: int, lang: str = "vi") -> dict[str, Any]:
        """The same for a movie (P12b): its own preset, style and cards; the answer names it under ``movie``."""
        return self.owner_overview(self.library.movie_owner(unit_id), lang)

    def owner_overview(self, owner: Owner, lang: str) -> dict[str, Any]:
        preset_id, style = self.styles.owner_style(owner.kind, owner.key)
        preset = self.styles.preset(preset_id) if preset_id else None
        effective = resolve_style([language_style(lang), preset.style if preset else None, style])
        return {
            owner.label: {"id": owner.id, "title": owner.title}, "lang": lang,
            "preset": preset_id if preset else "", "style": style.to_doc(), "effective": effective.to_doc(),
            "presets": [_preset_json(p) for p in self.styles.presets()],
            "cards": [_card_json(c) for c in self.styles.cards(owner.kind, owner.key)],
        }

    def save_style(self, series_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Save the series' preset and style (``dry_run`` only counts what it would make stale)."""
        return self.save_owner_style(self.library.series_owner(series_id), body)

    def save_movie_style(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self.save_owner_style(self.library.movie_owner(unit_id), body)

    def save_owner_style(self, owner: Owner, body: dict[str, Any]) -> dict[str, Any]:
        # The style is the owner's, not one language's: the count is over every language it has translations in.
        preset_id = str(body.get("preset") or "")
        style_doc = body.get("style") or {}
        if not isinstance(style_doc, dict):
            raise ValueError("style must be an object")
        style = _style(style_doc)
        if preset_id and self.styles.preset(preset_id) is None:
            raise LookupError(f"no preset {preset_id!r}")
        impact = self.library.impact_units(
            owner.units, None, layers=lambda unit: self._layers_with(unit, preset_id, style),
        )
        if not body.get("dry_run"):
            self.styles.save_owner_style(owner.kind, owner.key, preset_id, style.to_doc())
            self.telemetry.event(
                "style.saved",
                reason=f"the admin changed the style of a {owner.label}; translations made without it are flagged stale, not redone",
                **{owner.label: owner.title}, preset=preset_id or None, stale=impact["would_stale"],
            )
        return {"ok": True, "saved": not body.get("dry_run"), **impact}

    def suggest(self, series_id: int) -> dict[str, Any]:
        """Presets that fit the series' genres on TMDB (FR-18.4). Asks TMDB, so the page calls it on demand."""
        series = self._series(series_id)
        genres: tuple[str, ...] = ()
        if self.metadata is not None:
            genres = tuple(self.metadata.genres({"media_type": "series", "series_id": series.bazarr_id}))
        return {"genres": list(genres), "presets": [_preset_json(p) for p in self.styles.suggest(genres)]}

    # -- presets ------------------------------------------------------------------------------------
    def presets(self) -> dict[str, Any]:
        return {"items": [_preset_json(p) for p in self.styles.presets()]}

    def save_preset(self, preset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Edit a preset. Every series and movie that uses it has its translations flagged stale, so the answer says
        how many (in every language) and ``dry_run`` only counts them (FR-18.6)."""
        if self.styles.preset(preset_id) is None:
            raise LookupError(f"no preset {preset_id!r}")
        style_doc = body.get("style") or {}
        if not isinstance(style_doc, dict):
            raise ValueError("style must be an object")
        genres = body.get("genres") or []
        if not isinstance(genres, list):
            raise ValueError("genres must be a list")
        style = _style(style_doc)
        impact = self.library.preset_impact(preset_id, style)
        if body.get("dry_run"):
            return {"ok": True, "saved": False, **impact}
        try:
            saved = self.styles.save_preset(preset_id, str(body.get("name") or preset_id), style_doc, [str(g) for g in genres])
        except StyleError as exc:
            raise ValueError(str(exc)) from None
        self.telemetry.event(
            "style.preset_saved", preset=preset_id, stale=impact["would_stale"], owners=impact["owners"],
            reason="the admin edited a preset; translations of the series that use it are flagged stale, not redone",
        )
        return {**_preset_json(saved), "ok": True, "saved": True, **impact}

    def clone_preset(self, preset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        return _preset_json(self.styles.clone_preset(preset_id, name))

    def delete_preset(self, preset_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Delete a preset the admin made; a series that named it loses the layer, so the answer counts what that
        makes stale and ``dry_run`` only counts."""
        existing = self.styles.preset(preset_id)
        if existing is None:
            raise LookupError(f"no preset {preset_id!r}")
        if existing.builtin:
            raise ValueError("a built-in preset cannot be deleted; edit it instead")
        impact = self.library.preset_impact(preset_id, None)
        if (body or {}).get("dry_run"):
            return {"ok": True, "saved": False, **impact}
        if not self.styles.delete_preset(preset_id):
            raise LookupError(f"no preset {preset_id!r}")
        self.telemetry.event(
            "style.preset_deleted", preset=preset_id, stale=impact["would_stale"], owners=impact["owners"],
            reason="the admin deleted a preset; the series that used it lose that layer and are flagged stale, not redone",
        )
        return {"ok": True, "saved": True, **impact}

    # -- character cards -----------------------------------------------------------------------------
    def save_card(self, series_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Create or replace one card (``replaces`` renames). ``dry_run`` only counts the translations it would make stale."""
        return self.save_owner_card(self.library.series_owner(series_id), body)

    def save_movie_card(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self.save_owner_card(self.library.movie_owner(unit_id), body)

    def save_owner_card(self, owner: Owner, body: dict[str, Any]) -> dict[str, Any]:
        doc = body.get("card")
        if not isinstance(doc, dict):
            raise ValueError("card must be an object")
        try:
            card = CharacterCard.from_doc(doc, "card")
        except StyleError as exc:
            raise ValueError(str(exc)) from None
        replaces = str(body.get("replaces") or "") or None
        existing = self.styles.cards(owner.kind, owner.key)
        _refuse_new_generic_aliases(
            card.name, card.aliases, next((c.aliases for c in existing if c.name.casefold() == (replaces or card.name).casefold()), ()),
            owner.source_lang,
        )
        if card.name.casefold() != (replaces or card.name).casefold() and any(c.name.casefold() == card.name.casefold() for c in existing):
            # a rename onto another card: say so before counting an impact that would never happen (R12-5)
            raise ValueError(f"there is already a card for {card.name!r}; rename or delete that one first")
        cards = [c for c in existing if c.name.casefold() not in {card.name.casefold(), (replaces or "").casefold()}]
        impact = self.library.impact_units(
            owner.units, None,
            layers=lambda unit: replace(self.library.style_layers(unit), cards=tuple([*cards, card])),
        )
        if not body.get("dry_run"):
            self.styles.save_card(owner.kind, owner.key, card.to_doc(), replaces=replaces)
        return {"ok": True, "saved": not body.get("dry_run"), "card": _card_json(card), **impact}

    def delete_card(self, series_id: int, name: str, lang: str = "vi") -> dict[str, Any]:
        return self.delete_owner_card(self.library.series_owner(series_id), name, lang)

    def delete_movie_card(self, unit_id: int, name: str, lang: str = "vi") -> dict[str, Any]:
        return self.delete_owner_card(self.library.movie_owner(unit_id), name, lang)

    def delete_owner_card(self, owner: Owner, name: str, lang: str) -> dict[str, Any]:
        remaining = tuple(c for c in self.styles.cards(owner.kind, owner.key) if c.name.casefold() != name.casefold())
        impact = self.library.impact_units(
            owner.units, None, layers=lambda unit: replace(self.library.style_layers(unit), cards=remaining),
        )
        if not self.styles.delete_card(owner.kind, owner.key, name):
            raise LookupError(f"no card for {name!r}")
        return {"ok": True, **impact}

    def save_character(self, series_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """A character's Name (the glossary) and Voice (its card) written together, or its card deleted with the Name
        rewritten (``delete_card``): one transaction, so a failure leaves neither half behind (P12b)."""
        return self.save_owner_character(self.library.series_owner(series_id), body)

    def save_movie_character(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self.save_owner_character(self.library.movie_owner(unit_id), body)

    def save_owner_character(self, owner: Owner, body: dict[str, Any]) -> dict[str, Any]:
        lang = str(body.get("lang") or "vi")
        glossary, part, delete = body.get("glossary"), body.get("card"), body.get("delete_card")
        if glossary is not None and not isinstance(glossary, dict):
            raise ValueError("glossary must be a document")
        card: tuple[str, dict[str, Any], str | None] | None = None
        if part is not None:
            if not isinstance(part, dict) or not isinstance(part.get("card"), dict):
                raise ValueError("card must be {\"card\": {...}, \"replaces\": \"old name\"}")
            try:
                parsed = CharacterCard.from_doc(part["card"], "card")
            except StyleError as exc:
                raise ValueError(str(exc)) from None
            card = (parsed.name, parsed.to_doc(), str(part.get("replaces") or "") or None)
        if delete is not None and card is not None:
            raise ValueError("save a card or delete one, not both")
        if glossary is None and card is None and delete is None:
            raise ValueError("there is nothing to save")
        dry_run = body.get("dry_run") is True
        # What the save would flag stale: one count over both halves, so an episode the Name and the Voice both reach is
        # counted once (B1 of the review of P19). ``dry_run`` stops here.
        candidate = g.from_doc(glossary) if glossary is not None else None  # GlossaryError is a ValueError
        cards = tuple(self.styles.cards(owner.kind, owner.key))
        if candidate is not None:  # only what is NEW is refused: a glossary that already holds "Mom" is still read and saved (D-04)
            stored = {c.name.casefold(): c.aliases for c in self.library.glossaries.load(owner.kind, owner.key, lang).characters}
            for character in candidate.characters:
                _refuse_new_generic_aliases(character.name, character.aliases, stored.get(character.name.casefold(), ()), owner.source_lang)
        if card is not None:
            held = next((c.aliases for c in cards if c.name.casefold() == (card[2] or card[0]).casefold()), ())
            _refuse_new_generic_aliases(card[0], CharacterCard.from_doc(card[1], "card").aliases, held, owner.source_lang)
        if card is not None:
            name, doc, replaces = card
            gone = {name.casefold(), (replaces or "").casefold()}
            cards = (*[c for c in cards if c.name.casefold() not in gone], CharacterCard.from_doc(doc, "card"))
        elif delete is not None:
            cards = tuple(c for c in cards if c.name.casefold() != str(delete).casefold())
        impact = self.library.impact_units(
            owner.units, lang, glossary=candidate,
            layers=(lambda unit: replace(self.library.style_layers(unit), cards=cards)) if (card is not None or delete is not None) else None,
        )
        if not dry_run:
            self.library.glossaries.write_character(
                owner.kind, owner.key, lang, glossary_doc=glossary, card=card, delete_card=None if delete is None else str(delete),
            )
        return {"ok": True, "saved": not dry_run, **impact}

    # -- the style of one episode -------------------------------------------------------------------------
    def unit_style(self, unit_id: int) -> dict[str, Any]:
        self._unit(unit_id)
        return {"style": self.styles.unit_style(unit_id).to_doc()}

    def save_unit_style(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        unit = self._unit(unit_id)
        style_doc = body.get("style") or {}
        if not isinstance(style_doc, dict):
            raise ValueError("style must be an object")
        style = _style(style_doc)
        # What saving would flag: this one episode, in every language it is translated into (the episode's style is
        # laid under all of them). ``dry_run`` only counts, as every other save of a style does.
        impact = self.library.impact_units(
            [unit], None, layers=lambda u: replace(self.library.style_layers(u), unit=style),
        )
        if body.get("dry_run"):
            return {"ok": True, "saved": False, "style": style.to_doc(), **impact}
        saved = self.styles.save_unit_style(unit.id, style.to_doc())
        return {"ok": True, "saved": True, "style": saved.to_doc(), **impact}

    def _unit(self, unit_id: int):
        unit = self.catalog.get_unit(unit_id)
        if unit is None:
            raise LookupError(f"no unit {unit_id}")
        return unit

    # -- the trial of a style (FR-18.7) -----------------------------------------------------------------------
    def preview(self, series_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self.owner_preview(self.library.series_owner(series_id), body)

    def preview_movie(self, unit_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self.owner_preview(self.library.movie_owner(unit_id), body)

    def owner_preview(self, owner: Owner, body: dict[str, Any]) -> dict[str, Any]:
        """Translate about twenty sample cues with a style that need not be saved, next to what the episode has now.

        Writes nothing: no file, no version. The model is the default one; the style is the candidate in the body, or
        the stored one when the body has none. A sample is taken from ``unit_id``, or from the first episode of the
        series that has a source subtitle."""
        lang = str(body.get("lang") or "vi")
        unit = self._preview_unit(owner, body.get("unit_id"), lang)
        cues = self.library.source_cues(unit)
        if not cues:
            raise ValueError("the source subtitle has no cues")
        chosen = self._sample(body.get("cues"), body.get("count"), cues)
        window = sorted({m for n in chosen for m in range(max(1, n - PREVIEW_NEIGHBOURS), min(len(cues), n + PREVIEW_NEIGHBOURS) + 1)})
        trimmed = [cues[n - 1] for n in window]  # the samples and the lines around them, in story order
        position = {original: new for new, original in enumerate(window, start=1)}

        preset = body.get("preset")
        draft = body.get("style")
        if draft is not None and not isinstance(draft, dict):
            raise ValueError("style must be an object")
        layers = self._layers_with(unit, None if preset is None else str(preset), None if draft is None else _style(draft))
        # The rules are built on the whole source, as a real job builds them, so a timed address rule, a cue edit and
        # the characters a card is for are the ones the episode has; only then are they numbered for the window.
        rules = self.library.unit_rules(unit, lang, cues=cues, styles=layers).for_window(window)

        settings = self._settings()
        model = str(settings.get("gst_model") or DEFAULT_MODEL)
        if self._provider_factory is None:
            raise RuntimeError("no provider factory is wired into the style service")
        bundle_hash: dict[str, str] = {"value": ""}

        def record(log) -> None:
            self.telemetry.llm_call(
                "console-preview", log, prompt_version=PROMPT_VERSION, glossary_hash=rules.hash,
                style_hash=rules.style_hash, bundle_hash=bundle_hash["value"],
            )

        try:
            provider = self._provider_factory(
                settings, model, deadline=time.monotonic() + PREVIEW_TIMEOUT_SECONDS, on_call=record,
            )
            bundle = gather(
                cues=trimmed, rules=rules, unit=unit, title=owner.title, overview="", target_code=lang,
                target_name=next((o.target_language for o in self.catalog.outputs(unit.id) if o.target_lang == lang), ""),
                source_code=unit.source_code, model=str(getattr(provider, "model", "") or model),
                max_output_tokens=provider.caps.max_output_tokens, temperature=_temperature(settings),
                only=[position[n] for n in chosen], keep=None,
                glossary_filter_threshold=int(settings.get("glossary_filter_threshold", 50) or 0),
            )
            bundle_hash["value"] = bundle.bundle_hash()
            result = run(bundle, provider)
        except MissingApiKey as exc:
            raise ValueError(str(exc)) from None
        except ProviderError as exc:
            raise ValueError(redact(f"The provider could not finish ({type(exc).__name__}: {exc})", settings)) from None
        except TranslationError as exc:  # cues that stayed invalid after repair: that is the trial's answer too
            raise ValueError(redact(f"The model's answer stayed invalid: {exc}", settings)) from None

        current = self._current_texts(unit, lang, len(cues))
        rows = [
            {
                "n": n, "source": cues[n - 1].text, "current": current.get(n),
                "preview": result.cues[position[n] - 1].text,
            }
            for n in chosen
        ]
        self.telemetry.event(
            "style.previewed", unit_id=unit.id, reason="the admin tried a style on sample cues; nothing was written",
            cues=len(rows), model=model, calls=len(result.calls),
        )
        return {
            "ok": True, "unit_id": unit.id, "lang": lang, "model": model, "rows": rows,
            "effective": rules.style.to_doc(), "voices": [c.name for c in rules.voices],
            "tokens_in": result.tokens_in, "tokens_out": result.tokens_out, "bundle_hash": bundle.bundle_hash(),
            "style_hash": rules.style_hash,
        }

    def _preview_unit(self, owner: Owner, unit_id: Any, lang: str):
        if unit_id not in (None, ""):
            unit = self._unit(int(unit_id))
            if all(unit.id != u.id for u in owner.units):
                raise ValueError(f"that unit belongs to another {owner.label}")
            return unit
        units = [u for u in owner.units if u.source_path]
        if not units:
            raise ValueError(
                "no episode of this series has a source subtitle to try the style on" if owner.label == "series"
                else "this movie has no source subtitle to try the style on"
            )
        translated = [u for u in units if any(o.target_lang == lang for o in self.catalog.outputs(u.id))]
        return (translated or units)[0]

    @staticmethod
    def _sample(wanted: Any, count: Any, cues: list[srt.Cue]) -> list[int]:
        if wanted:
            if not isinstance(wanted, list):
                raise ValueError("cues must be a list of cue numbers")
            try:
                numbers = sorted({int(n) for n in wanted})
            except (TypeError, ValueError):
                raise ValueError("cues must be a list of cue numbers") from None
            if not numbers or len(numbers) > PREVIEW_MAX_CUES:
                raise ValueError(f"choose between 1 and {PREVIEW_MAX_CUES} cues")
            if numbers[0] < 1 or numbers[-1] > len(cues):
                raise ValueError(f"cues must be between 1 and {len(cues)}")
            return [n for n in numbers if cues[n - 1].text.strip()] or numbers
        try:
            size = max(1, min(int(count), PREVIEW_MAX_CUES)) if count else PREVIEW_CUES
        except (TypeError, ValueError):
            raise ValueError("count must be a number") from None
        return pick_samples([c.text for c in cues], size)

    def _current_texts(self, unit, lang: str, cue_count: int) -> dict[int, str]:
        """What the episode's current translation says for each cue, when it has one and it lines up with the source."""
        for out in self.catalog.outputs(unit.id):
            if out.target_lang == lang:
                try:
                    target = srt.parse_file(out.output_path)
                except (OSError, srt.SrtError):
                    return {}
                return {n: c.text for n, c in enumerate(target, start=1)} if len(target) == cue_count else {}
        return {}


def _style(doc: dict[str, Any]) -> Style:
    try:
        return Style.from_doc(doc, "style")
    except StyleError as exc:
        raise ValueError(str(exc)) from None


def _temperature(settings: dict[str, Any]) -> float | None:
    try:
        return float(str(settings.get("gst_temperature") or "").strip())
    except ValueError:
        return None
