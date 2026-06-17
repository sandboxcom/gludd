"""Offline tests for the token-bucket rate limiter (fake clock, no real sleep)."""

from __future__ import annotations

from general_ludd.web.ratelimit import HostRateLimiter, TokenBucket


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_bucket_burst_then_throttle() -> None:
    clock = _Clock()
    slept: list[float] = []
    bucket = TokenBucket(rate=1.0, burst=2, clock=clock, sleep=lambda s: slept.append(s))
    # Two immediate tokens (burst).
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    # Third must wait ~1s for a refill.
    waited = bucket.acquire()
    assert waited > 0
    assert slept and slept[-1] > 0


def test_bucket_refills_over_time() -> None:
    clock = _Clock()
    bucket = TokenBucket(rate=2.0, burst=1, clock=clock, sleep=lambda s: None)
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False  # empty
    clock.advance(0.5)  # 0.5s * 2/s = 1 token
    assert bucket.try_acquire() is True


def test_host_limiter_per_host_independent() -> None:
    clock = _Clock()
    limiter = HostRateLimiter(rate=1.0, burst=1, clock=clock, sleep=lambda s: None)
    # Different hosts each get their own immediate token.
    assert limiter.acquire("a.com") == 0.0
    assert limiter.acquire("b.com") == 0.0


def test_crawl_delay_caps_rate() -> None:
    clock = _Clock()
    slept: list[float] = []
    limiter = HostRateLimiter(rate=100.0, burst=1, clock=clock, sleep=lambda s: slept.append(s))
    limiter.acquire("h.com")  # consume the initial token
    limiter.set_min_interval("h.com", 10.0)  # robots Crawl-delay: 10
    limiter.acquire("h.com")  # fresh bucket from set_min_interval gives 1 token
    waited = limiter.acquire("h.com")  # now must wait ~10s
    assert waited > 0
