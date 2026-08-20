"""Red-team correctness tests for the model gateway / routing.

Originally these reproduced CONFIRMED bugs by asserting on the CURRENT (buggy)
behavior. The in-scope model-layer bugs (gateway cost/token counting,
timeout_detector health/classification, response_cache TTL/key/error-shape)
have now been FIXED, so the corresponding assertions are flipped to the
expected-correct behavior. The out-of-scope cases (router default-routing,
failover semantics, cache-key serialization of arbitrary kwargs) remain as
characterization tests of the intended/unchanged behavior.

Run: make test-iso TESTFILE='tests/security/test_gateway_redteam.py'
"""

from __future__ import annotations

import datetime

import httpx
import pytest

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.gateway import (
    CircuitBreakerOpenError,
    ModelGateway,
    ModelProfile,
)
from general_ludd.models.response_cache import _make_cache_key
from general_ludd.models.router import ModelRouter
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutClassifier,
    TimeoutEvent,
    TimeoutKind,
)


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------
class _FakeChatModel:
    """Minimal LangChain-ish chat model whose invoke() returns a canned response."""

    def __init__(self, *, content="ok", usage=None, raise_exc=None, **_kw):
        self._content = content
        self._usage = usage if usage is not None else {}
        self._raise = raise_exc

    def invoke(self, _messages):
        if self._raise is not None:
            raise self._raise
        return _FakeResponse(self._content, self._usage)


class _FakeResponse:
    def __init__(self, content, usage):
        self.content = content
        self.usage_metadata = usage


class _FakeRegistry:
    """Provider registry double that always yields a chat model class."""

    def __init__(self, chat_factory):
        self._factory = chat_factory

    def is_installed(self, _provider):
        return True

    def get_provider_class(self, _provider):
        return self._factory


class _InMemoryCache:
    def __init__(self):
        self._d: dict = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v, **_kw):
        self._d[k] = v


class _CacheAdapter:
    """Wraps a spy so set(k, v, **kw) is what the gateway calls."""

    def __init__(self, inner):
        self._inner = inner

    def get(self, k):
        return self._inner.get(k)

    def set(self, k, v, **kw):
        self._inner.set(k, v, **kw)


def _profile(pid="p1", **kw):
    base = dict(
        model_profile_id=pid,
        provider="openai",
        model_name="m",
        enabled=True,
        cost_per_input_token=0.00001,
        cost_per_output_token=0.00002,
    )
    base.update(kw)
    return ModelProfile(**base)


def _gateway_with(chat_factory, profiles, **gw_kw):
    return ModelGateway(
        profiles=profiles,
        provider_registry=_FakeRegistry(chat_factory),
        **gw_kw,
    )


# ==========================================================================
# AREA 1 — cost computation / token counting  (FIXED)
# ==========================================================================
def test_negative_tokens_credit_budget():
    """FIXED 1a: provider-supplied negative tokens are clamped to >= 0, so cost
    is never negative and record_spend() can never CREDIT the budget."""
    guard = RunBudgetGuard(run_budget_usd=1.0)
    guard.record_spend(0.50)  # genuine prior spend
    assert guard.get_total_spend() == pytest.approx(0.50)

    def chat(**kw):
        return _FakeChatModel(content="x", usage={"input_tokens": -1_000_000, "output_tokens": 0})

    gw = _gateway_with(chat, [_profile()], budget_guard=guard)
    resp = gw.call_model("p1", [{"role": "user", "content": "hi"}])

    # Tokens clamped at >= 0 -> cost is 0, not negative.
    assert resp.cost_estimate == pytest.approx(0.0)
    # No credit: total spend unchanged at the genuine prior spend.
    assert guard.get_total_spend() == pytest.approx(0.50)


def test_bool_tokens_treated_as_one():
    """FIXED 1b: bool token values are rejected (counted as 0), so True does not
    silently count as 1 token."""

    def chat(**kw):
        return _FakeChatModel(content="x", usage={"input_tokens": True, "output_tokens": False})

    gw = _gateway_with(chat, [_profile()])
    resp = gw.call_model("p1", [{"role": "user", "content": "hi"}])
    # bools -> 0 tokens -> $0 cost.
    assert resp.cost_estimate == pytest.approx(0.0)


def test_missing_token_keys_meter_as_free():
    """FIXED 1c: OpenAI-style 'prompt_tokens'/'completion_tokens' are accepted as
    fallbacks for input/output tokens, so the call is metered, not free."""

    def chat(**kw):
        return _FakeChatModel(
            content="x",
            usage={"prompt_tokens": 5000, "completion_tokens": 5000},
        )

    gw = _gateway_with(
        chat,
        [_profile()],
        billing_clock=lambda: datetime.datetime(
            2026,
            8,
            2,
            12,
            tzinfo=datetime.UTC,
        ),
    )
    resp = gw.call_model("p1", [{"role": "user", "content": "hi"}])
    # (5000 * 0.00001 + 5000 * 0.00002) * 0.75 == 0.1125
    assert resp.cost_estimate == pytest.approx(0.1125)


