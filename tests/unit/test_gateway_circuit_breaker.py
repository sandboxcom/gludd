"""Circuit-breaker accounting tests for ModelGateway.

These pin two specific bugs that were fixed in gateway.py:

1. DOUBLE-COUNT: ``call_model_with_retry`` recorded each retryable failure
   TWICE — once on the ``call_model`` path (record_timeout_on_failure) and
   again in the tenacity ``_before_sleep`` callback. Because
   ``ModelHealthTracker.record_event`` increments the consecutive-failure
   counter by exactly one per call, double-recording advanced the breaker two
   per real failure, so it tripped at HALF the configured failure_threshold.
   The fix removed the ``record_event`` from ``_before_sleep`` (leaving only the
   backoff sleep). These tests assert the breaker trips at EXACTLY
   failure_threshold real failures — not before.

2. SUCCESS-RESET: a billed success never reset the breaker on the plain
   ``call_model`` entry path (the reset lived only in
   ``call_model_with_retry``/``_call_fallback``), so a profile could stay
   tripped after it had already recovered. The fix adds
   ``health_tracker.record_success(profile_id)`` in ``_invoke_and_bill`` right
   after ``record_spend``, so EVERY billed success resets the consecutive
   counter. These tests assert a success via ``call_model`` clears the counter.
"""

from __future__ import annotations

from typing import ClassVar

import httpx
import pytest

# Warm the routing_roles <-> schemas.benchmark import cycle BEFORE importing the
# gateway. That cycle (schemas.benchmark -> routing_roles.weights -> back to
# schemas.benchmark.TaskType) is a pre-existing, repo-wide import-ordering quirk:
# importing general_ludd.models.gateway FIRST (before anything imports
# routing_roles) raises a partial-init ImportError. Every gateway-importing test
# module hits it in isolation; the full suite happens to import routing_roles
# earlier. Importing it here first makes THIS module collectable in any order.
import general_ludd.routing_roles  # noqa: F401  (import-order warm-up, see above)
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.timeout_detector import ModelHealthTracker


def _server_error() -> httpx.HTTPStatusError:
    """A retryable 503 PROVIDER_ERROR (classified, retried, and recorded)."""
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    response = httpx.Response(503, request=request, text="service unavailable")
    return httpx.HTTPStatusError("503", request=request, response=response)


class _FakeChatModel:
    """Stand-in for a LangChain chat model.

    ``invoke`` raises a queued exception (to simulate a provider failure) or, if
    the queue is empty / the next item is a string, returns a response object
    with that string as ``.content`` and usage metadata so a cost is billed.
    """

    # Class-level queue so each provider_cls(**init_kwargs) instantiation (a new
    # object per call_model) shares one script across the whole test. This is a
    # SHARED MUTABLE by necessity (the gateway makes a fresh instance per call,
    # so a per-instance attribute could not carry the queue forward). Cross-test
    # leakage is fully prevented by the autouse ``_reset_script`` fixture below,
    # which reassigns a fresh empty list before EVERY test — no test observes
    # another test's residual queue.
    script: ClassVar[list[object]] = []

    def __init__(self, **init_kwargs: object) -> None:
        # _invoke_and_bill instantiates provider_cls(model=..., api_key=...);
        # accept and ignore whatever init kwargs it passes.
        self._init_kwargs = init_kwargs

    def invoke(self, messages: list[dict[str, str]]) -> object:
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
    # Runs before EVERY test: rebinds ``script`` to a brand-new empty list so the
    # shared class-level queue cannot carry residual items across tests. This is
    # what isolates the SHARED MUTABLE documented on _FakeChatModel.script.
    _FakeChatModel.script = []


def _make_gateway(
    tracker: ModelHealthTracker,
    *,
    fallback_profiles: list[str] | None = None,
) -> ModelGateway:
    profile = ModelProfile(
        model_profile_id="primary",
        provider="openai",
        model_name="fake-model",
        cost_per_input_token=0.001,
        cost_per_output_token=0.002,
        enabled=True,
        fallback_profiles=fallback_profiles or [],
    )
    return ModelGateway(
        profiles=[profile],
        provider_registry=_FakeRegistry(),
        health_tracker=tracker,
    )


