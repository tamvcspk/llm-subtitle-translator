"""One filter that strips secrets from everything the worker writes down (FR-19.3, ADR-15 point 4).

Logs, events, prompt bodies and error reports all pass through :class:`Redactor`. It knows two kinds of
secret: the exact values it was told about (the configured API keys, the console password) and the shapes
secrets take (a Google key, a ``Bearer`` token, an ``Authorization`` or ``Cookie`` header, the session cookie,
a scrypt hash, ``password=...``). A shape needs no registration, so a key typed into the console a moment ago
is still caught in an error message. Pure: no I/O.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[REDACTED]"
# Settings that hold a secret value; their values are registered by `register_settings`.
SECRET_SETTING_KEYS = (
    "bazarr_api_key", "gemini_api_key", "gemini_api_key2", "tmdb_api_key", "openai_compat_api_key",
)
# A value shorter than this is never registered: replacing "abc" everywhere would wreck the text around it.
MIN_SECRET_LENGTH = 6

# Shapes that are secrets wherever they appear, subtitle text included.
_STRICT = (
    (re.compile(r"AIza[A-Za-z0-9_-]+"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {REDACTED}"),
    (re.compile(r"scrypt\$[A-Za-z0-9$+/=._-]+"), REDACTED),
    (re.compile(r"\bgst_session=[^;\s'\"]+"), f"gst_session={REDACTED}"),
    (
        re.compile(
            r"(?im)^([ \t]*(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-goog-api-key)[ \t]*:[ \t]*).*$"
        ),
        rf"\1{REDACTED}",
    ),
    # The same headers inside a dict or JSON dump: 'Authorization': 'Basic abc'
    (
        re.compile(
            r"(?i)(['\"](?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-goog-api-key)['\"]\s*:\s*)"
            r"(['\"])(?:\\.|(?!\2).)*\2"
        ),
        rf"\1\2{REDACTED}\2",
    ),
)
# Shapes that are only safe to assume in the worker's own messages (a subtitle may say "password: swordfish").
_LOOSE = (
    (
        re.compile(
            r"(?i)\b(password|passwd|api[_-]?key|secret|session[_-]?secret|access[_-]?token|auth[_-]?token)\b"
            r"(['\"]?\s*[:=]\s*['\"]?)([^'\"\s,&;}]+)"
        ),
        rf"\1\2{REDACTED}",
    ),
    (re.compile(r"(?i)([?&](?:key|api_key|apikey|token|password)=)[^&\s'\"]+"), rf"\1{REDACTED}"),
)
# A mapping key that names a secret: the value is replaced whatever it looks like.
_SECRET_KEY = re.compile(
    r"(?i)^(?:.*[_\-.])?(?:password|passwd|secret|api[_-]?key|apikey|authorization|cookie|set-cookie|"
    r"credentials?|session|access[_-]?token|auth[_-]?token|token)$"
)


class Redactor:
    """Replaces known secret values and secret shapes. Safe to share between threads."""

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._lock = threading.Lock()
        self._pinned: frozenset[str] = frozenset()  # kept for the life of the process (keys, an env password)
        self._current: frozenset[str] = frozenset()  # what `set_values` replaces
        self._values: tuple[str, ...] = ()
        self.set_values(values)

    @staticmethod
    def _usable(values: Iterable[str]) -> frozenset[str]:
        return frozenset(str(v) for v in values if v and len(str(v)) >= MIN_SECRET_LENGTH)

    def _rebuild(self) -> None:
        # Longest first, so a key that contains another one goes whole.
        self._values = tuple(sorted(self._pinned | self._current, key=len, reverse=True))

    def set_values(self, values: Iterable[str]) -> None:
        """Replace the registered secret values (the pinned ones stay)."""
        with self._lock:
            self._current = self._usable(values)
            self._rebuild()

    def add_values(self, values: Iterable[str]) -> None:
        """Register values that stay hidden for the rest of the process, whatever the settings later say."""
        with self._lock:
            self._pinned = self._pinned | self._usable(values)
            self._rebuild()

    def register_settings(self, settings: Mapping[str, Any] | None, extra: Iterable[str] = ()) -> None:
        """Take the secret values out of the worker settings (and any ``extra`` ones). They are kept for the life
        of the process, so a key that was just replaced is still hidden in the lines written a moment ago."""
        found = [str((settings or {}).get(name) or "") for name in SECRET_SETTING_KEYS]
        self.add_values([*found, *extra])

    def redact(self, text: str, *, loose: bool = True) -> str:
        """``text`` without any secret. ``loose=False`` leaves ``password=...`` style text alone (for subtitle
        bodies, where such words are dialogue)."""
        if not text:
            return text
        for value in self._values:
            if value in text:
                text = text.replace(value, REDACTED)
        for pattern, replacement in _STRICT:
            text = pattern.sub(replacement, text)
        if loose:
            for pattern, replacement in _LOOSE:
                text = pattern.sub(replacement, text)
        return text

    def clean(self, value: Any, *, loose: bool = True) -> Any:
        """``value`` (strings, lists, dicts, nested) with every secret removed; a mapping key that names a secret
        loses its whole value. Numbers, booleans and None pass through."""
        if isinstance(value, str):
            return self.redact(value, loose=loose)
        if isinstance(value, Mapping):
            out: dict[Any, Any] = {}
            for key, item in value.items():
                if isinstance(key, str) and _SECRET_KEY.match(key) and isinstance(item, (str, bytes)) and item:
                    out[key] = REDACTED
                else:
                    out[key] = self.clean(item, loose=loose)
            return out
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.clean(item, loose=loose) for item in value]
        if isinstance(value, bytes):
            return self.redact(value.decode("utf-8", errors="replace"), loose=loose)
        return value


# The process-wide instance: logging, telemetry and the legacy ``translation.redact`` all use this one, so a key
# registered when settings load is hidden everywhere at once.
DEFAULT = Redactor()
