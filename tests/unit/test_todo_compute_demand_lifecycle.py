"""Todo-driven execution-environment demand and phase-order contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop


class _ExecutionEnvironmentRunner:
    def __init__(self, results: list[dict[str, object]] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._results = list(results or [])

    def reconcile_execution_environment(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if self._results:
            return self._results.pop(0)
        return {"status": "successful", "rc": 0, "events": []}


def _loop(
    tmp_path: Path,
    *,
    by_status: dict[str, int],
    runner: object,
) -> tuple[EventLoop, Any]:
    repository = SimpleNamespace(
        status_summary=AsyncMock(return_value={"by_status": by_status}),
        claim_runnable=AsyncMock(return_value=[]),
        recover_queued_legacy_self_improve=AsyncMock(return_value=[]),
        count_active=AsyncMock(return_value=0),
    )
    loop = EventLoop(
        config={
            "repo_root": str(tmp_path),
            "execution_environment": {
                "machine_cpus": 3,
                "machine_memory_mb": 6144,
                "machine_disk_gb": 16,
            },
        },
        todo_repo=repository,
        runner=runner,
    )
    return loop, repository


def test_todo_producers_run_before_common_ranking_and_compute_reconciliation() -> None:
    assert PHASE_ORDER.index("load_config_snapshot") < PHASE_ORDER.index("run_scheduler")
    assert PHASE_ORDER.index("run_scheduler") < PHASE_ORDER.index("self_improve")
    assert PHASE_ORDER.index("self_improve") < PHASE_ORDER.index("poll_issue_sources")
    assert PHASE_ORDER.index("poll_issue_sources") < PHASE_ORDER.index(
        "reconcile_compute_demand"
    )
    assert PHASE_ORDER.index("reconcile_compute_demand") < PHASE_ORDER.index(
        "dispatch_return_review_jobs"
    )
    assert PHASE_ORDER.index("reconcile_compute_demand") < PHASE_ORDER.index(
        "claim_runnable_todos"
    )
    assert PHASE_ORDER.index("claim_runnable_todos") < PHASE_ORDER.index(
        "dispatch_execute_jobs"
    )


@pytest.mark.asyncio
async def test_runnable_todos_provision_once_then_idle_tears_down_exact_scope(
    tmp_path: Path,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, repository = _loop(
        tmp_path,
        by_status={"queued": 2, "approval_required": 4},
        runner=runner,
    )

    await loop._phase_reconcile_compute_demand()
    await loop._phase_reconcile_compute_demand()
    repository.status_summary.return_value = {
        "by_status": {"complete": 2, "approval_required": 4}
    }
    await loop._phase_reconcile_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present", "absent"]
    assert runner.calls[0] == {
        "state": "present",
        "project_root": tmp_path,
        "constraints": {
            "machine_cpus": 3,
            "machine_memory_mb": 6144,
            "machine_disk_gb": 16,
        },
    }
    assert runner.calls[1]["project_root"] == tmp_path
    assert loop._tick_state["compute_demand"]["runnable_todos"] == 0
    assert loop._tick_state["compute_demand"]["execution_environment"] == "absent"


@pytest.mark.asyncio
async def test_approval_waiting_or_empty_queue_never_provisions_compute(
    tmp_path: Path,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, _repository = _loop(
        tmp_path,
        by_status={"approval_required": 3, "scheduled": 2, "complete": 7},
        runner=runner,
    )

    await loop._phase_reconcile_compute_demand()

    assert [call["state"] for call in runner.calls] == ["absent"]
    assert loop._tick_state["compute_demand"]["runnable_todos"] == 0
    assert loop._tick_state["compute_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["active", "awaiting_result", "reviewing_return", "needs_more_work"],
)
async def test_in_flight_todo_states_retain_compute_until_work_is_terminal(
    tmp_path: Path,
    status: str,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, _repository = _loop(
        tmp_path,
        by_status={status: 1},
        runner=runner,
    )

    await loop._phase_reconcile_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]
    assert loop._tick_state["compute_ready"] is True


@pytest.mark.asyncio
async def test_bootstrap_failure_blocks_claim_without_mutating_todo_state(
    tmp_path: Path,
) -> None:
    runner = _ExecutionEnvironmentRunner(
        [{"status": "failed", "rc": 1, "error": "bounded failure", "events": []}]
    )
    loop, repository = _loop(
        tmp_path,
        by_status={"queued": 1},
        runner=runner,
    )

    await loop._phase_reconcile_compute_demand()
    await loop._phase_claim_runnable_todos()

    assert loop._tick_state["compute_ready"] is False
    assert loop._tick_state["claimed_todos"] == []
    repository.claim_runnable.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_queue_state_preserves_existing_compute_and_fails_closed(
    tmp_path: Path,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, repository = _loop(tmp_path, by_status={}, runner=runner)
    repository.status_summary.side_effect = RuntimeError("database unavailable")

    await loop._phase_reconcile_compute_demand()

    assert runner.calls == []
    assert loop._tick_state["compute_ready"] is False
    assert loop._tick_state["compute_demand"]["state"] == "unknown"
