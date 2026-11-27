"""Answers of the library routes (``api/library_api.py``): units and their translations, cues, corrections, versions and the
comparison of a candidate, story memory and the glossary of a series or a movie."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from . import Answer, maybe
from .common import Estimate
from .style import Style

#: What the version history says about a target: ``stale`` (the rules or the story behind earlier episodes changed since
#: it was translated; never retranslated by itself), ``incomplete`` (translated while an earlier episode had no subtitle
#: or facts) or ``untranslated``.
UnitStatus = Literal["current", "stale", "incomplete", "untranslated"]
VersionStatus = Literal["current", "superseded", "candidate", "removed", "pruned"]
CorrectionKind = Literal["term", "address", "note", "cue_edit"]


class IdTitle(Answer):
    id: int
    title: str


# --- the library -------------------------------------------------------------------------------------------------------


class UnitCounts(Answer):
    current: int
    stale: int
    incomplete: int
    untranslated: int


class LibrarySeriesRow(Answer):
    """An entry of ``GET /api/library/series``."""

    id: int
    key: str
    title: str
    stale_policy: str
    units: int
    counts: UnitCounts


class LibrarySeriesAnswer(Answer):
    items: list[LibrarySeriesRow]


class UnitTarget(Answer):
    """One target language of a unit: what the history says about its file."""

    lang: str
    language: str
    exists: bool
    """The target file is on disk."""
    status: UnitStatus
    version: int | None
    trigger: str | None
    model: str
    missing_units: list[str]
    """``["S01E03"]``: the earlier episodes the translation was made without."""
    updated_at: float | None
    """Unix seconds."""
    modified_outside: bool
    """The file on disk is not the version the worker wrote."""
    removed: bool
    """The admin deleted it: it shows as Missing · Removed and is not requested again."""


class LibraryUnit(Answer):
    """One episode or one movie with its targets."""

    id: int
    series_id: int | None
    """The worker's own series id (not the catalog id); ``null`` for a movie."""
    season: int | None
    episode: int | None
    title: str
    source_code: str
    targets: list[UnitTarget]
    status: UnitStatus


class LibrarySeriesHeader(Answer):
    id: int
    key: str
    title: str
    stale_policy: str


class LibraryUnits(Answer):
    """``GET /api/library/series/{id}/units``."""

    series: LibrarySeriesHeader
    languages: list[str]
    units: list[LibraryUnit]


class LibraryMovies(Answer):
    items: list[LibraryUnit]


class UnitDetail(LibraryUnit):
    """``GET /api/units/{id}``."""

    series: IdTitle | None
    source_exists: bool
    memory_applicable: bool
    """An episode of a series: the Context tab applies."""


# --- cues and corrections ----------------------------------------------------------------------------------------------


class CueRow(Answer):
    n: int
    """1-based number in the source subtitle, the one the model sees."""
    index: int
    start: str
    start_ms: int
    end_ms: int
    source: str
    target: str | None
    """``null`` where the target has no such cue (or no target exists)."""
    locked: bool


class CuesPage(Answer):
    lang: str
    total: int
    offset: int
    focus: int | None
    aligned: bool
    has_target: bool
    rows: list[CueRow]


class CorrectionRow(Answer):
    id: int
    unit_id: int
    target_lang: str
    kind: CorrectionKind
    created_at: float
    active: bool
    payload: dict[str, Any]
    """``term``: ``source``, ``target``; ``address``: ``a``, ``b``, ``a_to_b``, ``b_to_a``; ``note``: ``text``; ``cue_edit``: ``cue``, ``text``, ``source_text``, ``before_text``."""


class CorrectionsAnswer(Answer):
    items: list[CorrectionRow]


class Impact(Answer):
    """What a change to the rules would do to the translations made without it. Nothing is retranslated."""

    stale_now: int
    stale_after: int
    would_stale: int


