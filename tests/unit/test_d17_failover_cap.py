"""D.17: Failover fallback concurrency cap — unit tests.

Exercises the per-profile ``threading.Semaphore`` gate in
``ModelGateway._fallback_semaphore()`` / ``_call_fallback()`` that caps
how many concurrent callers are in-flight to a fallback target at once,
preventing a primary outage from thundering-herd-cascading into a
secondary outage.

Uses the same ``_ScriptedChatModel`` infrastructure as the e2e failover
tests so transport failures are simulated with real httpx exception types
and real wall-clock delays for concurrency measurement.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import ClassVar, cast

import httpx
import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile

# ---- Scripted provider (same pattern as test_failover_e2e.py) -------------


class _ScriptedResp:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.usage_metadata = {"input_tokens": 5, "output_tokens": 3}
        self.response_metadata = {"finish_reason": finish_reason}
        self.tool_calls = None


class _ScriptedChatModel:
    SCRIPTS: ClassVar[dict[str, list[object]]] = {}
    CALL_COUNTS: ClassVar[dict[str, int]] = {}
    INFLIGHT: ClassVar[dict[str, int]] = {}
    MAX_INFLIGHT: ClassVar[dict[str, int]] = {}
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
                    _ScriptedChatModel.MAX_INFLIGHT.get(self._model, 0), cur,
                )

    def invoke(self, messages: list[dict[str, str]]) -> object:
        self._bump(1)
        try:
            delay = _ScriptedChatModel.DELAYS.get(self._model, 0.0)
            if delay > 0:
                threading.Event().wait(delay)
            _ScriptedChatModel.CALL_COUNTS[self._model] = (
                _ScriptedChatModel.CALL_COUNTS.get(self._model, 0) + 1
            )
            script = _ScriptedChatModel.SCRIPTS.get(self._model, [])
            if not script:
                raise AssertionError(
                    f"model={self._model!r} invoked more times than scripted"
                )
            item = script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return _ScriptedResp(str(item))
        finally:
            self._bump(-1)


class _ScriptedRegistry:
    def is_installed(self, provider_name: str) -> bool:
        return True

    def install_provider(self, provider_name: str) -> None:
        raise AssertionError("install_provider must not be called")

    def get_provider_class(self, provider_name: str) -> type[_ScriptedChatModel]:
        return _ScriptedChatModel


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_scripts() -> None:
    _ScriptedChatModel.SCRIPTS = {}
    _ScriptedChatModel.CALL_COUNTS = {}
    _ScriptedChatModel.INFLIGHT = {}
    _ScriptedChatModel.MAX_INFLIGHT = {}
    _ScriptedChatModel.DELAYS = {}


_PRIMARY = "primary-model"
_SECONDARY = "secondary-model"
_MSGS: list[dict[str, str]] = [{"role": "user", "content": "hello"}]


def _profile(pid: str, model: str, *, fallback_max_concurrency: int = 2) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        provider="openai",
        model_name=model,
        cost_per_input_token=0.001,
        cost_per_output_token=0.002,
        enabled=True,
        run_budget_usd=100.0,
        fallback_max_concurrency=fallback_max_concurrency,
    )


def _build_gateway(
    fb_concurrency: int = 2,
) -> ModelGateway:
    primary = _profile("primary", _PRIMARY, fallback_max_concurrency=fb_concurrency)
    secondary = _profile("secondary", _SECONDARY, fallback_max_concurrency=fb_concurrency)
    return ModelGateway(
        profiles=[primary, secondary],
        provider_registry=cast(object, _ScriptedRegistry()),
    )


# ---- Tests -----------------------------------------------------------------


class TestFallbackSemaphoreCreation:
    """_fallback_semaphore lazily creates per-profile Semaphores."""

    def test_creates_semaphore_with_configured_limit(self) -> None:
        gw = _build_gateway(fb_concurrency=3)
        sem = gw._fallback_semaphore("secondary")
        # threading.Semaphore._value reports available permits after acquire.
        # A freshly created Semaphore(3) has _value == 3.
        assert sem._value == 3

    def test_returns_same_semaphore_on_second_call(self) -> None:
        gw = _build_gateway()
        s1 = gw._fallback_semaphore("secondary")
        s2 = gw._fallback_semaphore("secondary")
        assert s1 is s2

    def test_independent_semaphores_per_profile(self) -> None:
        gw = _build_gateway()
        s_primary = gw._fallback_semaphore("primary")
        s_secondary = gw._fallback_semaphore("secondary")
        assert s_primary is not s_secondary
        assert s_primary._value == 2
        assert s_secondary._value == 2

    def test_unknown_profile_defaults_to_limit_2(self) -> None:
        gw = _build_gateway()
        sem = gw._fallback_semaphore("no-such-profile")
        assert sem._value == 2

    def test_pydantic_validator_rejects_zero(self) -> None:
        with pytest.raises(ValueError, match="must be at least 1"):
            _build_gateway(fb_concurrency=0)


class TestCallFallbackAcquireRelease:
    """_call_fallback acquires and releases the semaphore."""

    def test_semaphore_released_after_success(self) -> None:
        gw = _build_gateway()
        sem = gw._fallback_semaphore("secondary")
        before = sem._value
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 10

        gw._call_fallback("secondary", _MSGS)

        assert sem._value == before  # released

    def test_semaphore_released_after_failure(self) -> None:
        gw = _build_gateway()
        sem = gw._fallback_semaphore("secondary")
        before = sem._value
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = [
            httpx.ConnectError("connection refused")
        ] * 10

        with contextlib.suppress(httpx.ConnectError):
            gw._call_fallback("secondary", _MSGS)

        assert sem._value == before  # released even on failure

    def test_would_block_caller_when_semaphore_fully_acquired(self) -> None:
        gw = _build_gateway(fb_concurrency=1)
        gw._fallback_semaphore("secondary")
        _ScriptedChatModel.DELAYS[_SECONDARY] = 0.5
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 10

        acquired = threading.Event()
        proceed = threading.Event()
        result_holder: dict[str, bool] = {}

        def _holder() -> None:
            acquired.set()
            gw._call_fallback("secondary", _MSGS)
            proceed.wait()

        def _contender() -> None:
            acquired.wait()
            start = time.monotonic()
            gw._call_fallback("secondary", _MSGS)
            elapsed = time.monotonic() - start
            result_holder["blocked"] = elapsed >= 0.3  # thread 2 waits > 300ms

        t_hold = threading.Thread(target=_holder, daemon=True)
        t_block = threading.Thread(target=_contender, daemon=True)

        t_hold.start()
        t_block.start()

        t_hold.join(timeout=5)
        proceed.set()
        t_block.join(timeout=5)

        assert result_holder.get("blocked") is True, (
            "second caller was NOT blocked; semaphore did not constrain concurrency"
        )


class TestConcurrentFallbackCap:
    """Concurrent fallback callers are capped at fallback_max_concurrency."""

    def test_10_callers_capped_at_default_limit_2(self) -> None:
        gw = _build_gateway(fb_concurrency=2)
        _ScriptedChatModel.DELAYS[_SECONDARY] = 0.05
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 20

        barrier = threading.Barrier(10)

        def _one_call() -> None:
            barrier.wait()
            with contextlib.suppress(Exception):
                gw._call_fallback("secondary", _MSGS)

        threads = [threading.Thread(target=_one_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        max_inflight = _ScriptedChatModel.MAX_INFLIGHT.get(_SECONDARY, 0)
        assert max_inflight <= 2, (
            f"secondary saw {max_inflight} concurrent; expected <= 2"
        )
        # All 10 callers should have completed (none dropped due to timeout).
        assert _ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0) == 10, (
            f"expected 10 completed calls, got {_ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0)}"
        )

    def test_10_callers_capped_at_limit_3(self) -> None:
        gw = _build_gateway(fb_concurrency=3)
        _ScriptedChatModel.DELAYS[_SECONDARY] = 0.05
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 20

        barrier = threading.Barrier(10)

        def _one_call() -> None:
            barrier.wait()
            with contextlib.suppress(Exception):
                gw._call_fallback("secondary", _MSGS)

        threads = [threading.Thread(target=_one_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        max_inflight = _ScriptedChatModel.MAX_INFLIGHT.get(_SECONDARY, 0)
        assert max_inflight <= 3, (
            f"secondary saw {max_inflight} concurrent; expected <= 3"
        )

    def test_parallel_primary_and_secondary_have_independent_caps(self) -> None:
        gw = _build_gateway(fb_concurrency=2)
        _ScriptedChatModel.DELAYS[_PRIMARY] = 0.05
        _ScriptedChatModel.DELAYS[_SECONDARY] = 0.05
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = ["ok"] * 10
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 10

        barrier = threading.Barrier(20)

        def _call_primary() -> None:
            barrier.wait()
            with contextlib.suppress(Exception):
                gw._call_fallback("primary", _MSGS)

        def _call_secondary() -> None:
            barrier.wait()
            with contextlib.suppress(Exception):
                gw._call_fallback("secondary", _MSGS)

        threads = [threading.Thread(target=_call_primary) for _ in range(10)]
        threads += [threading.Thread(target=_call_secondary) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        primary_max = _ScriptedChatModel.MAX_INFLIGHT.get(_PRIMARY, 0)
        secondary_max = _ScriptedChatModel.MAX_INFLIGHT.get(_SECONDARY, 0)
        assert primary_max == 2, (
            f"primary peak={primary_max}, expected 2"
        )
        assert secondary_max == 2, (
            f"secondary peak={secondary_max}, expected 2"
        )


class TestFallbackCapacityExhausted:
    """Timeout on semaphore acquire raises RuntimeError."""

    def test_raises_runtime_error_when_no_slot_available(self) -> None:
        gw = _build_gateway(fb_concurrency=1)
        _ScriptedChatModel.DELAYS[_SECONDARY] = 10.0  # hold slot for 10s
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 10

        barrier = threading.Barrier(2)
        outcomes: list[dict[str, str]] = []
        _lock = threading.Lock()

        def _try_call(label: str) -> None:
            barrier.wait()
            try:
                gw._call_fallback("secondary", _MSGS)
            except RuntimeError as exc:
                with _lock:
                    outcomes.append({"label": label, "error": str(exc)})

        t_hold = threading.Thread(target=_try_call, args=("holder",), daemon=True)
        t_contend = threading.Thread(target=_try_call, args=("contender",), daemon=True)
        t_hold.start()
        t_contend.start()
        t_hold.join(timeout=15)
        t_contend.join(timeout=15)

        assert len(outcomes) == 1, (
            f"expected exactly 1 RuntimeError (one slot, two callers), "
            f"got {outcomes}"
        )
        assert "fallback capacity exhausted" in outcomes[0]["error"]
        assert _ScriptedChatModel.CALL_COUNTS.get(_SECONDARY, 0) == 1

    def test_fallbacks_to_different_profiles_use_independent_slots(self) -> None:
        gw = _build_gateway(fb_concurrency=1)
        _ScriptedChatModel.DELAYS[_SECONDARY] = 10.0
        _ScriptedChatModel.DELAYS[_PRIMARY] = 10.0
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 10
        _ScriptedChatModel.SCRIPTS[_PRIMARY] = ["ok"] * 10

        barrier = threading.Barrier(4)
        events: list[dict[str, str]] = []
        _lock = threading.Lock()

        def _try_call(label: str, profile: str) -> None:
            barrier.wait()
            try:
                gw._call_fallback(profile, _MSGS)
                with _lock:
                    events.append({"label": label, "outcome": "success"})
            except RuntimeError:
                with _lock:
                    events.append({"label": label, "outcome": "RuntimeError"})

        threads = [
            threading.Thread(target=_try_call, args=("hold-sec", "secondary"), daemon=True),
            threading.Thread(target=_try_call, args=("hold-pri", "primary"), daemon=True),
            threading.Thread(target=_try_call, args=("contend-sec", "secondary"), daemon=True),
            threading.Thread(target=_try_call, args=("contend-pri", "primary"), daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(events) == 4, f"expected 4 events, got {len(events)}: {events}"
        runtime_errors = [e for e in events if e["outcome"] == "RuntimeError"]
        successes = [e for e in events if e["outcome"] == "success"]
        assert len(runtime_errors) == 2, (
            f"expected 2 RuntimeErrors (one per profile), got {runtime_errors}"
        )
        assert len(successes) == 2, (
            f"expected 2 successes (one per profile), got {successes}"
        )
        sec_errors = [e for e in runtime_errors if "sec" in e["label"]]
        pri_errors = [e for e in runtime_errors if "pri" in e["label"]]
        assert len(sec_errors) == 1, f"secondary errors: {sec_errors}"
        assert len(pri_errors) == 1, f"primary errors: {pri_errors}"


class TestSemaphoreVersusBoundedSemaphore:
    """Regression: threading.Semaphore (not BoundedSemaphore) is a deliberate choice
    here because the acquire/release pairs are inside a try/finally that is NOT
    re-entrant — a single release() call for each acquire(). BoundedSemaphore's
    ValueError-on-overrelease would fire only due to a programming bug, which
    the try/finally pattern prevents. The guardrail test below proves that the
    release count never exceeds the acquire count under load."""

    def test_10_concurrent_calls_do_not_over_release(self) -> None:
        gw = _build_gateway(fb_concurrency=2)
        sem = gw._fallback_semaphore("secondary")
        initial = sem._value
        _ScriptedChatModel.SCRIPTS[_SECONDARY] = ["ok"] * 20

        barrier = threading.Barrier(10)

        def _one_call() -> None:
            barrier.wait()
            with contextlib.suppress(Exception):
                gw._call_fallback("secondary", _MSGS)

        threads = [threading.Thread(target=_one_call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # After all calls complete, the semaphore value should be exactly
        # the initial value (all acquires were matched by releases).
        assert sem._value == initial, (
            f"semaphore at {sem._value} after 10 calls, expected {initial}"
        )
