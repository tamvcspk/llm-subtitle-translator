"""Making jobs: ``EnqueueMixin`` holds the ``enqueue_*`` methods of ``JobQueue`` (``queue.py``), one per kind of job.

Each one decides whether a request becomes a new job, lifts one already in line, or is refused, and says why in an
event (FR-15.3, FR-19.1). They use what ``JobQueue`` holds (``jobs``, ``_emit``, ``retry``).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..domain import queue_policy as policy
from ..domain.config import enabled_target_languages
from .jobs import (
    CANCELLED, FAILED, QUEUED, REPLACE, VARIANT, WAITING, EXTRACT, JUDGE, RESEARCH, SUGGEST, extract_key, job_key,
    judge_key, research_key, retranslation_key, suggest_key, variant_key,
)
from ..domain.subtitles import existing_target_subtitle, target_output_path
from .skip_rules import outside_allowed_paths


RETRANSLATE_PRIORITY = policy.RETRANSLATE


class EnqueueMixin:
    def enqueue(
        self, base_job: dict[str, Any], targets: list[dict[str, Any]], *, origin: str = "manual",
        prio: int = policy.INTERACTIVE,
    ) -> list[str]:
        """One job per enabled target language that is still missing; returns the ids queued (the admin's own
        requests are the interactive class). A day's quota being spent does not stop a request: it waits (NFR-1)."""
        created: list[str] = []
        subtitle_path = str(base_job.get("subtitle_path", ""))
        name = Path(subtitle_path).name

        def skipped(reason: str, target_code: str = "", job_id: str = "", level: str = "INFO") -> None:
            self._emit("enqueue.skipped", level=level, job_id=job_id, reason=reason, subtitle=name,
                                 target=target_code or None, unit_id=base_job.get("unit_id") or None)

        if not subtitle_path or not Path(subtitle_path).exists():
            skipped("the source subtitle does not exist")
            return created
        source_code = str(base_job.get("source_code") or base_job.get("language") or "en").split(":", 1)[0].strip()
        source_language = str(base_job.get("source_language") or source_code or "English")
        if outside_allowed_paths(subtitle_path):
            logging.warning("Not queueing %s: outside GST_ONLY_PATHS", subtitle_path)
            skipped("the subtitle is outside GST_ONLY_PATHS", level="WARNING")
            return created

        for target in enabled_target_languages({"target_languages": targets}):
            target_code = str(target["code"])
            if source_code.lower() == target_code.lower():
                skipped("the source and the target language are the same", target_code)
                continue
            output_path = str(base_job.get("output_path") or target_output_path(subtitle_path, target_code, source_code=source_code))
            key = job_key(subtitle_path, output_path, target_code)
            # Only a language that is missing gets translated: any sibling subtitle already in it counts.
            if Path(output_path).exists():
                skipped("the target subtitle already exists", target_code, key)
                continue
            sibling = existing_target_subtitle(subtitle_path, target_code, source_code)
            if sibling:
                skipped(f"a {target_code} subtitle is already present: {Path(sibling).name}", target_code, key)
                continue
            if self.jobs.open_for(key) is not None:
                self.jobs.add({}, key=key, prio=prio)  # a higher class lifts the job already in line
                skipped("the same job is already waiting or running", target_code, key)
                continue
            # The same request under another name: the files were renamed after the first job was made (FR-14.3).
            same = self.jobs.open_for_unit(int(base_job["unit_id"]), target_code) if base_job.get("unit_id") else None
            if same is not None:
                self.jobs.lift(same["job_id"], prio)
                skipped("the same request is already waiting or running", target_code, same["job_id"])
                continue
            if origin == "policy":
                # The policy never revives or repeats what failed or what the admin cancelled: only the admin retries (D4).
                ended = self.jobs.last_ended_state(key, base_job.get("unit_id") or None, target_code)
                if ended in (FAILED, CANCELLED):
                    skipped(f"an earlier job for this file {ended}; only the admin asks again", target_code, key)
                    continue
            else:
                failed = self.jobs.latest_for(key, FAILED)
                if failed is not None and self.retry(failed["job_id"]):
                    created.append(failed["job_id"])
                    continue
            job = {
                **base_job, "created_at": int(time.time()), "source_code": source_code, "source_language": source_language,
                "target_code": target_code, "target_language": str(target["language"]), "output_path": output_path,
            }
            added = self.jobs.add(job, key=key, origin=origin, prio=prio)
            if added.job_id:
                created.append(added.job_id)
                self._emit(
                    "job.created", job_id=added.job_id, unit_id=base_job.get("unit_id") or None, subtitle=name,
                    reason=("the auto_request policy asked for it: the target language is missing" if origin == "policy"
                            else "queued from the console: the target language is missing"),
                    target=target_code, origin=origin,
                )
        return created

    def enqueue_retranslation(self, job: dict[str, Any]) -> str:
        """One explicit retranslation ahead of automatic work. A request for a target that already has an open
        ``fill`` job turns that job into the retranslation (FR-15.3); a second retranslation is refused."""
        subtitle_path, output_path = str(job.get("subtitle_path", "")), str(job.get("output_path", ""))
        target_code = str(job.get("target_code", ""))
        key = job_key(subtitle_path, output_path, target_code)
        payload = {**job, "trigger": "retranslate", "priority": RETRANSLATE_PRIORITY, "created_at": int(time.time())}
        window = (job.get("retranslate") or {}).get("range") if isinstance(job.get("retranslate"), dict) else None
        open_job = self.jobs.open_for(key)
        if open_job is not None:
            if open_job["mode"] == REPLACE:
                raise ValueError("A retranslation of this subtitle is already queued")
            if not self.jobs.merge_retranslation(open_job["job_id"], payload, RETRANSLATE_PRIORITY):
                raise ValueError("This subtitle is being translated right now; retranslate it when the job ends")
            job_id = open_job["job_id"]
        else:
            added = self.jobs.add(
                payload, key=key, mode=REPLACE, origin="retranslate", prio=RETRANSLATE_PRIORITY,
                job_id=f"{retranslation_key(subtitle_path, output_path, target_code)}-{time.time_ns()}",
            )
            if not added.job_id:  # raced with another request between the check and the insert
                raise ValueError("A retranslation of this subtitle is already queued")
            job_id = added.job_id
        self._emit(
            "job.created", job_id=job_id, unit_id=job.get("unit_id") or None, subtitle=Path(subtitle_path).name,
            reason="an explicit retranslation: it runs ahead of automatic work and may replace the target",
            target=target_code, origin="retranslate", range=window,
        )
        return job_id

    def retranslation_blocker(self, job: dict[str, Any]) -> str | None:
        """Why ``enqueue_retranslation`` would refuse this job now, or None. Lets the Estimate box say so before
        the admin confirms; the two must agree, so a change to one reads the other."""
        key = job_key(str(job.get("subtitle_path", "")), str(job.get("output_path", "")), str(job.get("target_code", "")))
        open_job = self.jobs.open_for(key)
        if open_job is None:
            return None
        if open_job["mode"] == REPLACE:
            return "A retranslation of this subtitle is already queued"
        if open_job["state"] not in (QUEUED, WAITING):
            return "This subtitle is being translated right now; retranslate it when the job ends"
        return None

    def enqueue_variant(self, job: dict[str, Any]) -> str:
        """One variant job (FR-18.8): the whole episode translated with another style and kept as a candidate, never
        put on disk. At most one is open per episode and language (FR-15.3); a second request is refused."""
        subtitle_path, output_path = str(job.get("subtitle_path", "")), str(job.get("output_path", ""))
        target_code = str(job.get("target_code", ""))
        key = variant_key(subtitle_path, output_path, target_code)
        payload = {**job, "trigger": "variant", "created_at": int(time.time())}
        added = self.jobs.add(
            payload, key=key, kind=VARIANT, mode=VARIANT, origin="variant", prio=policy.INTERACTIVE,
            job_id=f"{key}-{time.time_ns()}",
        )
        if not added.job_id:
            raise ValueError("A variant of this episode is already queued")
        self._emit(
            "job.created", job_id=added.job_id, unit_id=job.get("unit_id") or None, subtitle=Path(subtitle_path).name,
            reason="a variant: the episode is translated with another style and kept as a candidate to compare",
            target=target_code, origin="variant", label=(job.get("variant") or {}).get("label"),
        )
        return added.job_id

    def enqueue_extract(self, units: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        """One ``extract`` job per episode, in the order given (story order, so the facts of the early episodes come
        first): the admin's own request, the interactive class. Each dict carries ``unit_id``, ``series_id`` (what
        a series' jobs are told apart by), ``label`` (``S01E02``) and ``title``. An episode that already has an open
        job is not made twice: that job is lifted to this class. Returns ``(created ids, ids of the jobs already open)``."""
        created: list[str] = []
        existing: list[str] = []
        for unit in units:
            unit_id = int(unit["unit_id"])
            payload = {**unit, "unit_id": unit_id, "created_at": int(time.time())}
            added = self.jobs.add(payload, key=extract_key(unit_id), kind=EXTRACT, mode=EXTRACT, origin="manual",
                                  prio=policy.INTERACTIVE, job_id=f"{extract_key(unit_id)}-{time.time_ns()}")
            if added.job_id:
                created.append(added.job_id)
                self._emit(
                    "job.created", job_id=added.job_id, unit_id=unit_id,
                    reason=f"the admin asked for the facts of {unit.get('label') or 'an episode'} to be read",
                    origin="manual", job_kind=EXTRACT,
                )
            elif added.existing:
                existing.append(added.existing)
        return created, existing

    def enqueue_suggest(
        self, series_id: int, lang: str, *, title: str, bazarr_id: str = "", origin: str = "manual",
        prio: int = policy.INTERACTIVE,
    ) -> tuple[str, bool]:
        """One ``suggest`` job for this series and language: it looks for proposals in the stored facts (at most three
        model calls). Returns ``(job id, whether it is new)``. At most one is open per series and language: a second
        request is that job, lifted to the higher class. The admin's own request is the interactive class; the one a
        translation leaves behind (origin ``facts``) is the lowest, and is not the policy's, so it never counts against
        ``auto_request_daily_cap``."""
        payload = {
            "stored_series_id": int(series_id), "series_id": bazarr_id, "title": title, "target_code": lang,
            "created_at": int(time.time()),
        }
        key = suggest_key(series_id, lang)
        added = self.jobs.add(payload, key=key, kind=SUGGEST, mode=SUGGEST, origin=origin, prio=prio,
                              job_id=f"{key}-{time.time_ns()}")
        if added.job_id:
            self._emit(
                "job.created", job_id=added.job_id, unit_id=None, target=lang, origin=origin, job_kind=SUGGEST,
                reason=("the admin asked for proposals from the facts of " if origin == "manual"
                        else "new facts were read: look for proposals in ") + (title or "the series"),
            )
            return added.job_id, True
        return str(added.existing), False

    def enqueue_research(
        self, series_id: int, lang: str, *, title: str, bazarr_id: str = "", origin: str = "manual",
        prio: int = policy.INTERACTIVE, auto_accept: bool | None = None,
    ) -> tuple[str, bool]:
        """One ``research`` job for this series and language: it looks the names and terms of the series up on the web (two
        model calls, FR-7.5). Returns ``(job id, whether it is new)``. At most one is open per series and language: a second
        request is that job, lifted to the higher class. The admin's own request is ``origin = manual``; the one a translation
        leaves behind is ``policy`` and outranks the translations still waiting for the series (``prio``), so a term applied
        by itself is there for the episodes after it. ``auto_accept`` is the admin's choice for this run in the Estimate box
        (``None`` = what ``auto_accept_terms`` says)."""
        payload: dict[str, Any] = {
            "stored_series_id": int(series_id), "series_id": bazarr_id, "title": title, "target_code": lang,
            "created_at": int(time.time()),
        }
        if auto_accept is not None:
            payload["auto_accept"] = bool(auto_accept)
        key = research_key(series_id, lang)
        added = self.jobs.add(payload, key=key, kind=RESEARCH, mode=RESEARCH, origin=origin, prio=prio,
                              job_id=f"{key}-{time.time_ns()}")
        if added.job_id:
            self._emit(
                "job.created", job_id=added.job_id, unit_id=None, target=lang, origin=origin, job_kind=RESEARCH,
                reason=("the admin asked for the terms of " if origin == "manual"
                        else "new facts were read: look up on the web what two episodes share in ")
                + (title or "the series") + (" to be looked up on the web" if origin == "manual" else ""),
            )
            return added.job_id, True
        return str(added.existing), False

    def enqueue_judge(self, payload: dict[str, Any]) -> tuple[str, bool]:
        """One ``judge`` job for a candidate (P15): it reads the candidate next to the current translation and says which
        cues look better, in as many calls as it takes to cover them (a few dozen cues each). The admin's own request,
        the interactive class. At most one is open per candidate: a second request is that job. Returns ``(job id,
        whether it is new)``. ``payload`` has ``unit_id``, ``target_code``, ``version``, ``label``, ``title`` and ``calls``."""
        unit_id, lang, version = int(payload["unit_id"]), str(payload["target_code"]), int(payload["version"])
        key = judge_key(unit_id, lang, version)
        added = self.jobs.add(
            {**payload, "created_at": int(time.time())}, key=key, kind=JUDGE, mode=JUDGE, origin="manual",
            prio=policy.INTERACTIVE, job_id=f"{key}-{time.time_ns()}",
        )
        if added.job_id:
            self._emit(
                "job.created", job_id=added.job_id, unit_id=unit_id, target=lang, origin="manual", job_kind=JUDGE,
                reason=f"the admin asked the judge to compare candidate v{version} with the current translation",
            )
            return added.job_id, True
        return str(added.existing), False
