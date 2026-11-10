from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import enabled_source_languages, enabled_target_languages


def target_output_path(subtitle_path: str, target_code: str, source_code: str | None = None) -> str:
    separator_index = max(subtitle_path.rfind("/"), subtitle_path.rfind("\\"))
    directory = subtitle_path[: separator_index + 1] if separator_index >= 0 else ""
    name = subtitle_path[separator_index + 1 :] if separator_index >= 0 else subtitle_path
    target_code = str(target_code or "zh").strip()
    if not name.lower().endswith(".srt"):
        return f"{directory}{name}.{target_code}.srt"

    stem = name[:-4]
    parts = stem.split(".")
    source_codes = {str(source_code or "").strip().lower()} if source_code else {"en", "eng"}
    source_codes.discard("")
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx].lower() in source_codes:
            parts[idx] = target_code
            return f"{directory}{'.'.join(parts)}.srt"

    return f"{directory}{stem}.{target_code}.srt"


def subtitle_source_language(path: str, source_languages: list[dict[str, Any]]) -> dict[str, Any] | None:
    name = Path(path).name.lower()
    if not name.endswith(".srt"):
        return None
    parts = name[:-4].split(".")
    sources = enabled_source_languages({"source_languages": source_languages})
    by_code = {str(source["code"]).lower(): source for source in sources}
    for part in reversed(parts):
        source = by_code.get(part.lower())
        if source:
            return source
    return None


def subtitle_video_guess(path: str, source_code: str) -> str:
    separator_index = max(path.rfind("/"), path.rfind("\\"))
    directory = path[: separator_index + 1] if separator_index >= 0 else ""
    name = path[separator_index + 1 :] if separator_index >= 0 else path
    if not name.lower().endswith(".srt"):
        return path
    stem = name[:-4]
    parts = stem.split(".")
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx].lower() == source_code.lower():
            del parts[idx]
            while parts and parts[-1].lower() in {"hi", "sdh", "cc", "forced"}:
                parts.pop()
            return f"{directory}{'.'.join(parts)}.mkv"
    return f"{directory}{stem}.mkv"


def scan_source_subtitles(
    roots: list[str],
    source_languages: list[dict[str, Any]],
    target_languages: list[dict[str, Any]],
    limit: int = 200,
) -> list[dict[str, Any]]:
    enabled_targets = enabled_target_languages({"target_languages": target_languages})
    items: list[dict[str, Any]] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for subtitle in root_path.rglob("*.srt"):
            source = subtitle_source_language(str(subtitle), source_languages)
            if not source:
                continue
            missing_targets = [
                target
                for target in enabled_targets
                if str(target["code"]).lower() != str(source["code"]).lower()
                and not Path(target_output_path(str(subtitle), str(target["code"]), source_code=str(source["code"]))).exists()
            ]
            if not missing_targets:
                continue
            video_guess = subtitle_video_guess(str(subtitle), str(source["code"]))
            items.append(
                {
                    "subtitle_path": str(subtitle),
                    "video_path": video_guess,
                    "source_code": str(source["code"]),
                    "source_language": str(source["language"]),
                    "missing_targets": missing_targets,
                }
            )
            if len(items) >= limit:
                return items
    return items


class SubtitleDiscovery:
    """Interface for finding source subtitles and missing translation targets."""

    def scan(self, roots: list[str], source_languages: list[dict[str, Any]], target_languages: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
        return scan_source_subtitles(roots, source_languages, target_languages, limit)
