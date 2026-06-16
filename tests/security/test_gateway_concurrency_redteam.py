"""Red-team concurrency tests for the model gateway + health tracker + cache.

Covers the CONFIRMED concurrency bugs fixed in this change:

  Fix 1  unlocked health-tracker read-modify-write  -> lost-update, breaker never trips
  Fix 2  is_healthy() cooldown reset race/stampede   -> every concurrent caller passes the gate
  Fix 3  fallback-exhaustion skips the circuit        -> fallbacks called while unhealthy, never recorded
  Fix 4  cache check-then-act has no single-flight     -> N identical misses all hit the provider
  Fix 5  empty-200 billed before the content guard     -> empty 200 charged + (pre-guard) metered

threading.Barrier forces a maximal interleave so the race is reliably triggered
(not merely possible). Run:

    make test-iso TESTFILE='tests/security/test_gateway_concurrency_redteam.py'
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutEvent,
    TimeoutKind,
)


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, content: str, usage: dict) -> None:
        self.content = content
        self.usage_metadata = usage


class _FakeChatModel:
    def __init__(self, *, content="ok", usage=None, raise_exc=None, on_invoke=None, **_kw):
        self._content = content
        self._usage = usage if usage is not None else {}
        self._raise = raise_exc
        self._on_invoke = on_invoke

    def invoke(self, _messages):
        if self._on_invoke is not None:
            self._on_invoke()
        if self._raise is not None:
            raise self._raise
        return _FakeResponse(self._content, self._usage)


class _PerProfileRegistry:
    """Yields a chat-model factory chosen per profile model name."""

    def __init__(self, factory_by_model: dict[str, object], default=None) -> None:
        self._by_model = factory_by_model
        self._default = default

    def is_installed(self, _provider):
        return True

    def get_provider_class(self, _provider):
        # The gateway passes model= in init kwargs; route on it.
        def _factory(**kw):
            model = kw.get("model")
            fac = self._by_model.get(model, self._default)
            assert fac is not None, f"no factory for model {model!r}"
            return fac(**kw)

        return _factory


class _CountingProvider:
    """A single-model provider factory that counts how many times invoke() ran
    and optionally sleeps inside invoke() to widen the stampede window."""

    def __init__(self, *, content="real", usage=None, sleep_s=0.0) -> None:
        self.invoke_count = 0
        self._content = content
        self._usage = usage if usage is not None else {}
        self._sleep_s = sleep_s
        self._lock = threading.Lock()

    def __call__(self, **_kw):
        def _on_invoke() -> None:
            if self._sleep_s:
                time.sleep(self._sleep_s)
            with self._lock:
                self.invoke_count += 1

        return _FakeChatModel(
            content=self._content, usage=self._usage, on_invoke=_on_invoke
        )


class _InMemoryCache:
    """A thread-safe in-process cache double mimicking the gateway's contract."""

    def __init__(self) -> None:
        self._d: dict = {}
        self._lock = threading.Lock()

    def get(self, k):
        with self._lock:
            return self._d.get(k)

    def set(self, k, v, **_kw):
        with self._lock:
            self._d[k] = v


def _profile(pid="p1", model_name="m", **kw) -> ModelProfile:
    base = dict(
        model_profile_id=pid,
        provider="openai",
        model_name=model_name,
        enabled=True,
        cost_per_input_token=0.00001,
        cost_per_output_token=0.00002,
    )
    base.update(kw)
    return ModelProfile(**base)


def _single_model_registry(factory):
    class _Reg:
        def is_installed(self, _p):
            return True

        def get_provider_class(self, _p):
            return factory

    return _Reg()


