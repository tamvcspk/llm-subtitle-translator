"""Form login (P0c POC): cookie session beside Basic Auth, lockout, CSRF check, no leaks."""
import base64
import http.client
import inspect
import json
import logging
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import worker
from gst_worker.auth import (
    LOCK_SECONDS,
    SESSION_COOKIE,
    UNCONFIGURED,
    ConsoleAuth,
    LoginThrottle,
    SessionStore,
    check_password,
    hash_password,
    same_origin,
    session_cookie,
    session_token,
)
from gst_worker.store import Store

USER = "admin"
PASSWORD = "s3cret-pässword"
HTML = {"Accept": "text/html,application/xhtml+xml"}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SessionStoreTests(unittest.TestCase):
    def test_token_is_random_and_resolves_to_user(self):
        store = SessionStore()
        first, second = store.create("a"), store.create("a")
        self.assertNotEqual(first, second)
        self.assertEqual(store.lookup(first), "a")
        self.assertIsNone(store.lookup("nope"))
        self.assertIsNone(store.lookup(None))

    def test_expires_ttl_seconds_after_it_was_issued(self):
        clock = FakeClock()
        store = SessionStore(ttl=100, clock=clock)
        token = store.create("a")
        clock.now += 99
        self.assertEqual(store.lookup(token), "a")
        clock.now += 1
        self.assertIsNone(store.lookup(token))

    def test_revoke_and_revoke_all(self):
        store = SessionStore()
        one, two = store.create("a"), store.create("a")
        store.revoke(one)
        self.assertIsNone(store.lookup(one))
        self.assertEqual(store.lookup(two), "a")
        store.revoke_all()
        self.assertIsNone(store.lookup(two))

    def test_store_keeps_no_per_session_state(self):
        store = SessionStore()
        token = store.create("a")
        self.assertNotIn(token, repr(store.__dict__))
        self.assertEqual(store._revoked, {})

    def test_tampered_or_foreign_tokens_are_refused(self):
        store, other = SessionStore(), SessionStore()
        token = store.create("a")
        user, token_id, issued, signature = token.split(".")
        forged_user = ".".join([base64.urlsafe_b64encode(b"root").decode().rstrip("="), token_id, issued, signature])
        later = ".".join([user, token_id, str(int(issued) + 99999), signature])
        for bad in (forged_user, later, token[:-2] + "xx", token + ".x", "a.b.c.d", "....", "x" * 5000):
            with self.subTest(bad=bad[:30]):
                self.assertIsNone(store.lookup(bad))
        self.assertIsNone(other.lookup(token))  # signed with a different secret

    def test_token_from_the_future_is_refused(self):
        clock = FakeClock()
        store = SessionStore(clock=clock)
        token = store.create("a")
        clock.now -= 3600
        self.assertIsNone(store.lookup(token))

    def test_resolve_hands_back_a_fresh_token_after_an_hour_and_it_slides_the_expiry(self):
        clock = FakeClock()
        store = SessionStore(ttl=100000, clock=clock)
        token = store.create("a")
        clock.now += 60
        self.assertEqual(store.resolve(token), ("a", None))
        clock.now += 3600
        user, fresh = store.resolve(token)
        self.assertEqual(user, "a")
        self.assertNotEqual(fresh, token)
        clock.now += 99000  # past the first token's expiry, within the refreshed one's
        self.assertIsNone(store.lookup(token))
        self.assertEqual(store.lookup(fresh), "a")

    def test_logout_denies_the_token_and_its_refreshed_copies(self):
        clock = FakeClock()
        store = SessionStore(clock=clock)
        token = store.create("a")
        clock.now += 7200
        _, fresh = store.resolve(token)
        store.revoke(token)
        self.assertIsNone(store.lookup(token))
        self.assertIsNone(store.lookup(fresh))
        self.assertIsNotNone(store.lookup(store.create("a")))

    def test_stored_secret_survives_a_restart_and_rotates_on_password_change(self):
        db = Store(":memory:")
        self.addCleanup(db.close)
        auth = ConsoleAuth.from_env({"CONSOLE_USER": "a", "CONSOLE_PASSWORD": "password-1"}, db)
        token = SessionStore.for_auth(auth).create("a")
        restarted = SessionStore.for_auth(ConsoleAuth.from_env({}, db))  # a new process: same database
        self.assertEqual(restarted.lookup(token), "a")
        auth.change_password("password-2")
        self.assertIsNone(restarted.lookup(token))
        reset = SessionStore.for_auth(auth).create("a")
        ConsoleAuth.from_env({"CONSOLE_USER": "a", "CONSOLE_PASSWORD": "password-3",
                              "CONSOLE_PASSWORD_RESET": "1"}, db)
        self.assertIsNone(SessionStore.for_auth(auth).lookup(reset))

    def test_account_from_before_schema_5_gets_a_secret_on_first_use(self):
        db = Store(":memory:")
        self.addCleanup(db.close)
        db._db.execute("INSERT INTO auth (id, user, password_hash, updated_at) VALUES (1, 'a', 'h', 1.0)")
        first = db.auth_session_secret()
        self.assertEqual(len(first), 64)
        self.assertEqual(db.auth_session_secret(), first)

    def test_no_account_means_no_sessions(self):
        db = Store(":memory:")
        self.addCleanup(db.close)
        sessions = SessionStore.for_auth(ConsoleAuth.from_env({}, db))
        with self.assertRaises(RuntimeError):
            sessions.create("a")
        self.assertIsNone(sessions.lookup("a.b.1.c"))


