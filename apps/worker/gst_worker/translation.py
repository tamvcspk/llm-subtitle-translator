from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .subtitles import target_output_path


GST_OUTPUT_TAIL_LENGTH = 1000


class ProviderUnavailableError(RuntimeError):
    """Gemini is temporarily unavailable and the queue should retry later."""


class DailyQuotaExceededError(RuntimeError):
    """Gemini's per-model daily quota is exhausted."""


def build_gst_command(
    input_srt: str,
    output_srt: str,
    description: str,
    target_language: str | None = None,
    gst_settings: dict[str, Any] | None = None,
) -> list[str]:
    settings = gst_settings or {}
    model = str(settings.get("gst_model") or os.getenv("GST_MODEL", "gemini-flash-latest"))
    batch_size = str(settings.get("gst_batch_size") or os.getenv("GST_BATCH_SIZE", "500"))
    command = [
        "gst",
        "translate",
        "-i",
        input_srt,
        "-l",
        target_language or os.getenv("GST_TARGET_LANGUAGE", "Simplified Chinese"),
        "-o",
        output_srt,
        "--model",
        model,
        "--batch-size",
        batch_size,
        "--context-size",
        str(_int_setting(settings, "gst_context_size", "GST_CONTEXT_SIZE", 50)),
    ]
    for option, key in (
        ("--temperature", "gst_temperature"),
        ("--top-p", "gst_top_p"),
        ("--top-k", "gst_top_k"),
    ):
        value = str(settings.get(key) or "").strip()
        if value:
            command.extend([option, value])

    thinking_budget = str(settings.get("gst_thinking_budget") or "").strip()
    thinking_level = str(settings.get("gst_thinking_level") or "").strip()
    if thinking_budget and thinking_level:
        if "2.5" in model:
            thinking_level = ""
        else:
            thinking_budget = ""
    if thinking_budget:
        command.extend(["--thinking-budget", thinking_budget])
    if thinking_level:
        command.extend(["--thinking-level", thinking_level])
    for option, key, default in (
        ("--skip-upgrade", "gst_skip_upgrade", True),
        ("--quiet", "gst_quiet", True),
        ("--paid-quota", "gst_paid_quota", False),
        ("--progress-log", "gst_progress_log", False),
        ("--thoughts-log", "gst_thoughts_log", False),
        ("--token-report", "gst_token_report", False),
        ("--no-streaming", "gst_no_streaming", False),
        ("--no-thinking", "gst_no_thinking", False),
    ):
        if bool(settings.get(key, default)):
            command.append(option)
    if description:
        command.extend(["--description", description])
    return command


def _result_output_tail(result: subprocess.CompletedProcess[str]) -> str:
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    parts = []
    if stdout:
        parts.append(f"stdout: {stdout[-GST_OUTPUT_TAIL_LENGTH:]}")
    if stderr:
        parts.append(f"stderr: {stderr[-GST_OUTPUT_TAIL_LENGTH:]}")
    return "\n".join(parts) or "<no output>"


def _format_gst_failure(result: subprocess.CompletedProcess[str]) -> str:
    return f"gst failed with exit {result.returncode}: {_result_output_tail(result)}"


def _result_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        part
        for part in (
            str(getattr(result, "stdout", "") or ""),
            str(getattr(result, "stderr", "") or ""),
        )
        if part
    )


def _is_daily_quota_error(output: str) -> bool:
    lowered = output.lower()
    daily_markers = (
        "generaterequestsperdayperprojectpermodel",
        "per_model_per_day",
        "per model per day",
        "requests per day",
    )
    return ("429" in lowered or "resource_exhausted" in lowered) and any(
        marker in lowered for marker in daily_markers
    )


def _is_provider_unavailable(output: str) -> bool:
    lowered = output.lower()
    return any(
        marker in lowered
        for marker in (
            "503 unavailable",
            "code': 503",
            '"code": 503',
            "currently experiencing high demand",
            "model is overloaded",
            "429 resource_exhausted",
            "code': 429",
            '"code": 429',
        )
    )


def _last_gst_error(output: str) -> str:
    """Return the error gst stopped on, not earlier errors it recovered from."""
    index = output.lower().rfind("last error:")
    return output[index:] if index >= 0 else output


def _is_content_retry_error(output: str) -> bool:
    lowered = output.lower()
    return (
        ("expected" in lowered and "lines" in lowered and "got" in lowered)
        or "line count mismatch" in lowered
    )


def _int_setting(settings: dict[str, Any], key: str, env_key: str, default: int) -> int:
    raw_value = settings.get(key)
    if raw_value in (None, ""):
        raw_value = os.getenv(env_key, str(default))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