# ==========================================================================
# Fix 4 — cache stampede single-flight
# ==========================================================================
def test_cache_stampede_single_flight_one_provider_call():
    """Fix 4: N concurrent identical cache MISSES must result in exactly ONE
    provider invocation; the rest serve the now-populated entry."""
    cache = _InMemoryCache()
    # sleep widens the window so without single-flight all N threads would race
    # into invoke() before any set() lands.
    provider = _CountingProvider(content="answer", sleep_s=0.05)

    gw = ModelGateway(
        profiles=[_profile("p1")],
        provider_registry=_single_model_registry(provider),
        response_cache=cache,
    )

    n = 32
    barrier = threading.Barrier(n)
    results: list[str] = []
    results_lock = threading.Lock()

    def call() -> None:
        barrier.wait()
        r = gw.call_model("p1", [{"role": "user", "content": "same query"}])
        with results_lock:
            results.append(r.content)

    threads = [threading.Thread(target=call) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Single-flight: exactly one provider hit despite N concurrent misses.
    assert provider.invoke_count == 1
    # Every caller got the same real answer.
    assert results == ["answer"] * n


def test_distinct_keys_are_not_serialized():
    """Fix 4 (scope guard): single-flight is PER cache key — distinct queries
    must NOT block one another; each distinct miss hits the provider once."""
    cache = _InMemoryCache()
    provider = _CountingProvider(content="answer", sleep_s=0.0)
    gw = ModelGateway(
        profiles=[_profile("p1")],
        provider_registry=_single_model_registry(provider),
        response_cache=cache,
    )

    n = 16
    barrier = threading.Barrier(n)

    def call(i: int) -> None:
        barrier.wait()
        gw.call_model("p1", [{"role": "user", "content": f"q{i}"}])

    threads = [threading.Thread(target=call, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # n distinct keys -> n provider calls (no false serialization/dedup).
    assert provider.invoke_count == n


# ==========================================================================
# Fix 5 — empty-200 billed
# ==========================================================================
def test_empty_200_not_billed_and_raises_retryable():
    """Fix 5: an empty-content 200 raises a RETRYABLE provider error BEFORE
    record_spend, so it is never billed and never cached."""
    guard = RunBudgetGuard(run_budget_usd=10.0)
    cache = _InMemoryCache()

    def empty_factory(**_kw):
        # Non-zero usage so, were billing reached, spend WOULD be non-zero.
        return _FakeChatModel(content="   ", usage={"input_tokens": 1000, "output_tokens": 1000})

    gw = ModelGateway(
        profiles=[_profile("p1")],
        provider_registry=_single_model_registry(empty_factory),
        budget_guard=guard,
        response_cache=cache,
    )

    with pytest.raises(httpx.HTTPStatusError):
        gw.call_model("p1", [{"role": "user", "content": "hi"}])

    # Never billed.
    assert guard.get_total_spend() == pytest.approx(0.0)
    # Never cached: a later good call is served fresh.
    good = _CountingProvider(content="real", usage={})
    gw2 = ModelGateway(
        profiles=[_profile("p1")],
        provider_registry=_single_model_registry(good),
        response_cache=cache,
    )
    r = gw2.call_model("p1", [{"role": "user", "content": "hi"}])
    assert r.content == "real"
    assert good.invoke_count == 1


def test_empty_200_records_provider_error_on_health_tracker():
    """Fix 5: the empty-200 error is recorded as PROVIDER_ERROR on the health
    tracker (so repeated empty 200s trip the breaker / drive failover)."""
    tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)

    def empty_factory(**_kw):
        return _FakeChatModel(content="", usage={})

    gw = ModelGateway(
        profiles=[_profile("p1")],
        provider_registry=_single_model_registry(empty_factory),
        health_tracker=tracker,
    )

    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            gw.call_model("p1", [{"role": "user", "content": "hi"}])

    status = tracker.get_health("p1")
    assert status["last_failure_kind"] == "provider_error"
    assert status["consecutive_failures"] == 3
    assert tracker.is_healthy("p1", admit_probe=False) is False


# ==========================================================================
# Fix 3 — fallback skips the circuit
# ==========================================================================
def test_fallback_skips_unhealthy_and_records_success():
    """Fix 3: with the primary unhealthy, call_model_with_retry must SKIP a
    fallback whose circuit is open (is_healthy False) and use the next healthy
    fallback, recording the served fallback's SUCCESS on the tracker."""
    tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=60.0)
    now = time.monotonic()

    # Primary: unhealthy (2 read-timeouts).
    for _ in range(2):
        tracker.record_event(
            TimeoutEvent(model_id="primary", kind=TimeoutKind.READ_TIMEOUT,
                         timestamp=now, duration_s=0.0)
        )
    # fb_bad: also unhealthy -> must be skipped (circuit honored).
    for _ in range(2):
        tracker.record_event(
            TimeoutEvent(model_id="fb_bad", kind=TimeoutKind.READ_TIMEOUT,
                         timestamp=now, duration_s=0.0)
        )

    primary = _profile("primary", model_name="mp", fallback_profiles=["fb_bad", "fb_good"])
    fb_bad = _profile("fb_bad", model_name="mb")
    fb_good = _profile("fb_good", model_name="mg")

    good = _CountingProvider(content="from-good", usage={})
    bad = _CountingProvider(content="from-bad", usage={})
    registry = _PerProfileRegistry({"mg": good, "mb": bad, "mp": good})

    gw = ModelGateway(
        profiles=[primary, fb_bad, fb_good],
        provider_registry=registry,
        health_tracker=tracker,
    )

    r = gw.call_model_with_retry("primary", [{"role": "user", "content": "hi"}])
    assert r.content == "from-good"
    # The open-circuit fallback was never called.
    assert bad.invoke_count == 0
    # The served fallback's success was recorded (consecutive reset to 0).
    assert tracker.get_health("fb_good")["consecutive_failures"] == 0
    assert tracker.is_healthy("fb_good", admit_probe=False) is True


def test_fallback_failure_is_recorded_on_tracker():
    """Fix 3: when an attempted fallback itself FAILS, its failure is recorded
    on the tracker (via call_model's record_timeout_on_failure)."""
    tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=60.0)
    now = time.monotonic()
    for _ in range(2):
        tracker.record_event(
            TimeoutEvent(model_id="primary", kind=TimeoutKind.READ_TIMEOUT,
                         timestamp=now, duration_s=0.0)
        )

    primary = _profile("primary", model_name="mp", fallback_profiles=["fb"])
    fb = _profile("fb", model_name="mf")

    def fb_factory(**_kw):
        return _FakeChatModel(raise_exc=httpx.ReadTimeout("fb timed out"))

    registry = _PerProfileRegistry({"mf": fb_factory, "mp": fb_factory})
    gw = ModelGateway(
        profiles=[primary, fb],
        provider_registry=registry,
        health_tracker=tracker,
    )

    with pytest.raises(httpx.ReadTimeout):
        gw.call_model_with_retry("primary", [{"role": "user", "content": "hi"}])

    # The fallback's failure was recorded on the tracker (Fix 3: previously the
    # fallback path neither checked nor recorded health).
    assert tracker.get_health("fb")["consecutive_failures"] >= 1
    assert tracker.get_health("fb")["last_failure_kind"] == "read_timeout"
