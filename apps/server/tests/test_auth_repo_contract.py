"""Contract of the console account storage on the real SQLite store (ADR-12)."""

import secrets
import tempfile
import unittest
from pathlib import Path

from subserver.domain.ports import Account
from subserver.adapters.store import Store


class AuthRepoContract:
    """Mixed into a TestCase that provides ``self.repo``."""

    repo: Store

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


class ConsoleAuthOnAMemoryStoreTests(unittest.TestCase):
    """ConsoleAuth only needs the port: no SQLite anywhere in this test."""

    def test_seed_verify_and_change_password_through_the_port(self):
        from subserver.services.auth import ConsoleAuth

        repo = Store(":memory:")
        auth = ConsoleAuth.from_env({"CONSOLE_USER": "admin", "CONSOLE_PASSWORD": "password-1"}, repo)
        self.assertTrue(auth.verify("admin", "password-1"))
        secret = auth.session_secret()
        auth.change_password("password-2")
        self.assertTrue(auth.verify("admin", "password-2"))
        self.assertFalse(auth.verify("admin", "password-1"))
        self.assertNotEqual(auth.session_secret(), secret)


if __name__ == "__main__":
    unittest.main()
