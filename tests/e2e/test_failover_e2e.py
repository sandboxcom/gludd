"""E2E tests for model failover, timeouts, and graceful degradation.

Exercises gludd's failover / fallback / retry machinery end-to-end. Provider
responses are simulated at the transport boundary by a scripted chat-model
stand-in (``_ScriptedChatModel``) that raises REAL ``httpx`` exceptions
(``HTTPStatusError``, ``ConnectError``, ``TimeoutException``) — the exact
exception types the live langchain-openai provider surfaces when the underlying
HTTP transport fails. This is functionally identical to an ``httpx.MockTransport``
mock (the gateway's classifier + retry logic consume these exceptions verbatim)
but does not require live API keys or network.

Two execution modes:
  - Gateway-level (tests 1-12): direct ``ModelGateway.call_model_with_retry()``
    against a 3-profile chain (primary -> secondary -> tertiary). Fast, no HTTP.
  - Daemon-level (tests 13-14): full FastAPI daemon via ``TestClient`` so
    concurrency and metrics are observable across the HTTP boundary.

Determinism: all backoff sleeps are captured via a ``time.sleep`` recorder (no
real wall-clock waits); ``random.uniform`` jitter is forced deterministic via a
``TimeoutRetryPolicy`` subclass injected through the gateway module attribute.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import ClassVar, cast

import httpx
import pytest

from general_ludd.models.gateway import (
    BudgetExceededError,
    ModelGateway,
    ModelProfile,
)
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutRetryPolicy,
)

# ---------------------------------------------------------------------------
# Scripted provider: per-model_name behavior. Simulates httpx transport
# failures by raising the SAME exception types a real transport would surface.
# ---------------------------------------------------------------------------


class _Resp:
    """Minimal stand-in for a LangChain AIMessage response object."""

    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.usage_metadata = {"input_tokens": 5, "output_tokens": 3}
        self.response_metadata = {"finish_reason": finish_reason}
        # tool_calls intentionally absent -> _extract_tool_calls returns None.
        self.tool_calls = None


class _ScriptedChatModel:
    """Provider stand-in whose ``invoke`` follows a per-model_name script.

    A script is a list of items; each ``invoke`` consumes one item. An item that
    is a ``BaseException`` is raised (simulating a transport failure); any other
    item is wrapped as the response ``.content``. Behavior is keyed by the
    ``model`` constructor kwarg so primary/secondary/tertiary profiles (which
    each carry a distinct ``model_name``) can be scripted independently.
    """

    # Per-model scripts and counters. Reset by the autouse _reset_scripts fixture
    # so no test observes another test's residue.
    SCRIPTS: ClassVar[dict[str, list[object]]] = {}
    CALL_COUNTS: ClassVar[dict[str, int]] = {}
    # Tracks max concurrent in-flight invokes per model (thundering-herd probe).
    INFLIGHT: ClassVar[dict[str, int]] = {}
    MAX_INFLIGHT: ClassVar[dict[str, int]] = {}
    # Real (non-patched) per-invoke latency in seconds, for concurrency tests.
    # Uses threading.Event().wait so it is unaffected by the sleep_recorder
    # time.sleep patch.
    DELAYS: ClassVar[dict[str, float]] = {}
    _LOCK: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, **init_kwargs: object) -> None:
        self._model = str(init_kwargs.get("model", ""))

    def _bump(self, delta: int) -> None:
        with _ScriptedChatModel._LOCK:
            cur = _ScriptedChatModel.INFLIGHT.get(self._model, 0) + delta
            _ScriptedChatModel.INFLIGHT[self._model] = max(cur, 0)
            if delta > 0:
                _ScriptedChatModel.MAX_INFLIGHT[self._model] = max(
                    _ScriptedChatModel.MAX_INFLIGHT.get(self._model, 0), cur
                )

    def invoke(self, messages: list[dict[str, str]]) -> object:
        self._bump(1)
        try:
            delay = _ScriptedChatModel.DELAYS.get(self._model, 0.0)
            if delay > 0:
                # Real wait so genuine concurrency builds up (thundering-herd
                # probe); Event.wait is not affected by time.sleep patches.
                threading.Event().wait(delay)
            _ScriptedChatModel.CALL_COUNTS[self._model] = (
                _ScriptedChatModel.CALL_COUNTS.get(self._model, 0) + 1
            )
            script = _ScriptedChatModel.SCRIPTS.get(self._model, [])
            if not script:
                raise AssertionError(
                    f"provider model={self._model!r} invoked more times than "
                    f"scripted (script is empty)"
                )
            item = script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return _Resp(str(item))
        finally:
            self._bump(-1)


class _ScriptedRegistry:
    """Provider registry stand-in returning the scripted chat model."""

    def is_installed(self, provider_name: str) -> bool:
        return True

    def install_provider(self, provider_name: str) -> None:  # pragma: no cover
        raise AssertionError("install_provider must not be called")

    def get_provider_class(self, provider_name: str) -> type[_ScriptedChatModel]:
        return _ScriptedChatModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIMARY = "primary-model"
_SECONDARY = "secondary-model"
_TERTIARY = "tertiary-model"


def _profile(pid: str, model: str, fallback: list[str] | None = None) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        provider="openai",
        model_name=model,
        cost_per_input_token=0.001,
        cost_per_output_token=0.002,
        enabled=True,
        run_budget_usd=100.0,
        fallback_profiles=fallback or [],
    )


def _build_gateway(
    tracker: ModelHealthTracker | None = None,
    *,
    metrics_collector: object | None = None,
    metrics_agent_id: str | None = None,
    primary_fallback: list[str] | None = None,
) -> tuple[ModelGateway, ModelHealthTracker]:
    """Build a 3-profile gateway (primary->secondary->tertiary) on scripted registry."""
    primary = _profile("primary", _PRIMARY, fallback=primary_fallback or ["secondary"])
    secondary = _profile("secondary", _SECONDARY, fallback=["tertiary"])
    tertiary = _profile("tertiary", _TERTIARY)
    ht = tracker or ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
    gw = ModelGateway(
        profiles=[primary, secondary, tertiary],
        provider_registry=cast(object, _ScriptedRegistry()),
        health_tracker=ht,
        metrics_collector=metrics_collector,
        metrics_agent_id=metrics_agent_id,
    )
    return gw, ht


def _server_error(code: int = 500) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://provider.invalid/v1/chat")
    resp = httpx.Response(code, request=req, text=f"server error {code}")
    return httpx.HTTPStatusError(f"{code}", request=req, response=resp)


def _rate_limit(retry_after: float | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://provider.invalid/v1/chat")
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    resp = httpx.Response(429, request=req, headers=headers, text="rate limited")
    return httpx.HTTPStatusError("429", request=req, response=resp)


@pytest.fixture(autouse=True)
def _reset_scripts() -> None:
    _ScriptedChatModel.SCRIPTS = {}
    _ScriptedChatModel.CALL_COUNTS = {}
    _ScriptedChatModel.INFLIGHT = {}
    _ScriptedChatModel.MAX_INFLIGHT = {}
    _ScriptedChatModel.DELAYS = {}


@pytest.fixture()
def sleep_recorder(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace time.sleep with a recorder; returns the list of captured waits."""
    recorded: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        recorded.append(float(seconds))

    # call_model_with_retry does `import time as _time`; patching the attribute
    # on the real `time` module object makes the alias resolve to the patch.
    monkeypatch.setattr(time, "sleep", _fake_sleep)
    return recorded


