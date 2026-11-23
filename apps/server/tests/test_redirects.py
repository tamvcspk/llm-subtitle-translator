"""P16: following the redirect link of a grounded answer to its page (``adapters.redirects``). A real loopback server stands
in for Google's redirect host; nothing leaves the machine."""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from subserver.adapters.redirects import GOOGLE_REDIRECT_HOSTS, RedirectResolver


class Handler(BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):  # noqa: N802 - the name http.server asks for
        type(self).seen.append(self.path)
        if self.path.startswith("/r/page"):
            self.send_response(302)
            self.send_header("Location", "https://en.wikipedia.org/wiki/Fern_(Frieren)")
        elif self.path.startswith("/r/relative"):
            self.send_response(302)
            self.send_header("Location", "/somewhere")
        elif self.path.startswith("/r/script"):
            self.send_response(302)
            self.send_header("Location", "javascript:alert(1)")
        elif self.path.startswith("/r/gone"):
            self.send_response(404)
        else:
            self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # keep the test output clean
        return None


class RedirectResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.seen = []
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def resolver(self, **kw):
        return RedirectResolver(hosts=("127.0.0.1",), schemes=("http",), timeout=3.0, **kw)

    def test_a_redirect_of_the_known_host_is_followed_one_hop_to_the_pages_own_address(self):
        self.assertEqual(self.resolver().resolve(f"{self.base}/r/page?x=1"), "https://en.wikipedia.org/wiki/Fern_(Frieren)")
        self.assertIn("/r/page?x=1", Handler.seen)

    def test_what_is_not_a_page_is_nothing(self):
        for path in ("/r/relative", "/r/script", "/r/gone", "/r/none"):
            self.assertIsNone(self.resolver().resolve(f"{self.base}{path}"), path)  # no Location, a relative or a script one

    def test_an_address_that_is_already_a_page_comes_back_as_it_is_and_is_never_fetched(self):
        before = len(Handler.seen)
        self.assertEqual(self.resolver().resolve("https://example.org/a/b?c=1"), "https://example.org/a/b?c=1")
        self.assertEqual(len(Handler.seen), before)

    def test_only_the_configured_host_and_scheme_are_ever_fetched(self):
        before = len(Handler.seen)
        https_only = RedirectResolver(hosts=("127.0.0.1",), schemes=("https",), timeout=1.0)
        self.assertIsNone(https_only.resolve(f"{self.base}/r/page"))  # plain http to the known host is refused
        self.assertEqual(len(Handler.seen), before)
        self.assertIsNone(self.resolver().resolve("ftp://example.org/x"))
        self.assertIsNone(self.resolver().resolve(""))
        self.assertIsNone(self.resolver().resolve("not a url"))

    def test_a_refused_connection_is_nothing_and_never_an_error(self):
        dead = HTTPServer(("127.0.0.1", 0), Handler)
        port = dead.server_port
        dead.server_close()
        self.assertIsNone(self.resolver().resolve(f"http://127.0.0.1:{port}/r/page"))

    def test_by_default_only_googles_redirect_host_is_known(self):
        self.assertEqual(GOOGLE_REDIRECT_HOSTS, ("vertexaisearch.cloud.google.com",))
        self.assertEqual(RedirectResolver().hosts, frozenset(GOOGLE_REDIRECT_HOSTS))
        self.assertEqual(RedirectResolver().schemes, frozenset({"https"}))


if __name__ == "__main__":
    unittest.main()
