import unittest

from subcore.providers import FailoverProvider, ResilientProvider

from gst_worker import llm


class LimitTests(unittest.TestCase):
    def test_free_tier_defaults_to_a_conservative_rpm(self):
        self.assertEqual(llm.rate_limits({}), (llm.FREE_TIER_RPM, 0, 0))

    def test_paid_quota_is_unlimited_unless_configured(self):
        self.assertEqual(llm.rate_limits({"gst_paid_quota": True}), (0, 0, 0))
        self.assertEqual(llm.rate_limits({"gst_paid_quota": True, "llm_rpm": "60", "llm_rpd": "1000"}), (60, 0, 1000))

    def test_garbage_limits_fall_back_to_defaults(self):
        self.assertEqual(llm.rate_limits({"llm_rpm": "abc", "llm_tpm": "-5"}), (llm.FREE_TIER_RPM, 0, 0))


class ThinkingTests(unittest.TestCase):
    def test_budget_only_applies_to_gemini_25(self):
        settings = {"gst_thinking_budget": "2048"}
        self.assertEqual(llm.thinking_budget(settings, "gemini-2.5-flash"), 2048)
        self.assertIsNone(llm.thinking_budget(settings, "gemini-3.5-flash"))

    def test_no_thinking_means_zero_budget(self):
        self.assertEqual(llm.thinking_budget({"gst_no_thinking": True, "gst_thinking_budget": "9"}, "gemini-2.5-flash"), 0)

    def test_unparsable_budget_is_ignored(self):
        self.assertIsNone(llm.thinking_budget({"gst_thinking_budget": "lots"}, "gemini-2.5-flash"))


class BuildProviderTests(unittest.TestCase):
    def test_no_key_is_a_clear_error(self):
        with self.assertRaises(llm.MissingApiKey):
            llm.build_provider({}, "gemini-3.5-flash")

    def test_one_key_gives_one_limited_provider(self):
        provider = llm.build_provider({"gemini_api_key": "k1"}, "gemini-3.5-flash")
        self.assertIsInstance(provider, ResilientProvider)
        self.assertEqual(provider.model, "gemini-3.5-flash")
        self.assertEqual(provider.limiter.rpm, llm.FREE_TIER_RPM)

    def test_two_keys_fail_over(self):
        provider = llm.build_provider({"gemini_api_key": "k1", "gemini_api_key2": "k2"}, "gemini-3.5-flash")
        self.assertIsInstance(provider, FailoverProvider)
        self.assertEqual(len(provider.providers), 2)
        self.assertNotIn("k1", repr(provider.providers[0].inner))


if __name__ == "__main__":
    unittest.main()
