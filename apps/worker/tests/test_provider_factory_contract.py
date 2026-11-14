"""Contract of the ProviderFactory port (SPEC criterion 10b): the same checks on the Gemini factory and on a fake."""

import unittest

from subcore.providers import Provider
from subcore.providers.fake import FakeProvider

from gst_worker.llm import build_provider
from gst_worker.ports import MissingApiKey, ProviderFactory


def fake_factory(settings, model, *, deadline=None, on_call=None):
    """Fake ProviderFactory: any key is good; no key is refused like the real one."""
    if not str(settings.get("gemini_api_key") or "").strip():
        raise MissingApiKey("no key")
    return FakeProvider(model=model)


class ProviderFactoryContract:
    factory: ProviderFactory

    def test_a_configured_key_gives_a_provider(self):
        self.assertIsInstance(self.factory({"gemini_api_key": "k"}, "m"), Provider)

    def test_no_key_raises_missing_api_key_not_a_provider(self):
        with self.assertRaises(MissingApiKey):
            self.factory({}, "m")
        with self.assertRaises(MissingApiKey):
            self.factory({"gemini_api_key": "   "}, "m")

    def test_it_accepts_the_deadline_and_call_log_the_attempt_passes(self):
        self.assertIsInstance(
            self.factory({"gemini_api_key": "k"}, "m", deadline=123.0, on_call=lambda log: None), Provider
        )


class GeminiFactoryTests(ProviderFactoryContract, unittest.TestCase):
    factory = staticmethod(build_provider)


class FakeFactoryTests(ProviderFactoryContract, unittest.TestCase):
    factory = staticmethod(fake_factory)


if __name__ == "__main__":
    unittest.main()
