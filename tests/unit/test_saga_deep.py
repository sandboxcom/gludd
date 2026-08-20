"""Deep saga orchestrator tests — steps, compensation, persistence, timeout, retry, builder."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from general_ludd.sagas.orchestrator import (
    SagaBuilder,
    SagaCompensationFailure,
    SagaConfig,
    SagaError,
    SagaOrchestrator,
    SagaState,
    SagaStep,
    SagaStepFailure,
    SagaTimeout,
    StepResult,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def save(self, saga_id: str, state: dict[str, Any]) -> None:
        self._data[saga_id] = json.loads(json.dumps(state))

    def load(self, saga_id: str) -> dict[str, Any] | None:
        return self._data.get(saga_id)

    def delete(self, saga_id: str) -> None:
        self._data.pop(saga_id, None)


class Counter:
    def __init__(self) -> None:
        self.count = 0
        self.compensations: list[str] = []
        self.actions: list[str] = []

    def action(self, name: str) -> None:
        self.count += 1
        self.actions.append(name)

    def compensate(self, name: str) -> None:
        self.compensations.append(name)


class FailingAction:
    def __init__(self, fail_on_call: int = 1) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def __call__(self) -> None:
        self.calls += 1
        if self.calls >= self.fail_on_call:
            raise RuntimeError(f"intentional failure on call {self.calls}")


def _slow_action(seconds: float) -> None:
    time.sleep(seconds)


# ===========================================================================
# SagaStep dataclass
# ===========================================================================


class TestSagaStep:
    def test_step_defaults(self):
        step = SagaStep("test", action=lambda: None)
        assert step.name == "test"
        assert step.compensation is None
        assert step.timeout_seconds == 30.0
        assert step.retry_count == 0
        assert step.retry_delay_seconds == 0.1

    def test_step_with_compensation(self):
        c = Counter()
        step = SagaStep("s1", action=lambda: c.action("s1"), compensation=lambda: c.compensate("s1"))
        assert step.compensation is not None

    def test_step_custom_timeout(self):
        step = SagaStep("s1", action=lambda: None, timeout_seconds=5.0)
        assert step.timeout_seconds == 5.0

    def test_step_retry_config(self):
        step = SagaStep("s1", action=lambda: None, retry_count=2, retry_delay_seconds=0.5)
        assert step.retry_count == 2
        assert step.retry_delay_seconds == 0.5


# ===========================================================================
# SagaConfig
# ===========================================================================


class TestSagaConfig:
    def test_defaults(self):
        c = SagaConfig()
        assert c.max_retries == 3
        assert c.retry_backoff_multiplier == 2.0
        assert c.persist_on_each_step is True
        assert c.compensation_timeout_seconds == 10.0


# ===========================================================================
# SagaOrchestrator — successful runs
# ===========================================================================


class TestSagaHappyPath:
    def test_all_steps_succeed_returns_results(self):
        c = Counter()
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("a", action=lambda: c.action("a"), compensation=lambda: c.compensate("a")),
                SagaStep("b", action=lambda: c.action("b"), compensation=lambda: c.compensate("b")),
                SagaStep("c", action=lambda: c.action("c"), compensation=lambda: c.compensate("c")),
            ],
        )
        results = saga.run()
        assert saga.state == SagaState.COMPLETED
        assert len(results) == 3
        assert all(r.success for r in results)
        assert c.actions == ["a", "b", "c"]
        assert c.compensations == []

    def test_single_step_saga(self):
        c = Counter()
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("only", action=lambda: c.action("only")),
            ],
        )
        results = saga.run()
        assert saga.state == SagaState.COMPLETED
        assert len(results) == 1
        assert results[0].success
        assert c.actions == ["only"]

    def test_results_have_step_index_and_duration(self):
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("a", action=lambda: None),
                SagaStep("b", action=lambda: None),
            ],
        )
        results = saga.run()
        assert results[0].step_index == 0
        assert results[0].step_name == "a"
        assert results[0].duration_ms > 0
        assert results[1].step_index == 1
        assert results[1].step_name == "b"

    def test_step_count_property(self):
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("a", action=lambda: None),
                SagaStep("b", action=lambda: None),
            ],
        )
        assert saga.step_count == 2

    def test_elapsed_seconds_is_positive_after_run(self):
        saga = SagaOrchestrator("s1", [SagaStep("a", action=lambda: time.sleep(0.01))])
        assert saga.elapsed_seconds == 0.0
        saga.run()
        assert saga.elapsed_seconds > 0.0

    def test_run_name_before_start(self):
        saga = SagaOrchestrator("s1", [SagaStep("a", action=lambda: None)])
        assert saga.state == SagaState.PENDING


# ===========================================================================
# SagaOrchestrator — compensation
# ===========================================================================


class TestSagaCompensation:
    def test_step_3_fails_compensates_steps_2_and_1(self):
        c = Counter()
        fail = FailingAction(fail_on_call=1)
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("s1", action=lambda: c.action("s1"), compensation=lambda: c.compensate("s1")),
                SagaStep("s2", action=lambda: c.action("s2"), compensation=lambda: c.compensate("s2")),
                SagaStep("s3", action=fail, compensation=lambda: c.compensate("s3")),
            ],
        )
        results = saga.run()
        assert saga.state == SagaState.COMPENSATED
        assert c.actions == ["s1", "s2"]
        assert c.compensations == ["s2", "s1"]
        assert results[0].success is True
        assert results[1].success is True
        assert results[2].success is False
        assert results[2].error is not None

    def test_step_1_fails_no_compensation_needed(self):
        c = Counter()
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("s1", action=FailingAction(fail_on_call=1), compensation=lambda: c.compensate("s1")),
                SagaStep("s2", action=lambda: c.action("s2"), compensation=lambda: c.compensate("s2")),
            ],
        )
        saga.run()
        assert saga.state == SagaState.COMPENSATED
        assert c.actions == []
        assert c.compensations == []

    def test_compensation_skipped_when_none(self):
        c = Counter()
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("s1", action=lambda: c.action("s1")),
                SagaStep("s2", action=FailingAction(fail_on_call=1), compensation=lambda: c.compensate("s2")),
            ],
        )
        results = saga.run()
        assert results[0].success is True
        assert results[1].success is False
        assert c.compensations == []

    def test_compensation_runs_in_reverse_order(self):
        order: list[str] = []
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("first", action=lambda: order.append("do-1"), compensation=lambda: order.append("undo-1")),
                SagaStep("second", action=lambda: order.append("do-2"), compensation=lambda: order.append("undo-2")),
                SagaStep("third", action=lambda: order.append("do-3"), compensation=lambda: order.append("undo-3")),
                SagaStep("fail", action=FailingAction(fail_on_call=1)),
            ],
        )
        saga.run()
        assert order == ["do-1", "do-2", "do-3", "undo-3", "undo-2", "undo-1"]

    def test_compensation_failure_does_not_block_other_compensations(self):
        order: list[str] = []

        def _safe_compensate(name: str):
            def _fn():
                order.append(f"undo-{name}")

            return _fn

        def _failing_compensation():
            order.append("undo-fail")
            raise RuntimeError("compensation failed")

        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("s1", action=lambda: order.append("do-1"), compensation=_safe_compensate("1")),
                SagaStep("s2", action=lambda: order.append("do-2"), compensation=_failing_compensation),
                SagaStep("s3", action=lambda: order.append("do-3"), compensation=_safe_compensate("3")),
                SagaStep("s4", action=FailingAction(fail_on_call=1)),
            ],
        )
        saga.run()
        assert saga.state == SagaState.COMPENSATED
        assert "undo-3" in order
        assert "undo-fail" in order
        assert "undo-1" in order


# ===========================================================================
# SagaOrchestrator — retry
# ===========================================================================


class TestSagaRetry:
    def test_retry_succeeds_on_second_attempt(self):
        class FailOnce:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient failure")

        fail = FailOnce()
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("s1", action=fail, retry_count=1, retry_delay_seconds=0.01),
            ],
        )
        results = saga.run()
        assert saga.state == SagaState.COMPLETED
        assert results[0].success is True
        assert fail.calls == 2

    def test_retry_exhausted_fails_with_error(self):
        fail = FailingAction(fail_on_call=1)
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("s1", action=fail, retry_count=1, retry_delay_seconds=0.01),
            ],
        )
        results = saga.run()
        assert results[0].success is False
        assert fail.calls == 2

    def test_max_retries_from_config_limits_retry_count(self):
        fail = FailingAction(fail_on_call=1)
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("s1", action=fail, retry_count=10),
            ],
            config=SagaConfig(max_retries=1),
        )
        results = saga.run()
        assert results[0].success is False
        assert fail.calls == 2


# ===========================================================================
# SagaOrchestrator — timeout
# ===========================================================================


class TestSagaTimeout:
    def test_step_exceeding_timeout_is_killed(self):
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("slow", action=lambda: time.sleep(0.5), timeout_seconds=0.05),
            ],
        )
        results = saga.run()
        assert saga.state == SagaState.TIMED_OUT
        assert results[0].success is False
        assert "timed out" in (results[0].error or "").lower()

    def test_step_within_timeout_succeeds(self):
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("fast", action=lambda: time.sleep(0.01), timeout_seconds=5.0),
            ],
        )
        results = saga.run()
        assert results[0].success is True

    def test_timeout_compensates_completed_steps_and_persists_terminal_state(self):
        store = InMemoryStore()
        compensations: list[str] = []
        saga = SagaOrchestrator(
            "timeout-with-prior-work",
            [
                SagaStep(
                    "prepared",
                    action=lambda: None,
                    compensation=lambda: compensations.append("prepared"),
                ),
                SagaStep(
                    "slow",
                    action=lambda: time.sleep(0.2),
                    timeout_seconds=0.01,
                ),
            ],
            store=store,
        )

        results = saga.run()

        assert saga.state == SagaState.TIMED_OUT
        assert compensations == ["prepared"]
        assert results[-1].step_name == "slow"
        assert results[-1].success is False
        assert store.load("timeout-with-prior-work")["state"] == "timed_out"


# ===========================================================================
# SagaOrchestrator — persistence
# ===========================================================================


class TestSagaPersistence:
    def test_store_called_after_each_step(self):
        store = InMemoryStore()
        saga = SagaOrchestrator(
            "abc",
            [
                SagaStep("a", action=lambda: None),
                SagaStep("b", action=lambda: None),
            ],
            store=store,
        )
        saga.run()
        saved = store.load("abc")
        assert saved is not None
        assert saved["state"] == "completed"
        assert len(saved["results"]) == 2

    def test_restore_skips_completed_saga(self):
        store = InMemoryStore()
        store.save("abc", {"state": "completed", "results": [], "completed_at": 1.0})
        saga = SagaOrchestrator(
            "abc",
            [
                SagaStep("a", action=lambda: None),
            ],
            store=store,
        )
        assert saga.state == SagaState.PENDING
        saga.run()
        assert saga.state == SagaState.COMPLETED

    def test_restore_skips_compensated_saga(self):
        store = InMemoryStore()
        store.save("abc", {"state": "compensated", "results": [], "completed_at": 1.0})
        saga = SagaOrchestrator(
            "abc",
            [
                SagaStep("a", action=lambda: None),
            ],
            store=store,
        )
        assert saga.state == SagaState.PENDING
        saga.run()
        assert saga.state == SagaState.COMPENSATED

    def test_no_store_no_persistence_calls(self):
        saga = SagaOrchestrator("s1", [SagaStep("a", action=lambda: None)])
        saga.run()
        assert saga.state == SagaState.COMPLETED

    def test_persist_on_each_step_disabled_still_persists_terminal_state(self):
        store = InMemoryStore()
        saga = SagaOrchestrator(
            "abc",
            [
                SagaStep("a", action=lambda: None),
            ],
            store=store,
            config=SagaConfig(persist_on_each_step=False),
        )
        saga.run()
        saved = store.load("abc")
        assert saved is not None
        assert saved["state"] == "completed"

    def test_restore_rebuilds_results_for_failed_saga(self):
        store = InMemoryStore()
        store.save(
            "abc",
            {
                "state": "running",
                "results": [
                    {"step_index": 0, "step_name": "a", "success": True, "error": None, "duration_ms": 1.0},
                ],
            },
        )
        saga = SagaOrchestrator(
            "abc",
            [
                SagaStep("a", action=lambda: None),
                SagaStep("b", action=lambda: None),
            ],
            store=store,
        )
        saga.run()
        results = saga.results
        assert len(results) == 2

    @pytest.mark.parametrize("stored_state", ["completed", "compensated"])
    def test_restore_terminal_saga_does_not_repeat_side_effects(self, stored_state: str):
        store = InMemoryStore()
        store.save(
            "abc",
            {
                "state": stored_state,
                "results": [
                    {
                        "step_index": 0,
                        "step_name": "a",
                        "success": True,
                        "error": None,
                        "duration_ms": 1.0,
                    }
                ],
                "completed_at": 1.0,
            },
        )
        calls: list[str] = []
        saga = SagaOrchestrator(
            "abc",
            [SagaStep("a", action=lambda: calls.append("repeated"))],
            store=store,
        )

        results = saga.run()

        assert calls == []
        assert saga.state.value == stored_state
        assert [result.step_name for result in results] == ["a"]

    def test_failed_step_persists_compensated_terminal_state(self):
        store = InMemoryStore()
        saga = SagaOrchestrator(
            "failed",
            [
                SagaStep("done", action=lambda: None, compensation=lambda: None),
                SagaStep("broken", action=FailingAction()),
            ],
            store=store,
        )

        saga.run()

        saved = store.load("failed")
        assert saga.state == SagaState.COMPENSATED
        assert saved is not None
        assert saved["state"] == "compensated"


# ===========================================================================
# SagaOrchestrator — error states
# ===========================================================================


class TestSagaErrors:
    def test_double_run_raises_error(self):
        saga = SagaOrchestrator("s1", [SagaStep("a", action=lambda: None)])
        saga.run()
        with pytest.raises(SagaError, match="already started"):
            saga.run()

    def test_run_after_timeout_is_idempotent(self):
        saga = SagaOrchestrator(
            "s1",
            [
                SagaStep("slow", action=lambda: time.sleep(0.3), timeout_seconds=0.02),
            ],
        )
        saga.run()
        with pytest.raises(SagaError, match="already started"):
            saga.run()


# ===========================================================================
# SagaBuilder
# ===========================================================================


class TestSagaBuilder:
    def test_builder_produces_saga(self):
        c = Counter()
        saga = (
            SagaBuilder()
            .step("a", action=lambda: c.action("a"), compensation=lambda: c.compensate("a"))
            .step("b", action=lambda: c.action("b"))
            .build("s1")
        )
        assert saga.step_count == 2
        results = saga.run()
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_builder_with_max_retries(self):
        fail = FailingAction(fail_on_call=1)
        saga = SagaBuilder().step("a", action=fail, retry_count=5).with_max_retries(1).build("s1")
        results = saga.run()
        assert results[0].success is False
        assert fail.calls == 2

    def test_builder_with_retry_backoff(self):
        saga = SagaBuilder().with_retry_backoff(3.0).build("s1")
        assert saga._config.retry_backoff_multiplier == 3.0

    def test_builder_with_persistence(self):
        store = InMemoryStore()
        saga = SagaBuilder().step("a", action=lambda: None).with_persistence(store).build("s1")
        saga.run()
        assert store.load("s1") is not None

    def test_builder_without_step_persistence_still_saves_terminal_state(self):
        store = InMemoryStore()
        saga = (
            SagaBuilder().step("a", action=lambda: None).with_persistence(store).without_step_persistence().build("s1")
        )
        saga.run()
        assert store.load("s1")["state"] == "completed"

    def test_builder_with_compensation_timeout(self):
        saga = SagaBuilder().with_compensation_timeout(5.0).build("s1")
        assert saga._config.compensation_timeout_seconds == 5.0

    def test_builder_method_chaining(self):
        saga = (
            SagaBuilder()
            .step("s1", action=lambda: None)
            .step("s2", action=lambda: None)
            .step("s3", action=lambda: None)
            .with_max_retries(5)
            .with_retry_backoff(1.5)
            .with_compensation_timeout(3.0)
            .build("chain")
        )
        assert saga.step_count == 3
        results = saga.run()
        assert len(results) == 3
        assert saga.state == SagaState.COMPLETED


# ===========================================================================
# SagaState enum
# ===========================================================================


class TestSagaState:
    def test_all_states_have_string_values(self):
        for state in SagaState:
            assert isinstance(state.value, str)

    def test_pending_is_default(self):
        assert SagaState.PENDING.value == "pending"


# ===========================================================================
# Saga exceptions
# ===========================================================================


class TestSagaExceptions:
    def test_saga_error_is_exception(self):
        with pytest.raises(SagaError):
            raise SagaError("test")

    def test_saga_step_failure_carries_context(self):
        orig = RuntimeError("boom")
        exc = SagaStepFailure(2, "step2", orig)
        assert exc.step_index == 2
        assert exc.step_name == "step2"
        assert exc.original is orig
        assert "step2" in str(exc)

    def test_saga_compensation_failure_carries_context(self):
        orig = RuntimeError("boom")
        exc = SagaCompensationFailure(1, "undo", orig)
        assert exc.step_index == 1
        assert exc.step_name == "undo"
        assert exc.original is orig

    def test_saga_timeout_carries_context(self):
        exc = SagaTimeout(3, "slow-step", 5.0)
        assert exc.step_index == 3
        assert exc.step_name == "slow-step"
        assert exc.timeout == 5.0
        assert "5.0s" in str(exc)


# ===========================================================================
# StepResult dataclass
# ===========================================================================


class TestStepResult:
    def test_default_fields(self):
        r = StepResult(step_index=0, step_name="a", success=True)
        assert r.error is None
        assert r.duration_ms == 0.0

    def test_with_error(self):
        r = StepResult(step_index=1, step_name="b", success=False, error="fail")
        assert r.error == "fail"
        assert not r.success
