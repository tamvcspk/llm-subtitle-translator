from __future__ import annotations

from pathlib import Path


def target_output_path(subtitle_path: str, target_code: str, source_code: str | None = None) -> str:
    separator_index = max(subtitle_path.rfind("/"), subtitle_path.rfind("\\"))
    directory = subtitle_path[: separator_index + 1] if separator_index >= 0 else ""
    name = subtitle_path[separator_index + 1 :] if separator_index >= 0 else subtitle_path
    target_code = str(target_code or "vi").strip()
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


SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")


def existing_target_subtitle(
    subtitle_path: str, target_code: str, source_code: str | None = None,
    *, dir_cache: dict[Path, list[Path]] | None = None,
) -> str | None:
    """A sibling subtitle of the same video that is already in ``target_code``, whatever its exact name.

    The target path the worker would write is not the only way a language can be present: Bazarr
    names files ``Name.vi.hi.srt`` or ``Name.vi.ass`` too. A forced-only track does not count.
    ``dir_cache`` remembers ``iterdir()`` results per folder across one ``plan()`` pass (P18a).
    """
    path = Path(subtitle_path)
    code = str(target_code or "").strip().lower()
    if not code or not path.name:
        return None
    stem = path.name.rsplit(".", 1)[0] if "." in path.name else path.name
    parts = stem.split(".")
    source = str(source_code or "").strip().lower()
    for idx in range(len(parts) - 1, -1, -1):
        if source and parts[idx].lower() == source:
            stem = ".".join(parts[:idx])
            break
    prefix = stem.lower() + "."
    parent = path.parent
    if dir_cache is not None and parent in dir_cache:
        siblings = dir_cache[parent]
    else:
        try:
            siblings = sorted(parent.iterdir())
        except OSError:
            if dir_cache is not None:
                dir_cache[parent] = []
            return None
        if dir_cache is not None:
            dir_cache[parent] = siblings
    for sibling in siblings:
        name = sibling.name.lower()
        if len(name) <= len(prefix) or not name.startswith(prefix) or not name.endswith(SUBTITLE_EXTENSIONS):
            continue
        tokens = name[len(prefix):].rsplit(".", 1)[0].split(".")
        if code in tokens and "forced" not in tokens:
            return str(sibling)
    return None
