"""The console's single account and the key its sessions are signed with."""

from __future__ import annotations

import secrets
import time

from ...domain.ports import Account
from .base import Repo


class AuthRepo(Repo):
    """The console's single account and the key its sessions are signed with."""

    # -- auth --------------------------------------------------------------
    def auth_account(self) -> Account | None:
        with self._lock:
            row = self._db.execute("SELECT user, password_hash FROM auth WHERE id = 1").fetchone()
        return Account(user=row["user"], password_hash=row["password_hash"]) if row else None

    def save_auth_account(self, user: str, password_hash: str) -> None:
        """Create or overwrite the account. A fresh session secret goes with it, so every session ends."""
        with self._lock:
            self._db.execute(
                "INSERT INTO auth (id, user, password_hash, session_secret, updated_at) VALUES (1, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET user = excluded.user,"
                " password_hash = excluded.password_hash, session_secret = excluded.session_secret,"
                " updated_at = excluded.updated_at",
                (user, password_hash, secrets.token_hex(32), time.time()),
            )

    def auth_session_secret(self) -> str | None:
        """Hex session secret of the account; None without an account (a row from before schema 5 gets one)."""
        with self._lock:
            row = self._db.execute("SELECT session_secret FROM auth WHERE id = 1").fetchone()
            if row is None:
                return None
            if not row["session_secret"]:
                self._db.execute("UPDATE auth SET session_secret = ? WHERE id = 1", (secrets.token_hex(32),))
                row = self._db.execute("SELECT session_secret FROM auth WHERE id = 1").fetchone()
            return row["session_secret"]