def test_huge_tokens_do_not_overflow_holds():
    """HOLDS: Python ints are arbitrary precision; huge token counts produce a
    large cost, never a wraparound-negative. Documents the non-bug."""

    def chat(**kw):
        return _FakeChatModel(content="x", usage={"input_tokens": 10**18, "output_tokens": 0})

    gw = _gateway_with(chat, [_profile()])
    resp = gw.call_model("p1", [{"role": "user", "content": "hi"}])
    assert resp.cost_estimate > 0  # large, positive, no overflow


# ==========================================================================
# AREA 2 — router  (OUT OF SCOPE — intended default-routing, unchanged)
# ==========================================================================
def test_router_unknown_role_silently_uses_default():
    """OUT OF SCOPE: with a default profile set, an unknown/typo'd role resolves
    to the default. This is intended default-routing; left unchanged."""
    router = ModelRouter(role_mapping={"code": "code_model"}, default_profile_id="cheap_default")
    assert router.resolve_role("cdoe") == "cheap_default"
    assert router.resolve_role("totally_unknown_role") == "cheap_default"
    # Without a default, it returns None.
    router_nodefault = ModelRouter(role_mapping={"code": "code_model"})
    assert router_nodefault.resolve_role("cdoe") is None


# ==========================================================================
# AREA 3 — failover  (OUT OF SCOPE — unchanged)
# ==========================================================================
def test_fallback_does_not_loop_holds():
    """HOLDS: fallback chains are finite lists; a fallback's own
    fallback_profiles are not traversed recursively, so no cycle/infinite loop
    occurs. When every profile in the chain fails, call_model_with_fallback
    raises CircuitBreakerOpenError (with the last exception chained as
    __cause__)."""

    def chat(**kw):
        return _FakeChatModel(raise_exc=ValueError("boom"))

    pa = _profile("a", fallback_profiles=["b"])
    pb = _profile("b", fallback_profiles=["a"])
    gw = _gateway_with(chat, [pa, pb])
    with pytest.raises(CircuitBreakerOpenError, match="providers down"):
        gw.call_model_with_fallback("a", [{"role": "user", "content": "hi"}])


def test_fallback_treats_budget_rejection_as_failover():
    """D-24 (now IN SCOPE / FIXED): a per-profile budget rejection MUST
    propagate as a ValueError instead of being silently swallowed and routed
    around via the fallback chain. Previously _try_call_model caught *all*
    ValueErrors and returned None, defeating the per-profile spending cap."""
    primary = _profile("primary", run_budget_usd=0.0, api_metered=True)
    fallback = _profile("fb", run_budget_usd=1000.0)

    def chat(**kw):
        return _FakeChatModel(content="served-by-fallback", usage={})

    gw = _gateway_with(chat, [primary, fallback])

    with pytest.raises(ValueError, match="over budget"):
        gw.call_model_with_fallback(
            "primary",
            [{"role": "user", "content": "hi"}],
            fallback_profiles=["fb"],
            estimated_cost=5.0,
        )


def test_failover_should_retry_substring_false_positive():
    """OUT OF SCOPE: should_retry() substring-matches the error text. Documented
    as-is (failover module not in this fix scope)."""
    chain = ModelFailoverChain("p", ["fb"])

    class _PermanentErr(Exception):
        pass

    assert chain.should_retry(_PermanentErr("feature unavailable in your plan")) is True
    assert chain.should_retry(_PermanentErr("timeout must be a positive integer")) is True
    assert chain.should_retry(_PermanentErr("overloaded")) is False


