"""Rate limiter, backoff and key failover (NFR-1, NFR-2, FR-9.4; SPEC §6.6). No real sleeping."""

import random
import unittest

from subcore.providers import (
    AuthError,
    DeadlineExceeded,
    FailoverProvider,
    FakeProvider,
    LLMRequest,
    QuotaExhausted,
    RateLimited,
    RateLimiter,
    ResilientProvider,
    Unavailable,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def request(text="hello"):
    return LLMRequest(system="s", user=text, meta={"targets": [1], "source": {1: text}})


class RateLimiterTests(unittest.TestCase):
    def test_rpm_never_exceeded(self):
        clock = FakeClock()
        limiter = RateLimiter(rpm=3, clock=clock, sleep=clock.sleep)
        stamps = []
        for _ in range(10):
            limiter.acquire()
            stamps.append(clock.now)
        for stamp in stamps:
            in_window = [s for s in stamps if stamp - 60 < s <= stamp]
            self.assertLessEqual(len(in_window), 3)
        self.assertGreaterEqual(stamps[-1] - stamps[0], 180)

    def test_tpm_waits_for_the_window_to_free_tokens(self):
        clock = FakeClock()
        limiter = RateLimiter(tpm=100, clock=clock, sleep=clock.sleep)
        limiter.acquire(60)
        start = clock.now
        limiter.acquire(60)  # 120 > 100: must wait for the first reservation to age out
        self.assertGreaterEqual(clock.now - start, 60)

    def test_oversized_request_does_not_wait_forever(self):
        clock = FakeClock()
        limiter = RateLimiter(tpm=10, clock=clock, sleep=clock.sleep)
        limiter.acquire(500)
        self.assertEqual(clock.sleeps, [])

    def test_rpd_exhaustion_raises_with_reset_time(self):
        clock = FakeClock()
        limiter = RateLimiter(rpd=2, clock=clock, sleep=clock.sleep)
        limiter.acquire()
        limiter.acquire()
        with self.assertRaises(QuotaExhausted) as raised:
            limiter.acquire()
        self.assertGreater(raised.exception.retry_after, 80_000)

    def test_zero_means_unlimited(self):
        clock = FakeClock()
        limiter = RateLimiter(rpm=0, tpm=0, rpd=0, clock=clock, sleep=clock.sleep)
        for _ in range(100):
            limiter.acquire(10_000)
        self.assertEqual(clock.sleeps, [])


class ResilientProviderTests(unittest.TestCase):
    def make(self, handler, **kwargs):
        clock = FakeClock()
        fake = FakeProvider(handler=handler, prefix="")
        provider = ResilientProvider(
            fake, kwargs.pop("limiter", None), clock=clock, sleep=clock.sleep, rng=random.Random(1), **kwargs
        )
        return provider, fake, clock

    def test_retries_rate_limit_with_retry_after_then_succeeds(self):
        def handler(req, n):
            if n < 3:
                raise RateLimited("slow down", retry_after=7)
            return [{"i": 1, "text": "ok"}]

        logs = []
        provider, fake, clock = self.make(handler, on_call=logs.append)
        response = provider.generate(request())
        self.assertIn("ok", response.text)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(len(clock.sleeps), 2)
        for delay in clock.sleeps:
            self.assertTrue(7 <= delay <= 7.71, delay)  # retry-after honoured, small jitter on top
        self.assertEqual([log.outcome for log in logs], ["rate_limited", "rate_limited", "ok"])

    def test_backoff_grows_exponentially_with_jitter(self):
        def handler(req, n):
            raise Unavailable("503")

        provider, fake, clock = self.make(handler, max_attempts=4, base_delay=2.0, max_delay=100.0)
        with self.assertRaises(Unavailable):
            provider.generate(request())
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(len(clock.sleeps), 3)
        for attempt, delay in enumerate(clock.sleeps):
            ceiling = 2.0 * 2**attempt
            self.assertTrue(ceiling * 0.5 <= delay <= ceiling, (attempt, delay))

    def test_continuous_429_stays_within_configured_rpm(self):
        def handler(req, n):
            raise RateLimited("429")

        clock = FakeClock()
        fake = FakeProvider(handler=handler)
        limiter = RateLimiter(rpm=4, clock=clock, sleep=clock.sleep)
        provider = ResilientProvider(
            fake, limiter, max_attempts=12, base_delay=1.0, max_delay=1.0, clock=clock, sleep=clock.sleep
        )
        with self.assertRaises(RateLimited):
            provider.generate(request())
        self.assertEqual(len(fake.calls), 12)
        # No 60 s window ever holds more than 4 calls although the server kept refusing.
        stamps = []
        # Reconstruct call times from the limiter's own record.
        stamps = sorted(limiter._day)
        for stamp in stamps:
            # Float addition near 1000 s loses ~1e-13 s, so a call may land that much inside the window
            # edge; a microsecond of tolerance keeps the check about the limiter, not about rounding.
            self.assertLessEqual(len([s for s in stamps if stamp - 60 + 1e-6 < s <= stamp]), 4)

    def test_quota_exhausted_is_not_retried(self):
        def handler(req, n):
            raise QuotaExhausted("per day")

        provider, fake, clock = self.make(handler)
        with self.assertRaises(QuotaExhausted):
            provider.generate(request())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(clock.sleeps, [])

    def test_deadline_stops_waiting_before_the_next_attempt(self):
        def handler(req, n):
            raise Unavailable("503", retry_after=500)

        clock = FakeClock()
        fake = FakeProvider(handler=handler)
        provider = ResilientProvider(
            fake, deadline=clock.now + 100, clock=clock, sleep=clock.sleep, rng=random.Random(1)
        )
        with self.assertRaises(DeadlineExceeded):
            provider.generate(request())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(clock.sleeps, [])

    def test_local_rpd_limit_surfaces_as_quota_exhausted(self):
        clock = FakeClock()
        fake = FakeProvider(prefix="")
        limiter = RateLimiter(rpd=1, clock=clock, sleep=clock.sleep)
        provider = ResilientProvider(fake, limiter, clock=clock, sleep=clock.sleep)
        provider.generate(request())
        with self.assertRaises(QuotaExhausted):
            provider.generate(request())
        self.assertEqual(len(fake.calls), 1)


class FailoverProviderTests(unittest.TestCase):
    def test_moves_to_second_key_when_first_is_spent(self):
        def spent(req, n):
            raise QuotaExhausted("per day")

        first = FakeProvider(handler=spent)
        second = FakeProvider(prefix="")
        provider = FailoverProvider([first, second])
        provider.generate(request())
        provider.generate(request())
        self.assertEqual(len(first.calls), 1)  # not retried once abandoned
        self.assertEqual(len(second.calls), 2)

    def test_raises_when_every_key_is_spent(self):
        def spent(req, n):
            raise AuthError("bad key")

        provider = FailoverProvider([FakeProvider(handler=spent), FakeProvider(handler=spent)])
        with self.assertRaises(AuthError):
            provider.generate(request())


if __name__ == "__main__":
    unittest.main()