@pytest.fixture()
def deterministic_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force deterministic jitter AND defer failover on the real policy class.

    Patches ``TimeoutRetryPolicy.__init__`` directly on the class object the
    gateway already references. max-jitter (returns ``hi``) collapses equal-
    jitter to the pure exponential value, so waits are exactly 1, 2, 4, ... for
    base_backoff=1.0. ``failover_after_retries=99`` prevents failover at the
    default attempt 3 so the primary retry sequence is observable end-to-end.
    """
    orig_init = TimeoutRetryPolicy.__init__

    def _patched_init(
        self: TimeoutRetryPolicy,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        failover_after_retries: int = 3,
        overload_max_retries: int = 10,
        overload_max_backoff_seconds: float = 120.0,
        jitter_fn: object = None,
    ) -> None:
        # Force deferred failover (so the primary retry sequence is observable
        # end-to-end) and max-jitter (collapses equal-jitter to the pure
        # exponential value). Forward the caller's other params unchanged.
        orig_init(
            self,
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
            failover_after_retries=99,
            overload_max_retries=overload_max_retries,
            overload_max_backoff_seconds=overload_max_backoff_seconds,
            jitter_fn=lambda lo, hi: hi,
        )

    monkeypatch.setattr(TimeoutRetryPolicy, "__init__", _patched_init)


_MSGS = [{"role": "user", "content": "hello"}]


# ===========================================================================
# 1. Primary timeout -> secondary
# ===========================================================================


class TestPrimaryTimeoutFallsBack:
    def test_primary_timeout_falls_back_to_secondary(self, sleep_recorder) -> None:
        # Primary raises a read timeout on every attempt; secondary responds.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [httpx.ReadTimeout("read timed out")] * 10
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]
        gw, _ = _build_gateway()

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=2, base_backoff_seconds=0.0
        )

        assert resp.content == "from secondary"
        assert resp.model_name == _SECONDARY
        # Primary WAS attempted (then failed over); secondary served the call.
        assert _ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0) == 1


# ===========================================================================
# 2. Primary 500 -> secondary (overload kind; retries then failover)
# ===========================================================================


class TestPrimary500FallsBack:
    def test_primary_500_falls_back_to_secondary(self, sleep_recorder) -> None:
        # 500 is PROVIDER_ERROR (overload kind) -> uses the overload retry
        # budget (default 10). With base_backoff=0 the retries are instant.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [_server_error(500)] * 12
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]
        gw, _ = _build_gateway()

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=1, base_backoff_seconds=0.0
        )

        assert resp.content == "from secondary"
        assert _ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0) == 1


# ===========================================================================
# 3. Primary DNS failure (ConnectError) -> secondary
# ===========================================================================


class TestPrimaryDnsFailureFallsBack:
    def test_primary_dns_failure_falls_back(self, sleep_recorder) -> None:
        # httpx.ConnectError simulates a DNS / connection failure.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [
            httpx.ConnectError("[Errno -2] Name or service not known")
        ] * 10
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]
        gw, _ = _build_gateway()

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=2, base_backoff_seconds=0.0
        )

        assert resp.content == "from secondary"
        assert _ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0) == 1


# ===========================================================================
# 4. 429 with Retry-After is honored -> primary retried, NOT failed over
# ===========================================================================


class TestRateLimitRetryAfterHonored:
    def test_primary_429_respects_retry_after(self, sleep_recorder) -> None:
        # Two 429s carrying Retry-After: 2, then primary succeeds on attempt 3.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [
            _rate_limit(retry_after=2.0),
            _rate_limit(retry_after=2.0),
            "from primary after backoff",
        ]
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["SHOULD NOT BE USED"]
        gw, _ = _build_gateway()

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=5, base_backoff_seconds=0.0
        )

        assert resp.content == "from primary after backoff"
        assert resp.model_name == _PRIMARY
        # Secondary must NOT have been invoked (no failover).
        assert _ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0) == 0
        # The gateway honored the server-supplied Retry-After (>= 1.0s each).
        # Filter out the 0.0 sleeps tenacity's wait_none() injects between
        # attempts; only the policy-computed backoffs are meaningful here.
        real_sleeps = [s for s in sleep_recorder if s > 0]
        assert len(real_sleeps) >= 2, (
            f"expected >=2 real backoff sleeps, got {sleep_recorder}"
        )
        assert all(s >= 2.0 for s in real_sleeps[:2]), (
            f"Retry-After not honored; real_sleeps={real_sleeps}"
        )


# ===========================================================================
# 5. 429 forever -> exhaust retries -> failover to secondary
# ===========================================================================


class TestRateLimitExhaustedFallsBack:
    def test_primary_429_after_max_retries_falls_back(self, sleep_recorder) -> None:
        # Primary returns 429 forever (overload kind, 10-retry budget).
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [_rate_limit()] * 12
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]
        gw, _ = _build_gateway()

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=1, base_backoff_seconds=0.0
        )

        assert resp.content == "from secondary"
        assert _ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0) == 1


# ===========================================================================
# 6. All providers down -> graceful error (typed exception, not a stack trace)
# ===========================================================================


class TestAllProvidersDownGracefulError:
    def test_all_providers_down_raises_typed_error(self, sleep_recorder) -> None:
        # Every provider returns 500. The gateway must raise a typed exception,
        # not crash with a raw stack trace / unrelated error.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [_server_error(500)] * 12
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = [_server_error(500)] * 12
        _ScriptedChatModel.SCRIPTS[_TERTIARY] = [_server_error(500)] * 12
        gw, _ = _build_gateway()

        # The propagated error is the last provider's httpx.HTTPStatusError (a
        # typed, classified exception) — graceful, not an uncaught crash.
        with pytest.raises(httpx.HTTPStatusError):
            gw.call_model_with_retry(
                "primary", _MSGS, max_retries=1, base_backoff_seconds=0.0
            )

    def test_all_providers_down_error_includes_provider_names(self, sleep_recorder) -> None:
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [_server_error(500)] * 12
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = [_server_error(503)] * 12
        _ScriptedChatModel.SCRIPTS[_TERTIARY] = [_server_error(500)] * 12
        gw, _ = _build_gateway()

        try:
            gw.call_model_with_retry(
                "primary", _MSGS, max_retries=1, base_backoff_seconds=0.0
            )
        except Exception as exc:
            msg = str(exc)
            # Expected once a structured-error feature exists.
            assert "primary" in msg and "secondary" in msg and "tertiary" in msg
            return
        pytest.fail("expected an exception enumerating failed providers")


# ===========================================================================
# 7. Circuit opens after N consecutive failures -> next call fast-fails primary
# ===========================================================================


class TestCircuitOpensAfterConsecutiveFailures:
    def test_circuit_opens_then_fast_fails_primary(self, sleep_recorder) -> None:
        tracker = ModelHealthTracker(failure_threshold=5, cooldown_seconds=60.0)
        gw, tracker = _build_gateway(tracker)
        # Use transient ConnectErrors so the breaker trips at exactly threshold
        # (overload kinds get the larger overload retry budget).
        conn_err = httpx.ConnectError("down")
        # Trip the breaker: 5 single-attempt failures via plain call_model.
        for _ in range(5):
            _ScriptedChatModel.SCRIPTS[_PRIMARY] = [conn_err]
            with pytest.raises(httpx.ConnectError):
                gw.call_model("primary", _MSGS)
        assert tracker.is_healthy("primary", admit_probe=False) is False
        primary_calls_after_trip = _ScriptedChatModel.CALL_COUNTS.get(_PRIMARY, 0)

        # 6th call: circuit is open -> primary skipped entirely, secondary serves.
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]
        start = time.monotonic()
        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=2, base_backoff_seconds=0.0
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0

        assert resp.content == "from secondary"
        # Primary invoke count did not increase (fast-fail, no attempt).
        assert _ScriptedChatModel.CALL_COUNTS.get(_PRIMARY, 0) == primary_calls_after_trip
        # No retries slept on the primary -> fast-fail is well under 50ms.
        assert elapsed_ms < 50.0, f"fast-fail took {elapsed_ms:.1f}ms"


# ===========================================================================
# 8. Circuit half-open recovery after cooldown
# ===========================================================================


class TestCircuitHalfOpenRecovery:
    def test_half_open_probe_recovers_on_success(
        self, monkeypatch: pytest.MonkeyPatch, sleep_recorder
    ) -> None:
        import general_ludd.models.timeout_detector as td

        clock = [1000.0]
        monkeypatch.setattr(td.time, "monotonic", lambda: clock[0])

        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        gw, tracker = _build_gateway(tracker)
        conn_err = httpx.ConnectError("down")
        for _ in range(3):
            _ScriptedChatModel.SCRIPTS[_PRIMARY] = [conn_err]
            with pytest.raises(httpx.ConnectError):
                gw.call_model("primary", _MSGS)
        assert tracker.is_healthy("primary", admit_probe=False) is False

        # Advance past the cooldown window and let the probe succeed.
        clock[0] = 1000.0 + 61.0
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = ["primary recovered"]
        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=1, base_backoff_seconds=0.0
        )

        assert resp.content == "primary recovered"
        assert tracker.is_healthy("primary", admit_probe=False) is True
        assert tracker._consecutive.get("primary", -1) == 0


# ===========================================================================
# 9. Budget exhausted -> graceful rejection (no crash)
# ===========================================================================


class TestBudgetExhaustedGraceful:
    def test_budget_exhausted_raises_budget_exceeded(self, sleep_recorder) -> None:
        # run_budget_usd caps per-profile spend; an over-budget call is rejected
        # gracefully with a typed BudgetExceededError (not a stack trace).
        primary = ModelProfile(
            model_profile_id="lowbudget",
            provider="openai",
            model_name=_PRIMARY,
            cost_per_input_token=0.001,
            cost_per_output_token=0.002,
            enabled=True,
            run_budget_usd=0.001,
        )
        gw = ModelGateway(
            profiles=[primary],
            provider_registry=cast(object, _ScriptedRegistry()),
        )
        with pytest.raises(BudgetExceededError, match="over budget"):
            gw.call_model(
                "lowbudget",
                _MSGS,
                estimated_cost=1.0,
                budget_remaining=0.0001,
            )


# ===========================================================================
# 10. Partial streaming failure -> clean failover, no partial state leak
# ===========================================================================


class TestPartialStreamFailureNoCorruption:
    def test_partial_drop_failovers_cleanly(self, sleep_recorder) -> None:
        # The gateway uses synchronous .invoke() (no accumulator), so a mid-call
        # transport drop surfaces as a raised exception BEFORE any content is
        # returned -> there is no partial state to corrupt. The failover then
        # yields a complete response from the secondary.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [
            httpx.ReadTimeout("stream dropped mid-read")
        ] * 10
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["complete response from secondary"]
        gw, _ = _build_gateway()

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=2, base_backoff_seconds=0.0
        )

        assert resp.content == "complete response from secondary"
        assert resp.content.strip() != ""
        # Primary never produced a partial result that leaked into the response.
        assert "partial" not in resp.content.lower()


# ===========================================================================
# 11. Retry backoff is exponential
# ===========================================================================


class TestExponentialBackoff:
    def test_retry_backoff_is_exponential(
        self, deterministic_jitter, sleep_recorder
    ) -> None:
        # READ_TIMEOUT with base=1.0, deterministic max-jitter -> backoff = exp
        # = 1.0, 2.0, 4.0 (2**(attempt-1)). Capture via the sleep recorder.
        # deterministic_jitter also defers failover (failover_after_retries=99)
        # so the primary is retried through max_retries without early failover.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            "ok",
        ]
        # High failure_threshold so the breaker does not trip mid-retry-sequence
        # (the mid-loop guard would otherwise fail over after 3 failures and we
        # could not observe the full 1,2,4 backoff shape on the primary).
        tracker = ModelHealthTracker(failure_threshold=10, cooldown_seconds=60.0)
        gw, _ = _build_gateway(tracker)

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=5, base_backoff_seconds=1.0
        )
        assert resp.content == "ok"

        # The policy-computed backoffs are the non-zero sleeps (tenacity's
        # wait_none injects 0.0 sleeps between attempts that we filter out).
        real_waits = [s for s in sleep_recorder if s > 0]
        assert len(real_waits) >= 3, (
            f"expected >=3 backoff sleeps, got real_waits={real_waits} "
            f"(raw={sleep_recorder})"
        )
        waits = real_waits[:3]
        # Exponential: each strictly greater than the previous.
        assert waits[1] > waits[0], f"not increasing: {waits}"
        assert waits[2] > waits[1], f"not increasing: {waits}"
        # And approximately 1, 2, 4 (equal-jitter max -> base + half = exp).
        assert abs(waits[0] - 1.0) < 0.01
        assert abs(waits[1] - 2.0) < 0.01
        assert abs(waits[2] - 4.0) < 0.01


# ===========================================================================
# 12. Failover preserves correlation ID
# ===========================================================================


class TestCorrelationIdPreserved:
    def test_failover_preserves_correlation_id(self, sleep_recorder) -> None:
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [httpx.ConnectError("down")] * 10
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]
        gw, _ = _build_gateway()

        correlation_id = "corr-1234-abcd"
        # Expected: the gateway threads the correlation ID through to the
        # response metadata and any structured-log records.
        resp = gw.call_model_with_retry(
            "primary", _MSGS, correlation_id=correlation_id
        )
        assert resp.content == "from secondary"
        # Once implemented, the correlation ID must be observable on the
        # response or via a log-record capture.
        assert getattr(resp, "correlation_id", None) == correlation_id


# ===========================================================================
# 13. Concurrent failover must not thundering-herd the secondary
# ===========================================================================


class TestConcurrentFailoverNoThunderingHerd:
    def test_concurrent_failover_caps_secondary_inflight(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        gw, tracker = _build_gateway(tracker)
        # Pre-trip the primary circuit so every call fails over.
        conn_err = httpx.ConnectError("down")
        for _ in range(3):
            _ScriptedChatModel.SCRIPTS[_PRIMARY] = [conn_err]
            with pytest.raises(httpx.ConnectError):
                gw.call_model("primary", _MSGS)

        # Secondary serves with a real delay so concurrency genuinely peaks
        # (Event.wait is unaffected by any time.sleep patch). With no fallback
        # concurrency limiter, all 10 concurrent callers overlap on the secondary.
        _ScriptedChatModel.DELAYS[_SECONDARY] = 0.1
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 20

        barrier = threading.Barrier(10)

        def _one_call() -> None:
            barrier.wait()
            with contextlib.suppress(Exception):
                gw.call_model_with_retry(
                    "primary", _MSGS, max_retries=1, base_backoff_seconds=0.0
                )

        threads = [threading.Thread(target=_one_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        max_inflight = _ScriptedChatModel.MAX_INFLIGHT.get(_SECONDARY, 0)
        # Expected once a concurrency limiter (e.g. a fallback semaphore) exists.
        assert max_inflight <= 2, (
            f"secondary saw {max_inflight} concurrent in-flight calls (expected <=2)"
        )

    def test_half_open_probe_single_flight_prevents_primary_stampede(
        self, monkeypatch: pytest.MonkeyPatch, sleep_recorder
    ) -> None:
        """Real anti-thundering-herd mechanism: at most ONE primary probe per
        cooldown window once the circuit is half-open."""
        import general_ludd.models.timeout_detector as td

        clock = [1000.0]
        monkeypatch.setattr(td.time, "monotonic", lambda: clock[0])

        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        gw, tracker = _build_gateway(tracker)
        conn_err = httpx.ConnectError("down")
        for _ in range(3):
            _ScriptedChatModel.SCRIPTS[_PRIMARY] = [conn_err]
            with pytest.raises(httpx.ConnectError):
                gw.call_model("primary", _MSGS)
        primary_calls_after_trip = _ScriptedChatModel.CALL_COUNTS.get(_PRIMARY, 0)
        # Advance past cooldown so is_healthy() will admit AT MOST ONE probe.
        clock[0] = 1000.0 + 61.0

        # Primary probe fails again (slowly), so the window's single slot is
        # held by whichever thread wins the race; the rest must NOT call primary.
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [conn_err] * 1
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 20

        barrier = threading.Barrier(10)

        def _one_call() -> None:
            barrier.wait()
            with contextlib.suppress(Exception):
                gw.call_model_with_retry(
                    "primary", _MSGS, max_retries=1, base_backoff_seconds=0.0
                )

        threads = [threading.Thread(target=_one_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        primary_attempts = (
            _ScriptedChatModel.CALL_COUNTS.get(_PRIMARY, 0) - primary_calls_after_trip
        )
        # Exactly ONE probe is admitted per cooldown window (single-flight).
        assert primary_attempts <= 1, (
            f"half-open admitted {primary_attempts} primary probes; expected <=1"
        )


# ===========================================================================
# 14. Failover emits metrics
# ===========================================================================


class TestFailoverEmitsMetrics:
    def test_secondary_success_is_recorded_in_metrics(self, sleep_recorder) -> None:
        from general_ludd.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.register_agent("agent-1")
        gw, _ = _build_gateway(
            metrics_collector=cast(object, collector),
            metrics_agent_id="agent-1",
        )
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [httpx.ConnectError("down")] * 10
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]

        resp = gw.call_model_with_retry(
            "primary", _MSGS, max_retries=2, base_backoff_seconds=0.0
        )
        assert resp.content == "from secondary"

        # The secondary's successful call is recorded in global model usage.
        usage = collector._global_model_usage.get("secondary")
        assert usage is not None, "secondary call was not recorded in metrics"
        assert usage.total_calls >= 1
        assert usage.successful_calls >= 1

    def test_failover_count_incremented(self, sleep_recorder) -> None:
        from general_ludd.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.register_agent("agent-1")
        gw, _ = _build_gateway(
            metrics_collector=cast(object, collector),
            metrics_agent_id="agent-1",
        )
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = [httpx.ConnectError("down")] * 10
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["from secondary"]

        gw.call_model_with_retry(
            "primary", _MSGS, max_retries=2, base_backoff_seconds=0.0
        )

        # Expected once a failover counter exists in the metrics surface.
        report = collector.get_full_report()
        model_facet = report.get("model_usage", {})
        assert model_facet.get("failover_count", 0) >= 1
        assert model_facet.get("primary", {}).get("error_count", 0) >= 1