# ==========================================================================
# AREA 4 — timeout_detector  (FIXED)
# ==========================================================================
def _http_status_error(code: int, body: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat")
    response = httpx.Response(code, request=request, text=body)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


def test_plain_400_misclassified_as_auth():
    """FIXED 4a: a generic 400 (malformed request / bad param) that is neither a
    context-length nor an auth error is now a non-auth client error
    (INVALID_REQUEST), not AUTH_ERROR."""
    body = "invalid value for parameter 'temperature'"
    exc = _http_status_error(400, body=body)
    kind = TimeoutClassifier.classify(exc, response_body=body)
    assert kind == TimeoutKind.INVALID_REQUEST
    assert kind != TimeoutKind.AUTH_ERROR


def test_rate_limited_never_unhealthy():
    """FIXED 4b: RATE_LIMITED is retryable-after-cooldown, so a rate-limited model
    is now reported UNHEALTHY once the failure threshold is crossed (within
    cooldown), enabling backoff/failover."""
    tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
    now = __import__("time").monotonic()
    for _ in range(10):
        tracker.record_event(TimeoutEvent(model_id="m", kind=TimeoutKind.RATE_LIMITED, timestamp=now, duration_s=0.0))
    # Rate-limited model is now unhealthy (was permanently healthy before the fix).
    assert tracker.is_healthy("m") is False

    # Contrast: connection timeouts (truly retryable) also mark unhealthy.
    tracker2 = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
    for _ in range(10):
        tracker2.record_event(
            TimeoutEvent(model_id="m", kind=TimeoutKind.CONNECTION_TIMEOUT, timestamp=now, duration_s=0.0)
        )
    assert tracker2.is_healthy("m") is False


# ==========================================================================
# AREA 5 — response_cache  (FIXED, except key-serialization which is out of scope)
# ==========================================================================
def test_cache_key_no_collision_for_distinct_serializable_inputs_holds():
    """HOLDS: distinct serializable inputs yield distinct sha256 keys."""
    k1 = _make_cache_key("p1", [{"role": "user", "content": "a"}])
    k2 = _make_cache_key("p1", [{"role": "user", "content": "b"}])
    k3 = _make_cache_key("p2", [{"role": "user", "content": "a"}])
    assert len({k1, k2, k3}) == 3


def test_cache_key_collapses_distinct_callables():
    """OUT OF SCOPE: default=str serialization makes two different config objects
    with the same repr collapse to the SAME cache key. Documented as-is
    (arbitrary-kwargs serialization is not in this fix scope)."""

    class _ReprStable:
        def __repr__(self):
            return "<handler>"

    a = _ReprStable()
    b = _ReprStable()  # different object, identical repr, different behavior
    ka = _make_cache_key("p1", [{"role": "user", "content": "x"}], callbacks=a)
    kb = _make_cache_key("p1", [{"role": "user", "content": "x"}], callbacks=b)
    assert ka == kb


def test_cache_serves_stale_after_model_swap():
    """FIXED 5a: cache key now includes the resolved model_name, so swapping the
    underlying model on the same profile yields a DISTINCT key and the new
    model's output is served (not the stale old one)."""
    cache = _InMemoryCache()

    def chat_old(**kw):
        return _FakeChatModel(content="old-answer", usage={})

    gw = _gateway_with(chat_old, [_profile("p1", model_name="old")], response_cache=cache)
    r1 = gw.call_model("p1", [{"role": "user", "content": "hi"}])
    assert r1.content == "old-answer"

    def chat_new(**kw):
        return _FakeChatModel(content="new-answer", usage={})

    gw2 = _gateway_with(chat_new, [_profile("p1", model_name="new")], response_cache=cache)
    r2 = gw2.call_model("p1", [{"role": "user", "content": "hi"}])
    # Model swap -> new key -> fresh answer.
    assert r2.content == "new-answer"


def test_empty_content_success_is_cached():
    """FIXED 5b (revised by Fix 5 / empty-200-billed): an error-shaped 200 with
    empty content now RAISES a retryable PROVIDER_ERROR BEFORE record_spend, so
    it is neither billed nor cached. A subsequent good call is served fresh."""
    cache = _InMemoryCache()
    guard = RunBudgetGuard(run_budget_usd=10.0)

    def chat_empty(**kw):
        return _FakeChatModel(content="", usage={"input_tokens": 100, "output_tokens": 100})

    gw = _gateway_with(chat_empty, [_profile("p1")], response_cache=cache, budget_guard=guard)
    # Empty 200 -> retryable provider error raised before billing.
    with pytest.raises(httpx.HTTPStatusError):
        gw.call_model("p1", [{"role": "user", "content": "hi"}])
    # Not billed: record_spend never ran for the empty 200.
    assert guard.get_total_spend() == pytest.approx(0.0)

    def chat_good(**kw):
        return _FakeChatModel(content="real-answer", usage={})

    gw2 = _gateway_with(chat_good, [_profile("p1")], response_cache=cache)
    r2 = gw2.call_model("p1", [{"role": "user", "content": "hi"}])
    # Empty response was not cached -> fresh good answer served.
    assert r2.content == "real-answer"


def test_cache_has_no_ttl():
    """FIXED 5a: gateway's cache .set now passes an expire= TTL so entries do not
    live forever."""
    captured: dict = {}

    class _SpyCache:
        def get(self, k):
            return captured.get(k)

        def set(self, k, v, **kw):
            captured[k] = v
            captured["__set_kwargs__"] = kw

    spy = _SpyCache()

    def chat(**kw):
        return _FakeChatModel(content="a", usage={})

    gw = _gateway_with(chat, [_profile("p1")], response_cache=_CacheAdapter(spy))
    gw.call_model("p1", [{"role": "user", "content": "hi"}])
    # A TTL is now passed through on set.
    set_kwargs = captured.get("__set_kwargs__", {})
    assert "expire" in set_kwargs
    assert set_kwargs["expire"] is not None and set_kwargs["expire"] > 0