class CorrectionAdded(Answer):
    """``POST /api/units/{id}/corrections``. ``ids`` are the corrections made (none for a wider scope); a ``cue_edit``
    adds ``version`` and ``warnings``; ``forward`` and ``series`` add ``impact`` and where it starts."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    scope: str
    ids: list[int]
    version: int = maybe()
    warnings: list[str] = maybe()
    impact: Impact | None = maybe()
    """``null`` for a movie (no series to count in)."""
    from_: str | None = Field(None, alias="from")


class CueRange(Answer):
    first: int
    last: int


class RetranslateQueued(Answer):
    ok: bool
    job_id: str
    range: CueRange | None
    correction_ids: list[int] | None


class RetranslateEstimate(Answer):
    """With ``estimate_only``: the same checks ran, nothing was queued."""

    ok: bool
    estimate_only: bool
    range: CueRange | None
    correction_ids: list[int] | None
    estimate: Estimate


class VariantQueued(Answer):
    ok: bool
    job_id: str
    label: str


class VariantEstimate(Answer):
    ok: bool
    estimate_only: bool
    label: str
    estimate: Estimate


# --- versions and the comparison of a candidate -------------------------------------------------------------------------


class VariantParams(Answer):
    label: str
    preset: str
    style: Style


class VersionParams(Answer):
    """How a version was made when its other fields do not say: the range of a retranslation, the style of a variant."""

    range: CueRange = maybe()
    variant: VariantParams = maybe()


class VersionRow(Answer):
    version: int
    trigger: str
    """``auto``, ``retranslate``, ``variant``, ``cue_edit``, ``revert``, ``external``, ``remove``."""
    origin: str
    """``machine``, ``edit``, ``revert``, ``external``."""
    status: VersionStatus
    parent_version: int | None
    model: str
    sha256: str
    tokens_in: int | None
    tokens_out: int | None
    created_at: float
    """Unix seconds."""
    current: bool
    params: VersionParams


class VersionsAnswer(Answer):
    lang: str
    modified_outside: bool
    versions: list[VersionRow]


class JudgeVerdict(Answer):
    """What the LLM judge thought of one cue: advice, never the admin's pick."""

    winner: Literal["variant", "current", "tie"]
    why: str


class CompareRow(Answer):
    n: int
    source: str
    current: str | None
    variant: str | None
    differs: bool
    judge: JudgeVerdict = maybe()
    """Present only where the judge has looked at exactly these two texts."""


class JudgeJob(Answer):
    job_id: str
    state: str
    stage: str


class JudgeSummary(Answer):
    """How the judge stands for one candidate (present when the worker has a judge)."""

    enabled: bool
    """``judge_enabled`` in Settings: off by default, because every use spends model calls."""
    model: str
    prompt_version: str
    judged: int
    variant: int
    current: int
    tie: int
    todo: int
    """Differing cues with no verdict yet."""
    job: JudgeJob | None
    """The open judge job of this candidate (queued, running or waiting), or ``null``."""


class CompareAnswer(Answer):
    """``GET /api/units/{id}/versions/{v}/compare``: the current translation and one version, cue by cue."""

    lang: str
    version: int
    status: VersionStatus
    params: VersionParams
    model: str
    aligned: bool
    differing: int
    rows: list[CompareRow]
    judge: JudgeSummary = maybe()


class DiffCue(Answer):
    n: int
    index: int
    time: str
    text: str


class DiffChange(Answer):
    op: str
    before: list[DiffCue]
    after: list[DiffCue]


class DiffAnswer(Answer):
    version: int
    against: int
    lang: str
    cues_before: int
    cues_after: int
    changed_cues: int
    changes: list[DiffChange]


class RevertAnswer(Answer):
    ok: bool
    version: int
    restored: int


class AdoptedStyle(Answer):
    preset: str
    style: Style


class AdoptAnswer(Answer):
    """``POST /api/units/{id}/versions/{v}/adopt``. With ``apply_style`` the answer adds the style kept and what it flags
    stale in the other episodes; with ``dry_run`` only that, and ``adopted`` is ``null``."""

    ok: bool
    version: int = maybe()
    adopted: int | None = maybe()
    """``null`` in a dry run: nothing is adopted."""
    dry_run: bool = maybe()
    style: AdoptedStyle = maybe()
    stale_now: int = maybe()
    stale_after: int = maybe()
    would_stale: int = maybe()


class RemoveOutputAnswer(Answer):
    ok: bool
    version: int


class JudgeQueued(Answer):
    ok: bool
    job_id: str
    new: bool
    """``false`` when an open judge job of this candidate was returned instead of a second one."""
    cues: int
    calls: int


class JudgeEstimate(Answer):
    ok: bool
    estimate_only: bool
    cues: int
    differing: int
    capped: bool
    """More cues differ than one job looks at."""
    calls: int
    model: str
    estimate: Estimate


class Applied(Answer):
    """What ``apply`` did: adopted the candidate (``adopted``, and ``style`` when asked), or wrote cue edits."""

    adopted: int = maybe()
    style: AdoptedStyle = maybe()
    edited_cues: list[int] = maybe()
    version: int | None = maybe()


class ChooseAnswer(Answer):
    ok: bool
    recorded: int
    applied: Applied


# --- story memory --------------------------------------------------------------------------------------------------------


class ContextSection(Answer):
    title: str
    body: str


class ContextUsed(Answer):
    version: int
    complete: bool
    missing: list[str]
    changed: bool
    sections: list[ContextSection] | None


class ContextPrior(Answer):
    label: str
    has_source: bool
    has_facts: bool


class ContextNow(Answer):
    fingerprint: str
    approximate: bool
    sections: list[ContextSection]
    prior: list[ContextPrior]
    missing: list[str]
    own_facts: dict[str, Any] | None
    own_has_source: bool


