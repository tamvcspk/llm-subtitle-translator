"""Deep interface for a translation attempt's work-file protocol."""
from typing import Any
from . import translation

class TranslationAttempt:
    def progress(self, job: dict[str, Any]) -> dict[str, Any]:
        return translation.translation_progress(job)
    def run(self, job: dict[str, Any], description: str, settings: dict[str, Any]) -> str:
        return translation.run_translation(job, description, settings)

DEFAULT_ATTEMPT = TranslationAttempt()