class LoginThrottleTests(unittest.TestCase):
    def test_locks_after_five_failures_then_releases(self):
        clock = FakeClock()
        throttle = LoginThrottle(clock=clock)
        for _ in range(4):
            self.assertFalse(throttle.fail("1.2.3.4"))
        self.assertEqual(throttle.locked_for("1.2.3.4"), 0)
        self.assertTrue(throttle.fail("1.2.3.4"))
        self.assertEqual(throttle.locked_for("1.2.3.4"), LOCK_SECONDS)
        self.assertEqual(throttle.locked_for("5.6.7.8"), 0)
        clock.now += LOCK_SECONDS
        self.assertEqual(throttle.locked_for("1.2.3.4"), 0)
        self.assertFalse(throttle.fail("1.2.3.4"))  # fresh count after the lock ran out

    def test_success_resets_the_count(self):
        throttle = LoginThrottle()
        for _ in range(4):
            throttle.fail("a")
        throttle.success("a")
        for _ in range(4):
            self.assertFalse(throttle.fail("a"))


class HelperTests(unittest.TestCase):
    def test_cookie_flags(self):
        value = session_cookie("tok", secure=False)
        for part in ("gst_session=tok", "HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=604800"):
            self.assertIn(part, value)
        self.assertNotIn("Secure", value)
        self.assertIn("Secure", session_cookie("tok", secure=True))
        self.assertIn("Max-Age=0", session_cookie("", secure=False, max_age=0))

    def test_session_token_parsing(self):
        self.assertEqual(session_token(f"a=b; {SESSION_COOKIE}=xyz; c=d"), "xyz")
        self.assertIsNone(session_token("a=b"))
        self.assertIsNone(session_token(None))

    def test_same_origin(self):
        self.assertTrue(same_origin(None, "h:1"))
        self.assertTrue(same_origin("http://h:1", "h:1"))
        self.assertFalse(same_origin("http://evil.example", "h:1"))
        self.assertFalse(same_origin("null", "h:1"))


