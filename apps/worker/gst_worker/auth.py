"""HTTP Basic Auth for the web console and API (SPEC NFR-5).

Credentials come from the environment at startup only. They are never written
to ``config.json``, never placed in ``RuntimeContext``, and never returned by an
API; the running server keeps a single ``ConsoleAuth`` handle for the handler.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

REALM = "LLM Subtitle Translator"

ALLOW = "allow"
UNAUTHORIZED = "unauthorized"
UNCONFIGURED = "unconfigured"

UNCONFIGURED_MESSAGE = (
    "Console authentication is not configured. Set CONSOLE_USER and "
    "CONSOLE_PASSWORD in the worker environment and restart it, or set "
    "CONSOLE_AUTH=off to run without authentication (trusted network only). "
    "The queue worker keeps running."
)


@dataclass(frozen=True)
class ConsoleAuth:
    enabled: bool = True
    # repr=False keeps the secrets out of any accidental log/traceback of this object.
    user: str = field(default="", repr=False)
    password: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ConsoleAuth":
        if (env.get("CONSOLE_AUTH") or "").strip().lower() == "off":
            return cls(enabled=False)
        return cls(
            enabled=True,
            user=env.get("CONSOLE_USER") or "",
            password=env.get("CONSOLE_PASSWORD") or "",
        )

    @property
    def configured(self) -> bool:
        return not self.enabled or bool(self.user and self.password)

    def log_startup(self) -> None:
        if not self.enabled:
            logging.warning(
                "CONSOLE_AUTH=off: the web console and API accept requests without authentication. "
                "Only run this on a trusted network."
            )
        elif not self.configured:
            logging.error(
                "Console authentication is required but CONSOLE_USER / CONSOLE_PASSWORD are not both set; "
                "the web console answers 503 until they are set. The queue worker is unaffected."
            )

    def check(self, authorization: str | None) -> str:
        """Return ALLOW, UNAUTHORIZED or UNCONFIGURED for an Authorization header value."""
        if not self.enabled:
            return ALLOW
        if not (self.user and self.password):
            return UNCONFIGURED
        credentials = _parse_basic(authorization)
        if credentials is None:
            return UNAUTHORIZED
        user, password = credentials
        # Evaluate both comparisons so timing does not reveal which part was wrong.
        user_ok = hmac.compare_digest(user.encode("utf-8"), self.user.encode("utf-8"))
        password_ok = hmac.compare_digest(password.encode("utf-8"), self.password.encode("utf-8"))
        return ALLOW if (user_ok and password_ok) else UNAUTHORIZED


def explain_failure(auth: ConsoleAuth, authorization: str | None) -> str:
    """Why a request was refused, for the log: never the credentials themselves, only which part failed."""
    if not authorization:
        return "no credentials were sent (the login prompt was dismissed or the client did not show one)"
    scheme = authorization.strip().partition(" ")[0].lower()
    if scheme != "basic":
        return f"unsupported authorization scheme {scheme[:20]!r}"
    credentials = _parse_basic(authorization)
    if credentials is None:
        return "the credentials the client sent could not be read"
    user, password = credentials
    if not hmac.compare_digest(user.encode("utf-8"), auth.user.encode("utf-8")):
        same_ignoring_case = user.casefold() == auth.user.casefold()
        return "wrong user name" + (" (right letters, wrong capitals: auto-capitalisation?)" if same_ignoring_case else "")
    if not hmac.compare_digest(password.encode("utf-8"), auth.password.encode("utf-8")):
        same_ignoring_case = password.casefold() == auth.password.casefold()
        return "wrong password" + (" (right letters, wrong capitals: auto-capitalisation?)" if same_ignoring_case else "")
    return "accepted"


def _parse_basic(header: str | None) -> tuple[str, str] | None:
    if not header:
        return None
    scheme, _, token = header.strip().partition(" ")
    if scheme.lower() != "basic" or not token.strip():
        return None
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    user, sep, password = decoded.partition(":")
    if not sep:
        return None
    return user, password
