"""HTTP Basic Auth for the web console and API (SPEC NFR-5).

The account is seeded from the environment on the first run and then lives in
the SQLite ``auth`` table as a scrypt hash (a password changed in the console wins
over the env). Passwords are never written to ``config.json``, never placed in
``RuntimeContext``, and never returned by an API; the running server keeps a
single ``ConsoleAuth`` handle for the handler.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit

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


MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """``scrypt$N$r$p$salt$hash`` (base64); a fresh random salt per password."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_raw = base64.b64decode(expected, validate=True)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt, validate=True),
            n=int(n), r=int(r), p=int(p), dklen=len(expected_raw),
        )
    except (ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(digest, expected_raw)


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ConsoleAuth:
    """The one console account.

    With a ``Store`` the account lives in its ``auth`` table (scrypt hash) and the
    password can be changed from the console; ``CONSOLE_USER``/``CONSOLE_PASSWORD``
    only seed it on the first run (or overwrite it under ``CONSOLE_PASSWORD_RESET``).
    Without a store (tests, scripts) the env pair is compared directly.
    """

    enabled: bool = True
    # repr=False keeps the secrets out of any accidental log/traceback of this object.
    user: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    accounts: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str], store: Any = None) -> "ConsoleAuth":
        if (env.get("CONSOLE_AUTH") or "").strip().lower() == "off":
            return cls(enabled=False)
        user = env.get("CONSOLE_USER") or ""
        password = env.get("CONSOLE_PASSWORD") or ""
        if store is None:
            return cls(enabled=True, user=user, password=password)
        reset = _flag(env.get("CONSOLE_PASSWORD_RESET"))
        existing = store.auth_account()
        if user and password and (existing is None or reset):
            store.save_auth_account(user, hash_password(password))
            if existing is not None:
                logging.warning(
                    "CONSOLE_PASSWORD_RESET is set: the console account was overwritten from "
                    "CONSOLE_USER / CONSOLE_PASSWORD. Remove CONSOLE_PASSWORD_RESET now, otherwise every "
                    "restart resets it again and a password changed in Settings is lost."
                )
        elif reset:
            logging.warning(
                "CONSOLE_PASSWORD_RESET is set but CONSOLE_USER / CONSOLE_PASSWORD are not both set; "
                "the stored account was left unchanged."
            )
        return cls(enabled=True, accounts=store)

    @property
    def name(self) -> str:
        """The account's user name ('' when there is none yet)."""
        if self.accounts is not None:
            account = self.accounts.auth_account()
            return account["user"] if account else ""
        return self.user

    @property
    def configured(self) -> bool:
        if not self.enabled:
            return True
        if self.accounts is not None:
            return self.accounts.auth_account() is not None
        return bool(self.user and self.password)

    @property
    def can_change_password(self) -> bool:
        return self.enabled and self.accounts is not None

    def change_password(self, new_password: str) -> None:
        if not self.can_change_password:
            raise RuntimeError("the password is managed outside the console")
        account = self.accounts.auth_account()
        if account is None:
            raise RuntimeError("there is no console account yet")
        self.accounts.save_auth_account(account["user"], hash_password(new_password))

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
        if not self.configured:
            return UNCONFIGURED
        credentials = _parse_basic(authorization)
        if credentials is None:
            return UNAUTHORIZED
        return ALLOW if self.verify(*credentials) else UNAUTHORIZED

    def user_matches(self, user: str) -> bool:
        return hmac.compare_digest(user.encode("utf-8"), self.name.encode("utf-8"))

    def password_matches(self, password: str) -> bool:
        if self.accounts is not None:
            account = self.accounts.auth_account()
            return bool(account) and check_password(password, account["password_hash"])
        return bool(self.password) and hmac.compare_digest(password.encode("utf-8"), self.password.encode("utf-8"))

    def verify(self, user: str, password: str) -> bool:
        """Compare a user/password pair (login form or Basic Auth) with the account."""
        if not self.configured:
            return False
        # Evaluate both comparisons so timing does not reveal which part was wrong.
        user_ok = self.user_matches(user)
        password_ok = self.password_matches(password)
        return user_ok and password_ok


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
    if not auth.user_matches(user):
        same_ignoring_case = user.casefold() == auth.name.casefold()
        return "wrong user name" + (" (right letters, wrong capitals: auto-capitalisation?)" if same_ignoring_case else "")
    if not auth.password_matches(password):
        # Only an env-held password can be compared ignoring case; a stored hash cannot.
        same_ignoring_case = auth.accounts is None and password.casefold() == auth.password.casefold()
        return "wrong password" + (" (right letters, wrong capitals: auto-capitalisation?)" if same_ignoring_case else "")
    return "accepted"


SESSION_COOKIE = "gst_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600
MAX_FAILURES = 5
LOCK_SECONDS = 300


class SessionStore:
    """In-memory login sessions for the form login (POC: they do not survive a restart).

    The cookie carries a random token; only its SHA-256 is kept, so a memory dump
    or a debug print of the store cannot be replayed as a cookie. A session expires
    after ``ttl`` seconds without use (sliding).
    """

    def __init__(self, ttl: float = SESSION_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[str, float]] = {}  # token hash -> (user, last seen)

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, user: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if now - v[1] < self._ttl}
            self._sessions[self._key(token)] = (user, now)
        return token

    def lookup(self, token: str | None) -> str | None:
        """The user a token belongs to, or None; a hit refreshes the expiry."""
        if not token:
            return None
        key = self._key(token)
        now = self._clock()
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return None
            if now - entry[1] >= self._ttl:
                del self._sessions[key]
                return None
            self._sessions[key] = (entry[0], now)
            return entry[0]

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(self._key(token), None)

    def revoke_all(self) -> None:
        with self._lock:
            self._sessions.clear()


class LoginThrottle:
    """Lock an address for five minutes after five wrong logins in a row (form and Basic Auth share it)."""

    def __init__(self, max_failures: int = MAX_FAILURES, lock_seconds: float = LOCK_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max = max_failures
        self._lock_seconds = lock_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}  # address -> (failures, locked until)

    def locked_for(self, address: str) -> int:
        """Seconds left on the lock for this address, 0 when it may try."""
        with self._lock:
            _, until = self._state.get(address, (0, 0.0))
            return max(0, math.ceil(until - self._clock()))

    def fail(self, address: str) -> bool:
        """Count a wrong login; True when this one triggered the lock."""
        now = self._clock()
        with self._lock:
            count, until = self._state.get(address, (0, 0.0))
            if until and until <= now:  # a lock that ran out starts a fresh count
                count, until = 0, 0.0
            count += 1
            if count >= self._max:
                self._state[address] = (0, now + self._lock_seconds)
                return True
            self._state[address] = (count, until)
            return False

    def success(self, address: str) -> None:
        with self._lock:
            self._state.pop(address, None)


def session_token(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def session_cookie(token: str, *, secure: bool, max_age: int = SESSION_TTL_SECONDS) -> str:
    """Set-Cookie value; an empty token with max_age=0 clears the cookie."""
    parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def same_origin(origin: str | None, host: str | None) -> bool:
    """A browser write must come from this very host; requests without Origin (scripts) pass."""
    if origin is None:
        return True
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


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