class LoginServerTests(unittest.TestCase):
    can_change_password = False

    def make_auth(self):
        return ConsoleAuth.from_env({"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        static = Path(self.tmp.name)
        (static / "index.html").write_text("<html>console</html>", encoding="utf-8")
        (static / "login.html").write_text("<html>login-form</html>", encoding="utf-8")
        self.ctx = {
            "static_dir": str(static),
            "queue_dir": str(static / "queue"),
            "log_dir": str(static / "logs"),
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.ConsoleHandler)
        server.ctx = self.ctx
        server.auth = self.make_auth()
        server.sessions = SessionStore.for_auth(server.auth)
        server.throttle = LoginThrottle()
        self.server = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.headers, response.read().decode("utf-8")
        finally:
            conn.close()

    def login(self, user=USER, password=PASSWORD, headers=None):
        return self.request("POST", "/api/auth/login", {"Content-Type": "application/json", **(headers or {})},
                            json.dumps({"user": user, "password": password}))

    def cookie(self):
        status, headers, _ = self.login()
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0]

    # --- unauthenticated -------------------------------------------------

    def test_login_page_is_served_without_credentials(self):
        status, headers, body = self.request("GET", "/login", HTML)
        self.assertEqual(status, 200)
        self.assertIn("login-form", body)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_browser_page_request_is_redirected_to_login(self):
        for path in ("/", "/index.html", "/js/main.js"):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path, HTML)
                self.assertEqual(status, 302)
                self.assertEqual(headers["Location"], "/login")
                self.assertNotIn("console", body)

    def test_browser_api_call_is_401_json_without_basic_prompt(self):
        status, headers, body = self.request("GET", "/api/status", {"Sec-Fetch-Mode": "cors"})
        self.assertEqual(status, 401)
        self.assertNotIn("WWW-Authenticate", headers)
        self.assertIn("authentication required", body)

    def test_script_without_cookie_still_gets_the_basic_challenge(self):
        status, headers, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertTrue(headers["WWW-Authenticate"].startswith("Basic realm="))

    def test_every_route_in_the_handler_is_closed_without_a_session(self):
        paths = set(re.findall(r'"(/api/[A-Za-z0-9_/-]+)"', inspect.getsource(worker.ConsoleHandler)))
        paths |= {"/", "/index.html", "/login.html", "/styles.css", "/js/main.js", "/favicon.svg", "/api/nope"}
        paths -= {"/api/auth/login"}  # the one documented exception (POST)
        self.assertGreater(len(paths), 15)
        for path in sorted(paths):
            for method in ("GET", "POST", "PUT", "DELETE"):
                with self.subTest(method=method, path=path):
                    status, _, body = self.request(method, path, HTML, body=b"{}" if method != "GET" else None)
                    self.assertIn(status, {302, 401})
                    self.assertNotIn("console", body)
        # GET /login is open, but POST /login is not a route and must stay closed.
        self.assertEqual(self.request("POST", "/login", body=b"{}")[0], 401)

    # --- login / session ---------------------------------------------------

    def test_login_sets_a_strict_http_only_cookie_and_it_opens_the_console(self):
        status, headers, body = self.login()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"], USER)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertNotIn(PASSWORD, cookie + body)
        status, _, page = self.request("GET", "/", {"Cookie": cookie.split(";", 1)[0], **HTML})
        self.assertEqual(status, 200)
        self.assertIn("console", page)

    def test_secure_flag_is_added_behind_https_proxy(self):
        _, headers, _ = self.login(headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", headers["Set-Cookie"])

    def test_wrong_credentials_are_401_without_cookie(self):
        for user, password in [(USER, "nope"), ("nobody", PASSWORD), ("", "")]:
            with self.subTest(user=user):
                status, headers, _ = self.login(user, password)
                self.assertEqual(status, 401)
                self.assertNotIn("Set-Cookie", headers)

    def test_me_reports_how_the_caller_got_in(self):
        cookie = self.cookie()
        can = self.can_change_password
        self.assertEqual(json.loads(self.request("GET", "/api/auth/me", {"Cookie": cookie})[2]),
                         {"user": USER, "via": "session", "can_change_password": can})
        basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        self.assertEqual(json.loads(self.request("GET", "/api/auth/me", {"Authorization": basic})[2]),
                         {"user": USER, "via": "basic", "can_change_password": can})

    def test_logout_ends_the_session_and_clears_the_cookie(self):
        cookie = self.cookie()
        status, headers, _ = self.request("POST", "/api/auth/logout", {"Cookie": cookie}, b"{}")
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        status, _, _ = self.request("GET", "/api/auth/me", {"Cookie": cookie, "Sec-Fetch-Mode": "cors"})
        self.assertEqual(status, 401)

    def test_bogus_cookie_is_refused(self):
        status, headers, _ = self.request("GET", "/api/status", {"Cookie": f"{SESSION_COOKIE}=forged"})
        self.assertEqual(status, 401)
        self.assertNotIn("WWW-Authenticate", headers)

    def test_basic_auth_still_works_for_scripts(self):
        basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        status, _, body = self.request("POST", "/api/nope", {"Authorization": basic}, b"{}")
        self.assertEqual(status, 404)
        self.assertIn("not found", body)

    # --- CSRF ---------------------------------------------------------------

    def test_cookie_write_from_another_origin_is_refused(self):
        cookie = self.cookie()
        host = f"127.0.0.1:{self.port}"
        status, _, _ = self.request("POST", "/api/nope", {"Cookie": cookie, "Origin": "http://evil.example"}, b"{}")
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/nope", {"Cookie": cookie, "Origin": f"http://{host}"}, b"{}")
        self.assertEqual(status, 404)  # same origin reaches the router

    def test_cross_origin_login_is_refused(self):
        status, headers, _ = self.login(headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("Set-Cookie", headers)

    # --- lockout ----------------------------------------------------------

    def test_five_wrong_logins_lock_the_address_for_form_and_basic_alike(self):
        for _ in range(5):
            self.assertEqual(self.login(password="nope")[0], 401)
        status, headers, _ = self.login()  # right password, but locked
        self.assertEqual(status, 429)
        self.assertEqual(int(headers["Retry-After"]), LOCK_SECONDS)
        basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        self.assertEqual(self.request("GET", "/api/queue", {"Authorization": basic})[0], 429)

    def test_wrong_basic_attempts_count_towards_the_same_lock(self):
        bad = "Basic " + base64.b64encode(f"{USER}:nope".encode()).decode()
        for _ in range(5):
            self.assertEqual(self.request("GET", "/api/queue", {"Authorization": bad})[0], 401)
        self.assertEqual(self.login()[0], 429)

    def test_a_live_session_survives_the_lock(self):
        cookie = self.cookie()
        for _ in range(5):
            self.login(password="nope")
        status, _, _ = self.request("POST", "/api/nope", {"Cookie": cookie}, b"{}")
        self.assertEqual(status, 404)

    # --- leaks --------------------------------------------------------------

    def test_logs_never_contain_password_or_session_token(self):
        with self.assertLogs(level="INFO") as captured:
            self.login(password="wrong-password-xyz")
            cookie = self.cookie()
            self.request("GET", "/api/status", {"Cookie": cookie, "Sec-Fetch-Mode": "cors"})
            self.request("POST", "/api/auth/logout", {"Cookie": cookie}, b"{}")
            self.request("GET", "/api/status", {"Cookie": cookie})  # now a dead cookie
            logging.info("sentinel")
        text = "\n".join(captured.output)
        token = cookie.split("=", 1)[1]
        for secret in ("wrong-password-xyz", PASSWORD, token, SESSION_COOKIE):
            self.assertNotIn(secret, text)


NEW_PASSWORD = "brand-new-pässword"


class StoredAccountServerTests(LoginServerTests):
    """The whole login suite again with the account in the SQLite store, plus changing the password."""

    can_change_password = True

    def make_auth(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        return ConsoleAuth.from_env({"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD}, store)

    def change(self, headers, current=PASSWORD, new=NEW_PASSWORD):
        return self.request("POST", "/api/auth/password", {"Content-Type": "application/json", **headers},
                            json.dumps({"current": current, "new": new}))

    def test_change_keeps_the_caller_signed_in_and_ends_every_other_session(self):
        mine, other = self.cookie(), self.cookie()
        status, headers, body = self.change({"Cookie": mine})
        self.assertEqual(status, 200)
        fresh = headers["Set-Cookie"].split(";", 1)[0]
        self.assertNotEqual(fresh, mine)
        self.assertNotIn(NEW_PASSWORD, body + headers["Set-Cookie"])
        api = {"Sec-Fetch-Mode": "cors"}
        self.assertEqual(self.request("GET", "/api/auth/me", {"Cookie": mine, **api})[0], 401)
        self.assertEqual(self.request("GET", "/api/auth/me", {"Cookie": other, **api})[0], 401)
        self.assertEqual(self.request("GET", "/api/auth/me", {"Cookie": fresh, **api})[0], 200)

    def test_old_password_stops_working_and_the_new_one_works_for_form_and_basic(self):
        self.change({"Cookie": self.cookie()})
        self.assertEqual(self.login(password=PASSWORD)[0], 401)
        self.assertEqual(self.login(password=NEW_PASSWORD)[0], 200)
        old = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        new = "Basic " + base64.b64encode(f"{USER}:{NEW_PASSWORD}".encode()).decode()
        self.assertEqual(self.request("GET", "/api/queue", {"Authorization": old})[0], 401)
        self.assertEqual(self.request("GET", "/api/queue", {"Authorization": new})[0], 200)

    def test_wrong_current_password_is_403_and_changes_nothing(self):
        cookie = self.cookie()
        status, headers, _ = self.change({"Cookie": cookie}, current="nope")
        self.assertEqual(status, 403)  # not 401: the console must not treat it as a dead session
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.request("GET", "/api/auth/me", {"Cookie": cookie})[0], 200)
        self.assertEqual(self.login()[0], 200)

    def test_new_password_must_be_8_to_256_characters(self):
        cookie = self.cookie()
        for bad in ("", "short", "x" * 257):
            with self.subTest(length=len(bad)):
                self.assertEqual(self.change({"Cookie": cookie}, new=bad)[0], 400)
        self.assertEqual(self.login()[0], 200)
        self.assertEqual(self.change({"Cookie": cookie}, new="x" * 8)[0], 200)

    def test_wrong_current_passwords_count_towards_the_lockout(self):
        cookie = self.cookie()
        for _ in range(5):
            self.assertEqual(self.change({"Cookie": cookie}, current="nope")[0], 403)
        status, headers, _ = self.change({"Cookie": cookie})
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)

    def test_basic_auth_script_can_change_it_without_a_cookie(self):
        basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        status, headers, _ = self.change({"Authorization": basic})
        self.assertEqual(status, 200)
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.login(password=NEW_PASSWORD)[0], 200)

    def test_change_from_another_origin_is_refused(self):
        status, _, _ = self.change({"Cookie": self.cookie(), "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.login()[0], 200)

    def test_unauthenticated_change_is_refused(self):
        self.assertEqual(self.change({"Sec-Fetch-Mode": "cors"})[0], 401)
        self.assertEqual(self.login()[0], 200)

    def test_logs_do_not_contain_either_password(self):
        with self.assertLogs(level="INFO") as captured:
            cookie = self.cookie()
            self.change({"Cookie": cookie}, current="wrong-current-xyz")
            self.change({"Cookie": cookie})
        text = "\n".join(captured.output)
        for secret in ("wrong-current-xyz", PASSWORD, NEW_PASSWORD):
            self.assertNotIn(secret, text)
        self.assertIn("password changed", text)

    def test_session_survives_a_worker_restart(self):
        mine = self.cookie()
        self.server.sessions = SessionStore.for_auth(self.server.auth)  # a new process: same database, empty memory
        self.assertEqual(self.request("GET", "/api/auth/me", {"Cookie": mine, "Sec-Fetch-Mode": "cors"})[0], 200)

    def test_an_old_session_gets_a_fresh_cookie_and_an_expired_one_is_refused(self):
        clock = FakeClock()
        self.server.sessions = SessionStore.for_auth(self.server.auth, clock=clock)
        mine = self.cookie()
        token = mine.split("=", 1)[1]
        clock.now += 60
        self.assertNotIn("Set-Cookie", self.request("GET", "/api/auth/me", {"Cookie": mine})[1])
        clock.now += 7200
        status, headers, _ = self.request("GET", "/api/auth/me", {"Cookie": mine})
        self.assertEqual(status, 200)
        refreshed = headers["Set-Cookie"]
        self.assertIn("HttpOnly", refreshed)
        self.assertIn("SameSite=Strict", refreshed)
        self.assertNotIn(token, refreshed)
        clock.now += 8 * 24 * 3600
        self.assertEqual(self.request("GET", "/api/auth/me", {"Cookie": refreshed.split(";", 1)[0],
                                                              "Sec-Fetch-Mode": "cors"})[0], 401)

    def test_with_auth_off_there_is_nothing_to_change(self):
        server_auth = ConsoleAuth.from_env({"CONSOLE_AUTH": "off"})
        self.assertFalse(server_auth.can_change_password)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def env(self, **extra):
        return {"CONSOLE_USER": USER, "CONSOLE_PASSWORD": PASSWORD, **extra}

    def test_hash_is_salted_scrypt_and_verifies(self):
        first, second = hash_password(PASSWORD), hash_password(PASSWORD)
        self.assertTrue(first.startswith("scrypt$"))
        self.assertNotEqual(first, second)
        self.assertNotIn(PASSWORD, first)
        self.assertTrue(check_password(PASSWORD, first))
        self.assertFalse(check_password("other", first))

    def test_malformed_hashes_never_verify(self):
        for stored in ("", "plain", "scrypt$1$2", "md5$1$1$1$AA==$AA==", "scrypt$x$8$1$AA==$AA==", "scrypt$16384$8$1$!!$!!"):
            with self.subTest(stored=stored):
                self.assertFalse(check_password(PASSWORD, stored))

    def test_first_run_seeds_the_account_from_env_and_stores_only_a_hash(self):
        auth = ConsoleAuth.from_env(self.env(), self.store)
        self.assertTrue(auth.configured)
        self.assertTrue(auth.verify(USER, PASSWORD))
        self.assertFalse(auth.verify(USER, "nope"))
        self.assertFalse(auth.verify("nobody", PASSWORD))
        account = self.store.auth_account()
        self.assertEqual(account.user, USER)
        self.assertNotIn(PASSWORD, account.password_hash)
        self.assertNotIn(PASSWORD, repr(auth))

    def test_without_env_and_without_account_it_is_unconfigured(self):
        auth = ConsoleAuth.from_env({}, self.store)
        self.assertFalse(auth.configured)
        self.assertFalse(auth.verify(USER, PASSWORD))
        self.assertEqual(auth.check(None), UNCONFIGURED)

    def test_stored_account_wins_over_a_changed_env_password(self):
        ConsoleAuth.from_env(self.env(), self.store).change_password(NEW_PASSWORD)
        restarted = ConsoleAuth.from_env(self.env(), self.store)  # the same env as before
        self.assertTrue(restarted.verify(USER, NEW_PASSWORD))
        self.assertFalse(restarted.verify(USER, PASSWORD))

    def test_account_is_still_there_when_env_is_gone(self):
        ConsoleAuth.from_env(self.env(), self.store)
        self.assertTrue(ConsoleAuth.from_env({}, self.store).verify(USER, PASSWORD))

    def test_reset_flag_overwrites_the_account_and_warns(self):
        ConsoleAuth.from_env(self.env(), self.store).change_password(NEW_PASSWORD)
        with self.assertLogs(level="WARNING") as captured:
            auth = ConsoleAuth.from_env(self.env(CONSOLE_USER="root", CONSOLE_PASSWORD="reset-pass-1",
                                                 CONSOLE_PASSWORD_RESET="1"), self.store)
        self.assertTrue(auth.verify("root", "reset-pass-1"))
        self.assertFalse(auth.verify(USER, NEW_PASSWORD))
        self.assertIn("Remove CONSOLE_PASSWORD_RESET", "\n".join(captured.output))
        self.assertNotIn("reset-pass-1", "\n".join(captured.output))

    def test_reset_flag_without_credentials_changes_nothing(self):
        ConsoleAuth.from_env(self.env(), self.store)
        with self.assertLogs(level="WARNING"):
            auth = ConsoleAuth.from_env({"CONSOLE_PASSWORD_RESET": "true"}, self.store)
        self.assertTrue(auth.verify(USER, PASSWORD))

    def test_without_the_flag_env_never_overwrites_an_existing_account(self):
        ConsoleAuth.from_env(self.env(), self.store)
        auth = ConsoleAuth.from_env(self.env(CONSOLE_PASSWORD="different-pass"), self.store)
        self.assertTrue(auth.verify(USER, PASSWORD))
        self.assertFalse(auth.verify(USER, "different-pass"))

    def test_env_only_auth_cannot_change_its_password(self):
        auth = ConsoleAuth.from_env(self.env())
        self.assertFalse(auth.can_change_password)
        with self.assertRaises(RuntimeError):
            auth.change_password(NEW_PASSWORD)


if __name__ == "__main__":
    unittest.main()