class _TranslationFiles:
    """The gst work-file protocol, shared by execution and progress reporting."""

    def __init__(self, job: dict[str, Any]) -> None:
        self.source = Path(str(job["subtitle_path"]))
        self.output = Path(str(job.get("output_path") or target_output_path(
            str(self.source), str(job.get("target_code") or "zh"),
        )))
        self.partial = self.output.with_name(f"{self.output.stem}.partial.srt")
        self.progress = self.source.with_suffix(".progress")

    def checkpoint(self) -> int | None:
        try:
            data = json.loads(self.progress.read_text(encoding="utf-8"))
            line = data.get("line") if isinstance(data, dict) else None
            return int(line) if line is not None else None
        except (OSError, TypeError, ValueError, OverflowError):
            return None

    def snapshot(self) -> dict[str, int]:
        result = {}
        line = self.checkpoint()
        if line is not None:
            result["progress_checkpoint"] = line
        try:
            result["partial_bytes"] = self.partial.stat().st_size
        except OSError:
            pass
        return result

    def prepare(self) -> bool:
        """Preserve resumable output; report whether its checkpoint is trusted."""
        can_resume = self.partial.exists() and self.partial.stat().st_size > 0 and self.progress.exists()
        if self.partial.exists() and not can_resume:
            self.partial.unlink()
        line = self.checkpoint() if can_resume else None
        return line is not None and line > 1

    def discard_untrusted_retry(self) -> None:
        line = self.checkpoint()
        if line is not None and line > 1:
            return
        for path in (self.partial, self.progress):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logging.warning("Failed to remove stale gst retry state %s: %s", path, exc)

    def publish(self) -> str:
        if not self.partial.exists() or self.partial.stat().st_size == 0:
            raise RuntimeError(f"gst did not create a non-empty output file: {self.partial}")
        if self.output.exists():
            self.partial.unlink()
            return "skipped-existing-output"
        self.partial.replace(self.output)
        return "translated"


def translation_progress(job: dict[str, Any]) -> dict[str, int]:
    """Read observable translation progress without exposing gst file conventions."""
    if not job.get("subtitle_path"):
        return {}
    try:
        return _TranslationFiles(job).snapshot()
    except (OSError, TypeError, ValueError):
        return {}


def translation_environment(settings: dict[str, Any], base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env if base_env is not None else os.environ)
    gemini_api_key = str(settings.get("gemini_api_key") or "")
    gemini_api_key2 = str(settings.get("gemini_api_key2") or "")
    if gemini_api_key:
        env["GEMINI_API_KEY"] = gemini_api_key
        env["GEMINI_API_KEY1"] = gemini_api_key
    if gemini_api_key2:
        env["GEMINI_API_KEY2"] = gemini_api_key2
    return env


def run_translation(job: dict[str, Any], description: str, settings: dict[str, Any]) -> str:
    files = _TranslationFiles(job)
    input_srt = str(files.source)
    output_srt = str(files.output)
    if files.output.exists():
        return "skipped-existing-output"
    can_resume_from_progress = files.prepare()

    primary_batch_size = _int_setting(settings, "gst_batch_size", "GST_BATCH_SIZE", 500)
    retry_batch_size = _int_setting(settings, "gst_retry_batch_size", "GST_RETRY_BATCH_SIZE", 300)
    command_settings = settings
    command_batch_size = primary_batch_size
    if can_resume_from_progress and retry_batch_size > 0 and retry_batch_size < primary_batch_size:
        logging.info("Resuming gst partial output with fallback batch size %s", retry_batch_size)
        command_settings = dict(settings)
        command_settings["gst_batch_size"] = retry_batch_size
        command_batch_size = retry_batch_size

    logging.info("Running translation: %s -> %s", input_srt, output_srt)

    while True:
        command = build_gst_command(
            input_srt,
            str(files.partial),
            description,
            target_language=str(job.get("target_language") or os.getenv("GST_TARGET_LANGUAGE", "Simplified Chinese")),
            gst_settings=command_settings,
        )
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=translation_environment(settings),
        )
        if result.returncode == 0:
            break
        result_output = _result_output(result)
        # Sanitize before retaining diagnostics in queue files or worker logs.
        for key, value in translation_environment(settings).items():
            if key.upper().endswith("API_KEY") or key.upper().endswith("API_KEY2"):
                if value:
                    result_output = result_output.replace(value, "[REDACTED]")
        result_output = re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", result_output)
        # Format from sanitized output rather than leaking the original streams.
        failure = f"gst failed with exit {result.returncode}: {result_output[-GST_OUTPUT_TAIL_LENGTH:]}"
        if _is_daily_quota_error(result_output):
            raise DailyQuotaExceededError(failure)
        overload_exhausted = "model is still overloaded after" in result_output.lower()
        exhausted = overload_exhausted or (
            "stopping script due to reaching" in result_output.lower()
            and "consecutive errors" in result_output.lower()
        )
        if exhausted:
            # An outage outlasts gst's short internal retries; the queue backs off instead.
            # Any other last error (for example invalid content) still fails immediately.
            if overload_exhausted or _is_provider_unavailable(_last_gst_error(result_output)):
                raise ProviderUnavailableError(failure)
            raise RuntimeError(
                "Translation stopped at the upstream retry limit; automatic queue retries disabled. "
                "Retry manually after checking the provider error or model settings. " + failure
            )
        if _is_provider_unavailable(result_output):
            raise ProviderUnavailableError(failure)
        if (
            result.returncode == 130
            and _is_content_retry_error(result_output)
            and retry_batch_size > 0
            and retry_batch_size < command_batch_size
        ):
            logging.warning(
                "gst returned invalid subtitle content with batch size %s; retrying with batch size %s. %s",
                command_batch_size,
                retry_batch_size,
                failure,
            )
            files.discard_untrusted_retry()
            command_settings = dict(settings)
            command_settings["gst_batch_size"] = retry_batch_size
            command_batch_size = retry_batch_size
            continue
        break
    if result.returncode != 0:
        raise RuntimeError(_format_gst_failure(result))
    return files.publish()
