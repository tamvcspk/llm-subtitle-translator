"""What may be translated at all: pure checks on a job dict, kept from the file queue (``GST_ONLY_PATHS``, a source
that is gone, a target that is already there).

``jobs.queue`` re-exports these names, so callers and tests keep one door.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .jobs import job_key
from ..domain.subtitles import existing_target_subtitle, target_output_path


def allowed_path_prefixes() -> tuple[str, ...]:
    """Folders automatic jobs may come from (`GST_ONLY_PATHS`, comma separated); empty means anywhere.

    A guard for trying the worker against a real Bazarr: only the named folders are ever translated,
    however many items Bazarr reports as wanted.
    """
    raw = os.getenv("GST_ONLY_PATHS", "")
    return tuple(p.strip().replace("\\", "/").rstrip("/") for p in re.split(r"[,\n]", raw) if p.strip())


def outside_allowed_paths(subtitle_path: str) -> bool:
    prefixes = allowed_path_prefixes()
    if not prefixes:
        return False
    normalized = str(subtitle_path).replace("\\", "/")
    return not any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in prefixes)


def skip_reason(job: dict[str, Any]) -> str | None:
    """Why a job must not translate anything, or None when it should run."""
    provider = str(job.get("provider", "")).lower()
    language = str(job.get("source_code") or job.get("language") or "").split(":", 1)[0].lower()
    subtitle_path = str(job.get("subtitle_path", ""))
    target_code = str(job.get("target_code") or "vi")
    output_path = str(job.get("output_path") or target_output_path(subtitle_path, target_code, source_code=language))

    if provider and provider not in {"embeddedsubtitles", "gemini-console", "manual", "local"}:
        return f"provider {provider} is not handled"
    if language and language == target_code.lower():
        return "source and target language are the same"
    if not subtitle_path or not Path(subtitle_path).exists():
        return "source subtitle is gone"
    # An explicit retranslation exists to replace the target (the version history keeps the old one); a variant
    # exists to be compared with it and is never put on disk, so an existing target is no reason to skip it.
    if job.get("trigger") in ("retranslate", "variant"):
        return None
    if outside_allowed_paths(subtitle_path):
        return "outside GST_ONLY_PATHS"
    if Path(output_path).exists():
        return "target subtitle already exists"
    found = existing_target_subtitle(subtitle_path, target_code, language)
    if found:
        return f"{target_code} subtitle already present: {Path(found).name}"
    return None


def should_skip_job(job: dict[str, Any]) -> bool:
    return skip_reason(job) is not None


def job_id_for(subtitle_path: str, output_path: str, target_code: str) -> str:
    return job_key(subtitle_path, output_path, target_code)