class TestNoDoubleCount:
    def test_breaker_does_not_trip_below_threshold(self) -> None:
        """failure_threshold-1 real failures must NOT trip the breaker.

        With the old double-count, two real failures advanced the consecutive
        counter to 4 (>= threshold 3) and tripped early. With the fix, two real
        failures advance it to exactly 2 (< 3) and the profile stays healthy.

        Uses a ConnectError (transient kind) so max_retries=1 is honored —
        a 503 would be classified as PROVIDER_ERROR and get the overload
        budget (default 10 retries), tripping the breaker at threshold 3.
        """
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        gateway = _make_gateway(tracker)

        # 2 transient failures (one per attempt). max_retries=1 means exactly
        # 2 invocations total (1 initial + 1 retry).
        _conn_error = httpx.ConnectError("connection refused")
        _FakeChatModel.script = [_conn_error, _conn_error]
        with pytest.raises(httpx.ConnectError):
            gateway.call_model_with_retry(
                "primary", [{"role": "user", "content": "hi"}],
                max_retries=1, base_backoff_seconds=0.0,
            )

        # EXACTLY 2 consecutive failures recorded (no double-count -> would be 4).
        assert tracker._consecutive.get("primary") == 2
        assert tracker.is_healthy("primary") is True

    def test_breaker_trips_at_exactly_threshold(self) -> None:
        """Exactly failure_threshold real failures trips the breaker — no sooner.

        We drive three independent single-attempt failures through call_model
        (each records the failure once via record_timeout_on_failure) and assert
        the consecutive counter is exactly 3 and the breaker is now open.
        """
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        gateway = _make_gateway(tracker)

        for _ in range(3):
            _FakeChatModel.script = [_server_error()]
            with pytest.raises(httpx.HTTPStatusError):
                gateway.call_model("primary", [{"role": "user", "content": "hi"}])

        # Exactly 3 (one per real failure). Double-count would have read 6 and
        # the breaker would already have been open after the 2nd failure.
        assert tracker._consecutive.get("primary") == 3
        # is_healthy(admit_probe=False) would consume a probe slot otherwise.
        assert tracker.is_healthy("primary", admit_probe=False) is False


class TestSuccessReset:
    def test_call_model_success_resets_consecutive_counter(self) -> None:
        """A billed success via plain call_model clears the breaker counter.

        Before the fix, call_model never called record_success, so the counter
        accumulated across a failure->recovery boundary. Now a success resets it
        to 0 on the call_model path.
        """
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        gateway = _make_gateway(tracker)

        # Two failures first.
        for _ in range(2):
            _FakeChatModel.script = [_server_error()]
            with pytest.raises(httpx.HTTPStatusError):
                gateway.call_model("primary", [{"role": "user", "content": "hi"}])
        assert tracker._consecutive.get("primary") == 2

        # A successful (billed) call resets the counter to 0.
        _FakeChatModel.script = ["hello from provider"]
        resp = gateway.call_model("primary", [{"role": "user", "content": "hi"}])
        assert resp.content == "hello from provider"
        assert resp.cost_estimate > 0.0  # success was billed
        assert tracker._consecutive.get("primary") == 0
        assert tracker.is_healthy("primary") is True

    def test_success_after_open_breaker_via_call_model_recovers(self) -> None:
        """A success on the call_model path clears an already-open breaker."""
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        gateway = _make_gateway(tracker)

        for _ in range(3):
            _FakeChatModel.script = [_server_error()]
            with pytest.raises(httpx.HTTPStatusError):
                gateway.call_model("primary", [{"role": "user", "content": "hi"}])
        assert tracker.is_healthy("primary", admit_probe=False) is False

        _FakeChatModel.script = ["recovered"]
        gateway.call_model("primary", [{"role": "user", "content": "hi"}])
        assert tracker._consecutive.get("primary") == 0
        assert tracker.is_healthy("primary", admit_probe=False) is True
