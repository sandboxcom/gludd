"""Circuit-breaker health check before each fallback dispatch in ``call_model_with_fallback``.

Pins the P1 security finding: ``call_model_with_fallback`` must check ``is_healthy``
before dispatching OR failing over to each fallback model, and must raise
``CircuitBreakerOpenError`` when the entire chain is circuit-open.

Scenarios:
1. Three models — open / half-open / closed: the open primary is skipped, the
   half-open fallback is tried (probe admitted), and the call succeeds.
2. All breakers open: ``CircuitBreakerOpenError`` raised immediately — no
   provider invocation occurs.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx
import pytest

# imported for side effects — warms the routing_roles import cycle so the
# gateway imports below succeed in any collection order.
import general_ludd.routing_roles  # noqa: F401
from general_ludd.models.gateway import (
    CircuitBreakerOpenError,
    ModelGateway,
    ModelProfile,
)
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutEvent,
    TimeoutKind,
)

# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------


def _server_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    response = httpx.Response(503, request=request, text="service unavailable")
    return httpx.HTTPStatusError("503", request=request, response=response)


class _FakeChatModel:
    script: ClassVar[list[object]] = []
    call_count: ClassVar[int] = 0

    def __init__(self, **init_kwargs: object) -> None:
        self._init_kwargs = init_kwargs

    def invoke(self, messages: list[dict[str, str]]) -> object:
        _FakeChatModel.call_count += 1
        if not _FakeChatModel.script:
            raise AssertionError("provider invoked more times than scripted")
        item = _FakeChatModel.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        resp = type("_Resp", (), {})()
        resp.content = item
        resp.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        return resp


class _FakeRegistry:
    def is_installed(self, provider_name: str) -> bool:
        return True

    def install_provider(self, provider_name: str) -> None:  # pragma: no cover
        raise AssertionError("install_provider should not be called")

    def get_provider_class(self, provider_name: str) -> type[_FakeChatModel]:
        return _FakeChatModel


@pytest.fixture(autouse=True)
def _reset_script() -> None:
    _FakeChatModel.script = []
    _FakeChatModel.call_count = 0


def _make_profile(pid: str, fallback: list[str] | None = None) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        provider="openai",
        model_name=f"model-{pid}",
        cost_per_input_token=0.001,
        cost_per_output_token=0.002,
        enabled=True,
        fallback_profiles=fallback or [],
    )


def _make_gateway(profiles: list[ModelProfile], tracker: ModelHealthTracker) -> ModelGateway:
    return ModelGateway(
        profiles=profiles,
        provider_registry=_FakeRegistry(),
        health_tracker=tracker,
    )


# ---------------------------------------------------------------------------
# Helpers to prime the tracker into specific states
# ---------------------------------------------------------------------------


def _prime_open(tracker: ModelHealthTracker, model_id: str) -> None:
    """Record exactly ``failure_threshold`` recent failures so the breaker is open."""
    for _ in range(tracker._failure_threshold):
        tracker.record_event(
            TimeoutEvent(
                model_id=model_id,
                kind=TimeoutKind.PROVIDER_ERROR,
                timestamp=time.monotonic(),
                duration_s=0.0,
            )
        )


def _prime_half_open(tracker: ModelHealthTracker, model_id: str) -> None:
    """Record ``failure_threshold`` failures with OLD timestamps so cooldown has elapsed.

    The breaker has enough failures to be open, but the last failure is so old
    that ``is_healthy(admit_probe=True)`` will admit a single half-open probe.
    """
    for _ in range(tracker._failure_threshold):
        tracker.record_event(
            TimeoutEvent(
                model_id=model_id,
                kind=TimeoutKind.PROVIDER_ERROR,
                # cooldown=0.001 means 1 ms — even one second ago is well past
                timestamp=time.monotonic() - 99999,
                duration_s=0.0,
            )
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerFallbackChain:
    """Three models — open / half-open / closed: check health before dispatch."""

    def test_open_primary_skipped_half_open_fallback_tried(self):
        """Open primary skipped; half-open fallback admitted as probe, call succeeds."""
        tracker = ModelHealthTracker(
            failure_threshold=3, cooldown_seconds=0.001
        )
        primary = _make_profile("primary", fallback=["fb1", "fb2"])
        fb1 = _make_profile("fb1")
        fb2 = _make_profile("fb2")
        gw = _make_gateway([primary, fb1, fb2], tracker)

        _prime_open(tracker, "primary")       # open
        _prime_half_open(tracker, "fb1")      # half-open (cooldown elapsed)
        # fb2 is closed (no failures recorded)

        # Script one successful response — the half-open fb1 should serve it.
        _FakeChatModel.script = ["response from half-open fallback"]

        result = gw.call_model_with_fallback(
            "primary", [{"role": "user", "content": "hi"}]
        )

        assert result.content == "response from half-open fallback"
        assert _FakeChatModel.call_count == 1

    def test_all_open_raises_circuit_breaker_open_error(self):
        """When every model in the chain is circuit-open, raise CircuitBreakerOpenError."""
        tracker = ModelHealthTracker(
            failure_threshold=3, cooldown_seconds=60.0
        )
        primary = _make_profile("primary", fallback=["fb1", "fb2"])
        fb1 = _make_profile("fb1")
        fb2 = _make_profile("fb2")
        gw = _make_gateway([primary, fb1, fb2], tracker)

        _prime_open(tracker, "primary")
        _prime_open(tracker, "fb1")
        _prime_open(tracker, "fb2")

        # No script — the error must raise before any provider invocation.
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback(
                "primary", [{"role": "user", "content": "hi"}]
            )

        assert _FakeChatModel.call_count == 0

    def test_chain_all_failed_raises_circuit_breaker_open_error(self):
        """When all models fail AND are then circuit-open, raise CircuitBreakerOpenError.

        Models that fail during the fallback walk have their failures recorded,
        so by the time the chain is exhausted every model is circuit-open
        (assuming failure_threshold=1 so a single failure trips the breaker).
        """
        tracker = ModelHealthTracker(
            failure_threshold=1, cooldown_seconds=60.0
        )
        primary = _make_profile("primary", fallback=["fb1"])
        fb1 = _make_profile("fb1")
        gw = _make_gateway([primary, fb1], tracker)

        # Primary and fb1 are initially healthy but every invoke raises.
        _FakeChatModel.script = [
            _server_error(),  # primary
            _server_error(),  # fb1
        ]

        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback(
                "primary", [{"role": "user", "content": "hi"}]
            )

        assert _FakeChatModel.call_count == 2


class TestCallModelCircuitBreaker:
    """Direct ``call_model`` must honour ``is_healthy`` before dispatch (defence-in-depth)."""

    def test_call_model_refuses_when_circuit_open(self):
        """When circuit is open, a direct call_model() raises CircuitBreakerOpenError."""
        tracker = ModelHealthTracker(
            failure_threshold=3, cooldown_seconds=60.0
        )
        primary = _make_profile("primary")
        gw = _make_gateway([primary], tracker)

        _prime_open(tracker, "primary")

        _FakeChatModel.script = ["should never be invoked"]

        with pytest.raises(CircuitBreakerOpenError, match="circuit is open"):
            gw.call_model("primary", [{"role": "user", "content": "hi"}])

        assert _FakeChatModel.call_count == 0

    def test_call_model_proceeds_when_healthy(self):
        """A healthy (closed) circuit allows dispatch."""
        tracker = ModelHealthTracker(
            failure_threshold=3, cooldown_seconds=60.0
        )
        primary = _make_profile("primary")
        gw = _make_gateway([primary], tracker)

        _FakeChatModel.script = ["healthy response"]

        result = gw.call_model("primary", [{"role": "user", "content": "hi"}])

        assert result.content == "healthy response"
        assert _FakeChatModel.call_count == 1

    def test_call_model_refuses_when_chain_all_open(self):
        """When every model including primary is open, direct call_model raises."""
        tracker = ModelHealthTracker(
            failure_threshold=1, cooldown_seconds=60.0
        )
        primary = _make_profile("primary", fallback=["fb1"])
        fb1 = _make_profile("fb1")
        gw = _make_gateway([primary, fb1], tracker)

        _prime_open(tracker, "primary")
        _prime_open(tracker, "fb1")

        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model("primary", [{"role": "user", "content": "hi"}])

        assert _FakeChatModel.call_count == 0
