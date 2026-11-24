"""The LLM judge between a variant and the current translation (P15, FR-18.8): which cues look better, as advice.

``JudgeService`` is the one place that asks for, runs and reads that advice:

* ``request`` is what ``POST /api/units/{id}/versions/{v}/judge`` calls. It only queues a ``judge`` job (kind ``judge``,
  key ``judge:<unit>:<lang>:<version>``) or, with ``estimate_only``, says what the job would cost: the model calls of a
  job go through the one queue, so the day's quota, ``max_calls_per_day``, the breaker, Stop and a restart apply (F7).
* ``run`` is the job: it reads the candidate and the current text cue by cue (``UnitService.compare``), asks the judge
  model (task ``judge``) in batches and stores each verdict as soon as its batch comes back, so a run that stops loses
  nothing it paid for and running it again asks only about the cues without a verdict.
* ``annotate`` lays the stored verdicts over the rows of the comparison.

The judge never decides anything. Its verdict is a quality signal of its own kind (``judge``), never an ``ab`` signal,
so it neither counts in a style's score (Insights) nor becomes a cue edit: only the admin's pick does (``choose``).
A verdict belongs to the exact pair of texts it saw, to the version of the rubric and to the version of the candidate:
when the current text, the variant or the rubric changes, the verdict is no longer shown.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from subcore import judge as engine
from subcore.judge import JUDGE_PROMPT_VERSION, JudgeError, Pair, batches

from ..domain.config import DEFAULT_MODEL, model_for_task
from ..jobs.jobs import JUDGE, JUDGE_MAX_CUES, judge_key
from .memory import unit_label
from ..domain.ports import Telemetry
from ..domain.redaction import DEFAULT as REDACTOR
from ..adapters.telemetry import NULL

SIGNAL = "judge"
SIGNALS_READ = 5000  # the newest verdicts of one episode that are looked at: far more than a few hundred cues times a few asks
StatusCallback = Callable[..., None]


def calls_for(cues: int) -> int:
    """Model calls to judge ``cues`` cues (at most ``JUDGE_MAX_CUES`` of them)."""
    return -(-min(max(0, int(cues)), JUDGE_MAX_CUES) // engine.BATCH)


def cue_limit(settings: dict[str, Any]) -> int:
    """The most cues one job looks at: ``JUDGE_MAX_CUES``, and no more than ``max_calls_per_job`` calls can cover, so a job
    never stops on its own call budget half way (what is left is asked in the next one)."""
    try:
        per_job = int(settings.get("max_calls_per_job") or 0)
    except (TypeError, ValueError):
        per_job = 0
    return min(JUDGE_MAX_CUES, per_job * engine.BATCH) if per_job > 0 else JUDGE_MAX_CUES


class JudgeService:
    def __init__(
        self, units: Any, queue: Any, store: Any, *, settings_source: Callable[[], dict[str, Any]],
        telemetry: Telemetry | None = None,
    ) -> None:
        self.units = units  # ``UnitService``: the comparison is read through it
        self.queue = queue  # ``JobQueue``
        self.store = store
        self._settings = settings_source
        self.telemetry: Telemetry = telemetry or NULL

    # -- what the settings say -----------------------------------------------------------------------------------------
    def enabled(self) -> bool:
        return self._settings().get("judge_enabled") is True

    def model(self) -> str:
        settings = self._settings()
        return model_for_task(settings, "judge", str(settings.get("gst_model") or DEFAULT_MODEL))

    # -- what the comparison shows ---------------------------------------------------------------------------------------
    @staticmethod
    def askable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The cues the judge can say something about: both texts exist and they differ."""
        return [r for r in rows if r["differs"] and r["current"] and r["variant"]]

    def annotate(self, unit_id: int, lang: str, version: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Lay the stored verdicts over ``rows`` (each judged row gets ``judge: {winner, why}``) and say how things
        stand: how many cues the judge has looked at, what it found, how many are still to ask, and the open job."""
        verdicts = self._verdicts(unit_id, lang, version)
        tally = {"variant": 0, "current": 0, "tie": 0}
        todo = 0
        for row in rows:
            if row["n"] in verdicts and verdicts[row["n"]][0] == (self._seen(row["current"]), self._seen(row["variant"])):
                (_, winner, why) = verdicts[row["n"]]
                row["judge"] = {"winner": winner, "why": why}
                tally[winner] += 1
            elif self.askable([row]):
                todo += 1
        job = self.queue.jobs.open_for(judge_key(unit_id, lang, version), JUDGE)
        return {
            "enabled": self.enabled(), "model": self.model(), "prompt_version": JUDGE_PROMPT_VERSION,
            "judged": sum(tally.values()), **tally, "todo": todo,
            "job": {"job_id": job["job_id"], "state": job["state"], "stage": job.get("stage") or ""} if job else None,
        }

    def _redactor(self) -> Any:
        return getattr(self.telemetry, "redactor", REDACTOR)

    def _seen(self, text: str) -> str:
        """The text as it is stored in a verdict (the one filter of ``redaction.py`` is applied to what is written down), so a
        stored text and a live one are compared on equal terms."""
        return str(self._redactor().redact(str(text or ""), loose=False))

    def _verdicts(
        self, unit_id: int, lang: str, version: int, *, job_id: str = "",
    ) -> dict[int, tuple[tuple[str, str], str, str]]:
        """cue -> ((current text, variant text) it judged, winner, why): the newest verdict of the current rubric (with
        ``job_id``, only the verdicts that job stored)."""
        return {
            cue: ((signal.before, signal.after), str(signal.meta["winner"]), str(signal.meta.get("why") or ""))
            for cue, signal in self._newest(unit_id, lang, version, job_id).items()
        }

    def _newest(self, unit_id: int, lang: str, version: int, job_id: str = "") -> dict[int, Any]:
        """cue -> the newest stored verdict (a signal) of the current rubric for it."""
        found: dict[int, Any] = {}
        for signal in self.store.signals(kind=SIGNAL, unit_id=unit_id, limit=SIGNALS_READ):  # newest first
            meta = signal.meta
            if signal.version != version or meta.get("lang") != lang or meta.get("prompt_version") != JUDGE_PROMPT_VERSION:
                continue
            if job_id and meta.get("job_id") != job_id:
                continue
            if signal.cue is None or signal.cue in found or meta.get("winner") not in ("variant", "current", "tie"):
                continue
            found[signal.cue] = signal
        return found

    # -- the request ----------------------------------------------------------------------------------------------------------
    def request(self, unit_id: int, version: int, body: dict[str, Any]) -> dict[str, Any]:
        """Queue the judging of a candidate, or with ``estimate_only`` say what it would cost and queue nothing.
        ``again`` asks about the cues that already have a verdict too."""
        if not self.enabled():
            raise ValueError("The judge is off: turn on judge_enabled in Settings")
        unit = self.units.unit(unit_id)
        out = self.units.output(unit, body.get("lang"))
        view = self.units.compare(unit_id, version, out.target_lang)
        if view["status"] != "candidate":
            raise ValueError(f"version {version} is {view['status']}, not a candidate to judge")
        again = body.get("again") is True
        if again and self.queue.jobs.open_for(judge_key(unit_id, out.target_lang, version), JUDGE):
            # One job is open per candidate and a second request is that job: it would not ask again what the first skips.
            raise ValueError("The judge is already working on this candidate: ask again when it has finished")
        rows = self.askable(view["rows"])
        wanted = rows if again else [r for r in rows if "judge" not in r]
        if not wanted:
            raise ValueError(
                "Nothing to judge: the two translations are the same" if not rows
                else "Nothing to judge: the judge has already looked at every cue that differs",
            )
        cues = min(len(wanted), cue_limit(self._settings()))
        calls = calls_for(cues)
        if body.get("estimate_only") is True:
            return {
                "ok": True, "estimate_only": True, "cues": cues, "differing": len(wanted), "capped": len(wanted) > cues,
                "calls": calls, "model": self.model(), "estimate": asdict(self.units.estimator.of(1, cues, calls)),
            }
        series = self.units.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        job_id, new = self.queue.enqueue_judge({
            "unit_id": unit.id, "series_id": series.bazarr_id if series else "", "target_code": out.target_lang,
            "version": version, "label": unit_label(unit), "title": series.title if series else unit.title,
            "cues": cues, "calls": calls, "again": again,
        })
        return {"ok": True, "job_id": job_id, "new": new, "cues": cues, "calls": calls}

    # -- the job ------------------------------------------------------------------------------------------------------------------
    def run(
        self, job: dict[str, Any], update_status: StatusCallback, for_task: Callable[[str], Any],
    ) -> str:
        """Judge the cues of a candidate that have no verdict (or all of them, for ``again``). Each batch is stored when
        it comes back. Provider errors propagate typed: the caller maps them for the queue."""
        unit_id, version, lang = int(job.get("unit_id") or 0), int(job.get("version") or 0), str(job.get("target_code") or "")
        if not self.enabled():  # the switch stops what is already in line too, not only new requests
            return "skipped-judge-off"
        view = self.units.compare(unit_id, version, lang)  # LookupError / ValueError say why it cannot be read: the job fails with it
        if view["status"] != "candidate":
            return "skipped-not-a-candidate"
        rows = view["rows"]
        job_id = str(job.get("job_id") or "")
        asked = self.askable(rows)
        if job.get("again") is True:
            # A run that stopped part-way (quota, outage, restart, Stop then Retry) must not pay twice for the cues this
            # very job already judged: only the ones it has not stored a verdict for are asked.
            mine = self._verdicts(unit_id, lang, version, job_id=job_id) if job_id else {}
            asked = [r for r in asked if mine.get(r["n"], (None,))[0] != (self._seen(r["current"]), self._seen(r["variant"]))]
            # More cues than one run may ask: the ones the judge looked at longest ago come first, so asking again covers the
            # whole episode over a few runs and not the same first cues each time (a cue never judged is the oldest of all).
            stored = self._newest(unit_id, lang, version)
            asked.sort(key=lambda r: stored[r["n"]].at if r["n"] in stored else float("-inf"))
        else:
            asked = [r for r in asked if "judge" not in r]
        limit = cue_limit(self._settings())
        capped = len(asked) > limit
        asked = asked[:limit]
        if not asked:
            return "skipped-nothing-to-judge"
        source_of = {r["n"]: r["source"] for r in rows}
        pairs = [Pair(r["n"], r["source"], r["current"], r["variant"], source_of.get(r["n"] - 1, "")) for r in asked]
        texts = {r["n"]: (r["current"], r["variant"]) for r in asked}
        answered = unanswered = 0
        for chunk in batches(pairs):
            update_status(
                f"Judging cues {chunk[0].n} to {chunk[-1].n}", progress_done=answered + unanswered, progress_total=len(pairs),
            )
            provider = for_task("judge")
            try:
                verdicts = engine.judge(provider, chunk, lang=lang)
            except JudgeError as exc:
                raise RuntimeError(f"The judge's answer could not be read: {exc}") from None
            for verdict in verdicts:
                current, variant = texts[verdict.n]
                # The verdicts are the job's only output: written straight to the store (not through the best-effort
                # telemetry sink, which may drop or delay a write), filtered like everything written down.
                self.store.add_signal(
                    SIGNAL, unit_id=unit_id, version=version, cue=verdict.n, before=self._seen(current), after=self._seen(variant),
                    meta=self._redactor().clean({
                        "winner": verdict.winner, "why": verdict.why, "lang": lang, "model": getattr(provider, "model", ""),
                        "prompt_version": JUDGE_PROMPT_VERSION, "job_id": job_id,
                    }),
                )
            answered += len(verdicts)
            unanswered += len(chunk) - len(verdicts)
        job["report"] = {"judged": answered, "unanswered": unanswered, "capped": capped}
        return "judged" if answered else "nothing-answered"
