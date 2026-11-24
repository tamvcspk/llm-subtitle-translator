import unittest
from types import SimpleNamespace

from google.genai import errors

from subcore.providers.base import (
    AuthError,
    BadOutput,
    Blocked,
    LLMRequest,
    ModelNotFound,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from subcore.providers.gemini import GeminiProvider, map_api_error


def api_error(code, status, message="boom", details=None):
    body = {"error": {"code": code, "message": message, "status": status, "details": details or []}}
    return errors.APIError(code, body)


def chunk(text, usage=None, finish=None):
    candidates = [SimpleNamespace(finish_reason=SimpleNamespace(name=finish))] if finish else []
    return SimpleNamespace(text=text, usage_metadata=usage, candidates=candidates)


class FakeClient:
    def __init__(self, chunks=None, raises=None):
        self.models = SimpleNamespace(generate_content_stream=self._stream, count_tokens=self._count)
        self._chunks, self._raises = chunks or [], raises
        self.requests = []

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        if self._raises:
            raise self._raises
        return iter(self._chunks)

    def _count(self, **kwargs):
        return SimpleNamespace(total_tokens=321)


REQ = LLMRequest(system="sys", user="usr", response_schema={"type": "array"})


class ErrorMappingTests(unittest.TestCase):
    def test_minute_limit_is_rate_limited_with_retry_after(self):
        details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]
        exc = map_api_error(429, "RESOURCE_EXHAUSTED", "slow", {"error": {"details": details}})
        self.assertIsInstance(exc, RateLimited)
        self.assertEqual(exc.retry_after, 34.0)

    def test_daily_quota_is_quota_exhausted(self):
        details = [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]
        exc = map_api_error(429, "RESOURCE_EXHAUSTED", "quota", {"error": {"details": details}})
        self.assertIsInstance(exc, QuotaExhausted)

    def test_server_errors_are_unavailable(self):
        for code in (500, 503, 504):
            self.assertIsInstance(map_api_error(code, None, "x", None), Unavailable)

    def test_auth_errors(self):
        self.assertIsInstance(map_api_error(403, "PERMISSION_DENIED", "x", None), AuthError)
        self.assertIsInstance(map_api_error(401, None, "x", None), AuthError)

    def test_a_key_google_does_not_know_is_an_auth_error_by_its_reason_not_its_text(self):
        info = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]
        exc = map_api_error(400, "INVALID_ARGUMENT", "anything at all", {"error": {"details": info}})
        self.assertIsInstance(exc, AuthError)
        self.assertEqual(exc.http_status, 400)
        # Without the reason a 400 is not about the key, whatever the words say.
        self.assertIs(type(map_api_error(400, "INVALID_ARGUMENT", "API key not valid", None)), ProviderError)

    def test_unknown_model_is_model_not_found_and_is_not_an_auth_error(self):
        for exc in (map_api_error(404, "NOT_FOUND", "models/x is not found", None), map_api_error(404, None, "x", None)):
            self.assertIsInstance(exc, ModelNotFound)
            self.assertNotIsInstance(exc, AuthError)
            self.assertEqual(exc.http_status, 404)

    def test_other_client_errors_are_plain_provider_errors(self):
        exc = map_api_error(400, "INVALID_ARGUMENT", "x", None)
        self.assertIs(type(exc), ProviderError)


class GeminiProviderTests(unittest.TestCase):
    def test_streamed_chunks_are_joined_and_usage_read(self):
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=40, thoughts_token_count=10)
        client = FakeClient([chunk('[{"i":1,'), chunk('"text":"x"}]', usage, "STOP")])
        resp = GeminiProvider("k", "gemini-test", client=client).generate(REQ)
        self.assertEqual(resp.text, '[{"i":1,"text":"x"}]')
        self.assertEqual((resp.tokens_in, resp.tokens_out, resp.finish_reason), (100, 50, "STOP"))
        sent = client.requests[0]
        self.assertEqual(sent["model"], "gemini-test")
        self.assertEqual(sent["contents"], "usr")
        self.assertEqual(sent["config"].system_instruction, "sys")
        self.assertEqual(sent["config"].response_mime_type, "application/json")

    def test_truncated_output_is_returned_for_partial_recovery(self):
        client = FakeClient([chunk('[{"i":1,"text":"a"},{"i":2', None, "MAX_TOKENS")])
        resp = GeminiProvider("k", client=client).generate(REQ)
        self.assertEqual(resp.finish_reason, "MAX_TOKENS")

    def test_empty_response_is_bad_output(self):
        with self.assertRaises(BadOutput):
            GeminiProvider("k", client=FakeClient([chunk("", None, "SAFETY")])).generate(REQ)

    def test_a_refused_answer_is_blocked_not_just_empty(self):
        with self.assertRaises(Blocked):
            GeminiProvider("k", client=FakeClient([chunk("", None, "SAFETY")])).generate(REQ)
        with self.assertRaises(Blocked):
            GeminiProvider("k", client=FakeClient([chunk("", None, "PROHIBITED_CONTENT")])).generate(REQ)
        refused = chunk("")
        refused.prompt_feedback = SimpleNamespace(block_reason=SimpleNamespace(name="PROHIBITED_CONTENT"))
        with self.assertRaises(Blocked) as raised:
            GeminiProvider("k", client=FakeClient([refused])).generate(REQ)
        self.assertIn("PROHIBITED_CONTENT", str(raised.exception))
        with self.assertRaises(BadOutput) as plain:  # an empty answer that was not refused stays plain bad output
            GeminiProvider("k", client=FakeClient([chunk("", None, "STOP")])).generate(REQ)
        self.assertNotIsInstance(plain.exception, Blocked)
        # a refusal after some text is a cut-off like any other: what arrived is kept for repair
        kept = GeminiProvider("k", client=FakeClient([chunk('[{"i":1,"text":"a"}', None, "SAFETY")])).generate(REQ)
        self.assertEqual(kept.finish_reason, "SAFETY")

    def test_sdk_errors_become_typed_errors(self):
        client = FakeClient(raises=api_error(503, "UNAVAILABLE"))
        with self.assertRaises(Unavailable):
            GeminiProvider("k", client=client).generate(REQ)

    def test_a_403_on_the_search_tool_is_a_failed_search_not_a_refused_key(self):
        """A key that translates may not be allowed Google Search: an AuthError would mark it invalid and stop every job."""
        client = FakeClient(raises=api_error(403, "PERMISSION_DENIED", "the search tool is not allowed"))
        search = LLMRequest(system="s", user="u", web_search=True)
        with self.assertRaises(ProviderError) as raised:
            GeminiProvider("k", client=client).generate(search)
        self.assertNotIsInstance(raised.exception, AuthError)
        self.assertEqual(raised.exception.http_status, 403)
        self.assertIn("Google Search", str(raised.exception))
        with self.assertRaises(AuthError):  # the same answer to a plain call still says the key is not permitted
            GeminiProvider("k", client=client).generate(REQ)
        with self.assertRaises(AuthError):  # and a 401 is the key, whatever the call
            GeminiProvider("k", client=FakeClient(raises=api_error(401, "UNAUTHENTICATED"))).generate(search)

    def test_count_tokens(self):
        self.assertEqual(GeminiProvider("k", client=FakeClient()).count_tokens(REQ), 321)

    def test_key_never_appears_in_repr(self):
        self.assertNotIn("secret-key", repr(GeminiProvider("secret-key")))

    def test_empty_key_is_an_auth_error(self):
        with self.assertRaises(AuthError):
            GeminiProvider("")


if __name__ == "__main__":
    unittest.main()
