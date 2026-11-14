"""Contract of the AuthRepo port (SPEC criterion 10b): one set of checks, run on the real SQLite adapter
and on an in-memory fake, so a fake that drifts from the real behavior fails here, not in some other test."""

import secrets
import tempfile
import unittest
from pathlib import Path

from gst_worker.ports import Account, AuthRepo
from gst_worker.store import Store


class InMemoryAuthRepo:
    """Fake AuthRepo for tests that need an account without a database."""

    def __init__(self) -> None:
        self._account: Account | None = None
        self._secret: str | None = None

    def auth_account(self) -> Account | None:
        return self._account

    def save_auth_account(self, user: str, password_hash: str) -> None:
        self._account = Account(user=user, password_hash=password_hash)
        self._secret = secrets.token_hex(32)

    def auth_session_secret(self) -> str | None:
        return self._secret


class AuthRepoContract:
    """Mixed into a TestCase that provides ``self.repo``."""

    repo: AuthRepo

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.repo, AuthRepo)

    def test_empty_repo_has_no_account_and_no_secret(self):
        self.assertIsNone(self.repo.auth_account())
        self.assertIsNone(self.repo.auth_session_secret())

    def test_returns_a_named_immutable_type_not_a_dict(self):
        self.repo.save_auth_account("admin", "hash-1")
        account = self.repo.auth_account()
        self.assertIsInstance(account, Account)
        self.assertEqual((account.user, account.password_hash), ("admin", "hash-1"))
        with self.assertRaises(AttributeError):
            account.user = "other"

    def test_keeps_one_account_and_the_last_write_wins(self):
        self.repo.save_auth_account("a", "h1")
        self.repo.save_auth_account("b", "h2")
        self.assertEqual(self.repo.auth_account(), Account("b", "h2"))

    def test_session_secret_is_stable_hex_until_the_account_is_written_again(self):
        self.repo.save_auth_account("a", "h1")
        first = self.repo.auth_session_secret()
        self.assertEqual(len(bytes.fromhex(first)), 32)
        self.assertEqual(self.repo.auth_session_secret(), first)
        self.repo.save_auth_account("a", "h1")  # even an identical write rotates it
        self.assertNotEqual(self.repo.auth_session_secret(), first)

    def test_hash_never_appears_in_repr(self):
        self.repo.save_auth_account("a", "very-secret-hash")
        self.assertNotIn("very-secret-hash", repr(self.repo.auth_account()))


class SqliteAuthRepoTests(AuthRepoContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Store(Path(directory.name) / "app.db")
        self.addCleanup(self.repo.close)


class InMemoryAuthRepoTests(AuthRepoContract, unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryAuthRepo()


class ConsoleAuthOnAFakeTests(unittest.TestCase):
    """ConsoleAuth only needs the port: no SQLite anywhere in this test."""

    def test_seed_verify_and_change_password_through_the_port(self):
        from gst_worker.auth import ConsoleAuth

        repo = InMemoryAuthRepo()
        auth = ConsoleAuth.from_env({"CONSOLE_USER": "admin", "CONSOLE_PASSWORD": "password-1"}, repo)
        self.assertTrue(auth.verify("admin", "password-1"))
        secret = auth.session_secret()
        auth.change_password("password-2")
        self.assertTrue(auth.verify("admin", "password-2"))
        self.assertFalse(auth.verify("admin", "password-1"))
        self.assertNotEqual(auth.session_secret(), secret)


if __name__ == "__main__":
    unittest.main()
