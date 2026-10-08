"""Runtime acceptance tests for bounded shadow estimation feedback."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

import general_ludd.execution.engine as engine_module
from general_ludd.daemon_components import lifecycle
from general_ludd.execution.engine import ExecutionEngine
from general_ludd.review.estimation_tracker import (
    EstimationTracker,
    TaskActual,
    TaskEstimate,
)
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn


class _Gateway:
    def __init__(self, cost: object = 0.10, *, raises: bool = False) -> None:
        self.cost = cost
        self.raises = raises
        self.calls: list[tuple[str, str]] = []

    def get_profile(self, profile_id: str) -> SimpleNamespace:
        assert profile_id == "default"
        return SimpleNamespace(
            model_name="provider-model",
            max_input_tokens=1000,
            max_output_tokens=0,
        )

    def call_model(
        self,
        profile_id: str,
        *,
        messages: list[dict[str, str]],
        work_type: str,
    ) -> SimpleNamespace:
        del messages
        self.calls.append((profile_id, work_type))
        if self.raises:
            raise RuntimeError("provider unavailable")
        response = SimpleNamespace(
            content="```python\nFILE: generated.py\nprint('ok')\n```",
        )
        if self.cost is not _MISSING:
            response.cost_estimate = self.cost
        return response


class _CostSource:
    def token_cost_usd(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        assert (model, input_tokens, output_tokens) == ("provider-model", 1000, 0)
        return 1.00

    def check_all_limits(self, *, estimated_cost: float) -> dict[str, object]:
        assert estimated_cost == 1.00
        return {"allowed": True}


_MISSING = object()


def _job() -> JobSpec:
    return JobSpec(
        job_id="JOB-EST-1",
        todo_id="TODO-EST-1",
        playbook="code",
        queue="core",
        work_type="code",
        prompt_text="Create generated.py",
    )


def _engine(
    tmp_path: object,
    gateway: _Gateway,
    tracker: EstimationTracker,
) -> ExecutionEngine:
    return ExecutionEngine(
        model_gateway=gateway,
        workspace_path=str(tmp_path),
        budget_guard=_CostSource(),
        estimation_tracker=tracker,
    )


def _task_return() -> TaskReturn:
    return TaskReturn(
        return_id="RET-EST-1",
        todo_id="TODO-EST-1",
        job_id="JOB-EST-1",
        playbook="code",
        queue="core",
        exit_code=0,
    )


@pytest.fixture(autouse=True)
def _isolate_engine_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "_is_git_repo", lambda _path: False)
    monkeypatch.setattr(engine_module, "_run_tests", lambda _path: (0, "focused tests passed"))


@pytest.mark.asyncio
async def test_real_engine_cost_variance_is_consumed_as_suspect(
    tmp_path: object,
) -> None:
    tracker = EstimationTracker()
    gateway = _Gateway(cost=0.10)
    with patch.object(
        engine_module,
        "perf_counter_ns",
        side_effect=[10, 60_000_000_010],
    ):
        result = await _engine(tmp_path, gateway, tracker).execute_async(_job())

    assert result.exit_code == 0
    assert gateway.calls == [("default", "code")]
    assert tracker.pending_count == 0
    variance = tracker.get_variance("TODO-EST-1")
    assert variance is not None
    assert variance.cost_variance == pytest.approx(-0.90)
    assert variance.is_suspect is True
    assert tracker._actuals["TODO-EST-1"].actual_time_minutes == pytest.approx(1.0)
    calibration = tracker.get_calibration("code")
    assert calibration is not None
    assert calibration.sample_count == 0
    assert tracker.get_corrected_estimate("code", 1.0, 1.0, 1) == (1.0, 1.0, 1)

    registry = MagicMock()
    registry.render.return_value = "review prompt"
    reviewer = ReturnReviewer(
        gateway=MagicMock(),
        prompt_registry=registry,
        estimation_tracker=tracker,
    )
    model_decision = json.dumps(
        {
            "return_id": result.return_id,
            "matched_todo_id": result.todo_id,
            "decision": "complete",
            "confidence": 0.95,
        }
    )
    with (
        patch.object(reviewer, "_call_model", return_value=(model_decision, None)),
        patch.object(reviewer, "_audit_evidence", return_value=[]),
    ):
        decision = reviewer.review_return(result, [], result.artifacts)

    assert decision.estimation_suspect is True
    assert any(note.startswith("ESTIMATION_SUSPECT:") for note in decision.audit_notes)


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_cost", [_MISSING, None, float("nan"), float("inf")])
async def test_missing_or_nonfinite_provider_cost_stays_unobserved(
    tmp_path: object,
    actual_cost: object,
) -> None:
    tracker = EstimationTracker()

    result = await _engine(tmp_path, _Gateway(cost=actual_cost), tracker).execute_async(_job())

    assert result.exit_code == 0
    assert tracker.pending_count == 0
    assert tracker.completed_count == 0
    assert tracker.get_variance("TODO-EST-1") is None


@pytest.mark.asyncio
async def test_provider_failure_discards_pending_observation(tmp_path: object) -> None:
    tracker = EstimationTracker()

    result = await _engine(
        tmp_path,
        _Gateway(raises=True),
        tracker,
    ).execute_async(_job())

    assert result.exit_code == 1
    assert tracker.pending_count == 0
    assert tracker.completed_count == 0


@pytest.mark.asyncio
async def test_environment_rollback_disables_feedback(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_ESTIMATION_FEEDBACK", "0")
    tracker = EstimationTracker()

    result = await _engine(tmp_path, _Gateway(cost=0.10), tracker).execute_async(_job())

    assert result.exit_code == 0
    assert tracker.pending_count == 0
    assert tracker.completed_count == 0


def test_tracker_caps_pending_and_completed_observations() -> None:
    tracker = EstimationTracker(max_history=3)
    for index in range(5):
        tracker.record_estimate(
            TaskEstimate(
                todo_id=f"pending-{index}",
                work_type="code",
                estimated_cost_usd=1.0,
                estimated_time_minutes=0.0,
                estimated_loc=0,
            )
        )

    assert tracker.pending_count == 3
    assert "pending-0" not in tracker._estimates
    assert "pending-1" not in tracker._estimates

    for index in range(5):
        todo_id = f"complete-{index}"
        tracker.record_estimate(
            TaskEstimate(
                todo_id=todo_id,
                work_type="code",
                estimated_cost_usd=1.0,
                estimated_time_minutes=0.0,
                estimated_loc=0,
            )
        )
        tracker.record_completion(
            TaskActual(
                todo_id=todo_id,
                actual_cost_usd=0.1,
                actual_time_minutes=0.01,
                actual_loc=0,
                exit_code=0,
            ),
            cost_only=True,
        )
        tracker.discard_estimate(todo_id)

    assert tracker.pending_count == 2
    assert tracker.completed_count == 3
    assert tracker.get_variance("complete-0") is None
    assert tracker.get_variance("complete-4") is not None


def test_default_tracker_bound_is_one_thousand() -> None:
    tracker = EstimationTracker()

    assert tracker.max_history == 1000


def test_lifecycle_shares_one_flagged_tracker_with_runtime_consumers() -> None:
    source = inspect.getsource(cast(Any, lifecycle.lifespan).__wrapped__)

    assert 'os.environ.get("GLUDD_ESTIMATION_FEEDBACK", "1")' in source
    assert source.count("estimation_tracker=estimation_tracker") == 2


@pytest.mark.parametrize("projection", [True, "1.0", 0.0, float("nan")])
def test_invalid_projection_does_not_open_observation(
    tmp_path: object,
    projection: object,
) -> None:
    tracker = EstimationTracker()
    runtime = _engine(tmp_path, _Gateway(), tracker)

    assert runtime._start_estimation_observation(_job(), projection) is None
    assert tracker.pending_count == 0


def test_observation_failures_are_contained_and_cleaned(tmp_path: object) -> None:
    tracker = MagicMock(spec=EstimationTracker)
    runtime = _engine(tmp_path, _Gateway(), tracker)
    tracker.record_estimate.side_effect = RuntimeError("start failed")

    assert runtime._start_estimation_observation(_job(), 1.0) is None

    tracker.record_estimate.side_effect = None
    tracker.record_completion.side_effect = RuntimeError("finish failed")
    runtime._finish_estimation_observation(("TODO-EST-1", 1), 0.1)
    tracker.discard_estimate.assert_called_once_with("TODO-EST-1")

    tracker.reset_mock()
    tracker.discard_estimate.side_effect = RuntimeError("cleanup failed")
    runtime._finish_estimation_observation(("TODO-EST-1", 1), "not-a-number")
    tracker.discard_estimate.assert_called_once_with("TODO-EST-1")


def test_reviewer_observes_only_finite_attached_cost() -> None:
    tracker = EstimationTracker()
    tracker.record_estimate(
        TaskEstimate(
            todo_id="TODO-EST-1",
            work_type="code",
            estimated_cost_usd=1.0,
            estimated_time_minutes=2.0,
            estimated_loc=0,
        )
    )
    reviewer = ReturnReviewer(
        gateway=MagicMock(),
        prompt_registry=MagicMock(),
        estimation_tracker=tracker,
    )
    task_return = _task_return()
    object.__setattr__(task_return, "cost_estimate", 0.1)
    object.__setattr__(task_return, "duration_seconds", 120.0)

    variance = reviewer._estimation_variance(task_return)

    assert variance is not None
    assert variance.cost_variance == pytest.approx(-0.9)
    assert tracker._actuals["TODO-EST-1"].actual_time_minutes == pytest.approx(2.0)


@pytest.mark.parametrize("actual_cost", [None, True, "bad", float("nan"), -0.1])
def test_reviewer_leaves_invalid_attached_cost_unobserved(actual_cost: object) -> None:
    tracker = EstimationTracker()
    reviewer = ReturnReviewer(
        gateway=MagicMock(),
        prompt_registry=MagicMock(),
        estimation_tracker=tracker,
    )
    task_return = _task_return()
    object.__setattr__(task_return, "cost_estimate", actual_cost)

    assert reviewer._estimation_variance(task_return) is None
    assert tracker.completed_count == 0


def test_reviewer_contains_invalid_attached_duration() -> None:
    tracker = EstimationTracker()
    tracker.record_estimate(
        TaskEstimate(
            todo_id="TODO-EST-1",
            work_type="code",
            estimated_cost_usd=1.0,
            estimated_time_minutes=0.0,
            estimated_loc=0,
        )
    )
    reviewer = ReturnReviewer(
        gateway=MagicMock(),
        prompt_registry=MagicMock(),
        estimation_tracker=tracker,
    )
    task_return = _task_return()
    object.__setattr__(task_return, "cost_estimate", 1.0)
    object.__setattr__(task_return, "duration_seconds", "invalid")

    variance = reviewer._estimation_variance(task_return)

    assert variance is not None
    assert tracker._actuals["TODO-EST-1"].actual_time_minutes == 0.0
