#!/usr/bin/env bash
set -euo pipefail

VIDEO_PATH="${1:-}"
SUBTITLE_PATH="${2:-}"
LANGUAGE="${3:-}"
PROVIDER="${4:-}"
SERIES_ID="${5:-}"
MEDIA_ID="${6:-}"
QUEUE_DIR="${GST_QUEUE_DIR:-/config/postprocess/queue}"
TARGETS_FILE="${GST_TARGETS_FILE:-/config/postprocess/targets.json}"

mkdir -p "$QUEUE_DIR/pending" "$QUEUE_DIR/processing" "$QUEUE_DIR/done" "$QUEUE_DIR/failed"

provider_lc="$(printf '%s' "$PROVIDER" | tr '[:upper:]' '[:lower:]')"
language_lc="$(printf '%s' "$LANGUAGE" | cut -d: -f1 | tr '[:upper:]' '[:lower:]')"

if [ "$provider_lc" != "embeddedsubtitles" ]; then
  echo "skip: provider=$PROVIDER"
  exit 0
fi

MEDIA_TYPE="movie"
if [ -n "$SERIES_ID" ]; then
  MEDIA_TYPE="series"
fi

export VIDEO_PATH SUBTITLE_PATH LANGUAGE PROVIDER SERIES_ID MEDIA_ID MEDIA_TYPE QUEUE_DIR TARGETS_FILE
python3 - <<'PY'
import json
import os
import pathlib
import hashlib
import time

queue = pathlib.Path(os.environ["QUEUE_DIR"])
try:
    pause = json.loads((queue / "provider-pause.json").read_text(encoding="utf-8"))
    if pause.get("reason") == "daily-quota" and float(pause.get("retry_at") or 0) > time.time():
        print("skip: daily Gemini quota exhausted; queue admission paused")
        raise SystemExit(0)
except (OSError, TypeError, ValueError):
    pass

targets_file = pathlib.Path(os.environ["TARGETS_FILE"])
subtitle_path = os.environ["SUBTITLE_PATH"]
source_code = os.environ["LANGUAGE"].split(":", 1)[0].strip().lower()

if targets_file.exists():
    config = json.loads(targets_file.read_text(encoding="utf-8"))
else:
    config = {
        "source_languages": [{"code": "en", "language": "English", "enabled": True}],
        "target_languages": [{"code": "zh", "language": "Simplified Chinese", "enabled": True}],
    }

if not isinstance(config, dict):
    raise SystemExit("targets.json must contain source_languages and target_languages in an object")
sources = config.get("source_languages") or [{"code": "en", "language": "English", "enabled": True}]
targets = config.get("target_languages") or [{"code": "zh", "language": "Simplified Chinese", "enabled": True}]

def enabled(items):
    for item in items:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        code = str(item.get("code") or item.get("code2") or "").strip()
        language = str(item.get("language") or item.get("name") or "").strip()
        if code and language:
            yield {"code": code, "language": language}

source = None
for candidate in enabled(sources):
    if candidate["code"].lower() == source_code:
        source = candidate
        break

if source is None:
    print(f"skip: source_language={os.environ['LANGUAGE']}")
    raise SystemExit(0)

def output_path_for(code):
    if not subtitle_path.lower().endswith(".srt"):
        return f"{subtitle_path}.{code}.srt"
    prefix = subtitle_path[:-4]
    parts = prefix.split(".")
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx].lower() == source["code"].lower():
            parts[idx] = code
            return ".".join(parts) + ".srt"
    return f"{prefix}.{code}.srt"

SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

def existing_target(code):
    """A sibling subtitle already in this language, however it is named (Name.vi.hi.srt, Name.vi.ass)."""
    path = pathlib.Path(subtitle_path)
    stem = path.name.rsplit(".", 1)[0] if "." in path.name else path.name
    parts = stem.split(".")
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx].lower() == source["code"].lower():
            stem = ".".join(parts[:idx])
            break
    prefix = stem.lower() + "."
    try:
        siblings = sorted(path.parent.iterdir())
    except OSError:
        return None
    for sibling in siblings:
        name = sibling.name.lower()
        if len(name) <= len(prefix) or not name.startswith(prefix) or not name.endswith(SUBTITLE_EXTENSIONS):
            continue
        tokens = name[len(prefix):].rsplit(".", 1)[0].split(".")
        if code.lower() in tokens and "forced" not in tokens:
            return sibling
    return None

# Guards for trying this against a real Bazarr: translate only inside GST_ONLY_PATHS and never keep
# more than GST_MAX_PENDING jobs waiting, however many items Bazarr reports as wanted.
normalized_subtitle = subtitle_path.replace("\\", "/")
only_paths = [p.strip().replace("\\", "/").rstrip("/") for p in os.environ.get("GST_ONLY_PATHS", "").replace("\n", ",").split(",") if p.strip()]
if only_paths and not any(normalized_subtitle == p or normalized_subtitle.startswith(p + "/") for p in only_paths):
    print("skip: outside GST_ONLY_PATHS")
    raise SystemExit(0)
try:
    max_open = max(0, int(os.environ.get("GST_MAX_PENDING", "0") or 0))
except ValueError:
    max_open = 0

def open_jobs():
    return sum(len(list((queue / state).glob("*.json"))) for state in ("pending", "processing", "deferred"))

created = []
for target in enabled(targets):
    code = target["code"]
    language = target["language"]
    if code.lower() == source["code"].lower():
        continue
    output_path = output_path_for(code)
    if pathlib.Path(output_path).exists() or existing_target(code):
        continue
    job_id = hashlib.sha1(f"{subtitle_path}|{output_path}|{code}".encode("utf-8")).hexdigest()
    pending = queue / "pending" / f"{job_id}.json"
    processing = queue / "processing" / f"{job_id}.json"
    if pending.exists() or processing.exists() or (queue / "deferred" / pending.name).exists():
        continue
    if max_open and open_jobs() >= max_open:
        print(f"skip: GST_MAX_PENDING={max_open} jobs already waiting")
        break
    tmp = pending.with_name(f".{job_id}.tmp")
    job = {
        "job_id": job_id,
        "created_at": int(time.time()),
        "video_path": os.environ["VIDEO_PATH"],
        "subtitle_path": subtitle_path,
        "output_path": output_path,
        "language": os.environ["LANGUAGE"],
        "source_code": source["code"],
        "source_language": source["language"],
        "target_code": code,
        "target_language": language,
        "provider": os.environ["PROVIDER"],
        "series_id": os.environ["SERIES_ID"],
        "media_id": os.environ["MEDIA_ID"],
        "media_type": os.environ["MEDIA_TYPE"],
    }
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    tmp.replace(pending)
    created.append(job_id)

if created:
    print("queued: " + ",".join(created))
else:
    print("skip: no target jobs created")
PY