class UnitContextAnswer(Answer):
    """``GET /api/units/{id}/context``: stored facts only, never a model call. A movie answers ``applicable: false``."""

    applicable: bool
    enabled: bool
    policy: str = maybe()
    specials_after: str = maybe()
    label: str = maybe()
    special: bool = maybe()
    lang: str | None = maybe()
    """``null`` when the unit has no target."""
    used: ContextUsed | None = maybe()
    """The context of the current version of the target; ``null`` when it was made without memory."""
    now: ContextNow = maybe()
    """What a translation started now would get."""


class FactCharacter(Answer):
    name: str
    aliases: list[str]
    gender: str
    age_hint: str
    role: str


class FactRelationship(Answer):
    a: str
    b: str
    relation: str
    intimacy: int
    hierarchy: str
    cue: int
    evidence: str
    confidence: float
    detail: str


class FactTerm(Answer):
    source: str
    kind: str


class StoredFacts(Answer):
    characters: list[FactCharacter]
    relationship_events: list[FactRelationship]
    terms: list[FactTerm]
    summary: str


class UnitFacts(Answer):
    """``GET /api/units/{id}/facts``: what the stored facts of an episode say. Read only, never a model call."""

    label: str
    has_source: bool
    stored: bool
    """``false`` until the facts were read; ``facts`` is then ``null``."""
    facts: StoredFacts | None


class MemoryUnit(Answer):
    """One episode of ``GET /api/series/{id}/memory``, in story order."""

    id: int
    label: str
    season: int | None
    episode: int | None
    has_source: bool
    has_facts: bool
    facts_hash: str


class SeriesMemory(Answer):
    series: IdTitle
    policy: Literal["", "on", "off"]
    """The series' story-memory switch: ``""`` follows the global ``memory_enabled``."""
    specials_after: str
    """Where season 0 sits in the story (``S02E05``); blank is after the last episode of the first season."""
    global_enabled: bool
    units: list[MemoryUnit]


class AdoptEpisodesAnswer(Answer):
    ok: bool
    adopted: int
    """Units made or given a source."""


class ExtractAnswer(Answer):
    """``POST /api/units/{id}/context/extract``: one ``extract`` job per episode that needs reading."""

    ok: bool
    job_ids: list[str]
    queued: int
    merged: int
    """Episodes that already had an open job: it was lifted, not made twice."""
    skipped: dict[str, str]
    """Episode label to ``no-source`` (nothing to read) or ``cached`` (the newest facts are those of the source as it is)."""


# --- glossary ----------------------------------------------------------------------------------------------------------------


class GlossaryTerm(Answer):
    """A hard rule: where ``source`` shows up, the output says ``target``. ``from``/``until`` are ``S01E06`` (or ``S01E06 00:32:10``)."""

    model_config = ConfigDict(populate_by_name=True)

    source: str
    target: str
    aliases: list[str] = maybe()
    locked: bool = maybe()
    note: str = maybe()
    from_: str = Field(None, alias="from")
    until: str = maybe()


class GlossaryCharacter(Answer):
    """The Name of a character: the translated name, gender and a short description. ``aliases`` are other names the
    source calls the same person (never a form of address like "Mom")."""

    name: str
    target: str = maybe()
    gender: str = maybe()
    description: str = maybe()
    aliases: list[str] = maybe()


class GlossaryAddress(Answer):
    """Who calls whom how, as ``self–other``."""

    model_config = ConfigDict(populate_by_name=True)

    a: str
    b: str
    a_to_b: str = maybe()
    b_to_a: str = maybe()
    from_: str = Field(None, alias="from")
    until: str = maybe()
    note: str = maybe()


class GlossaryDoc(Answer):
    version: int = maybe()
    target_lang: str = maybe()
    style: str = maybe()
    terms: list[GlossaryTerm] = maybe()
    characters: list[GlossaryCharacter] = maybe()
    address_rules: list[GlossaryAddress] = maybe()


class GlossaryAnswer(Answer):
    lang: str
    exists: bool
    doc: GlossaryDoc
    yaml: str
    version: int
    """How many times it was saved; ``0`` when there is none."""
    stale_units: int


class GlossarySave(Impact):
    """A save, or a dry run of one (``saved: false``): what it flags stale. Nothing is retranslated."""

    ok: bool
    saved: bool
    version: int | None


class GlossaryRestore(GlossarySave):
    restored: int


class GlossaryCopy(GlossarySave):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    """The title of the series copied from."""


class GlossaryRevision(Answer):
    version: int
    at: float
    """Unix seconds."""
    note: str
    """What made the replacing save: ``console``, ``import``, ``correction``, ``restore``, ``copy``, ..."""
    terms: int
    characters: int
    address_rules: int


class GlossaryHistory(Answer):
    lang: str
    items: list[GlossaryRevision]
