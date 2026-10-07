"""Todo-driven execution-environment demand and phase-order contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop


class _ExecutionEnvironmentRunner:
    def __init__(self, results: list[object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._results = list(results or [])

    def reconcile_execution_environment(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self._results:
            return self._results.pop(0)
        return {"status": "successful", "rc": 0, "events": []}


class _RaisingExecutionEnvironmentRunner(_ExecutionEnvironmentRunner):
    def reconcile_execution_environment(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        raise RuntimeError("provider unavailable")


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


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
        todo_repo=cast(TodoRepository, repository),
        runner=runner,
    )
    return loop, repository


def test_todo_producers_run_before_common_ranking_and_compute_reconciliation() -> None:
    assert PHASE_ORDER.index("load_config_snapshot") < PHASE_ORDER.index("run_scheduler")
    assert PHASE_ORDER.index("run_scheduler") < PHASE_ORDER.index("self_improve")
    assert PHASE_ORDER.index("self_improve") < PHASE_ORDER.index("poll_issue_sources")
    assert PHASE_ORDER.index("poll_issue_sources") < PHASE_ORDER.index(
        "claim_runnable_todos"
    )
    assert PHASE_ORDER.index("claim_runnable_todos") < PHASE_ORDER.index(
        "reconcile_compute_demand"
    )
    assert PHASE_ORDER.index("reconcile_compute_demand") < PHASE_ORDER.index(
        "dispatch_execute_jobs"
    )
    assert PHASE_ORDER.index("reconcile_completed_decisions") < PHASE_ORDER.index(
        "release_compute_demand"
    )


@pytest.mark.asyncio
async def test_runnable_todos_provision_once_then_idle_tears_down_exact_scope(
    tmp_path: Path,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, repository = _loop(
        tmp_path,
        by_status={"active": 2, "approval_required": 4},
        runner=runner,
    )
    loop.config["execution_environment"]["enabled"] = True
    loop._tick_state["claimed_todos"] = [
        SimpleNamespace(todo_id="TODO-1"),
        SimpleNamespace(todo_id="TODO-2"),
    ]

    await loop._phase_reconcile_compute_demand()
    await loop._phase_reconcile_compute_demand()
    loop._tick_state["claimed_todos"] = []
    repository.status_summary.return_value = {
        "by_status": {"complete": 2, "approval_required": 4}
    }
    await loop._phase_release_compute_demand()
    await loop._phase_release_compute_demand()

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
    loop.config.pop("repo_root")

    await loop._phase_reconcile_compute_demand()

    assert runner.calls == []
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
    loop, repository = _loop(
        tmp_path,
        by_status={status: 1},
        runner=runner,
    )
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]

    await loop._phase_reconcile_compute_demand()
    loop._tick_state["claimed_todos"] = []
    await loop._phase_release_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]
    assert loop._tick_state["compute_ready"] is True
    repository.status_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_failure_happens_after_claim_and_blocks_dispatch(
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

    claimed = SimpleNamespace(todo_id="TODO-1", version=2)
    repository.claim_runnable.return_value = [claimed]
    await loop._phase_claim_runnable_todos()
    await loop._phase_reconcile_compute_demand()
    await loop._phase_dispatch_execute_jobs()

    assert loop._tick_state["compute_ready"] is False
    assert loop._tick_state["claimed_todos"] == [claimed]
    repository.claim_runnable.assert_awaited_once()
    assert loop._tick_metrics["todos_dispatched"] == 0


@pytest.mark.asyncio
async def test_unknown_queue_state_preserves_existing_compute_and_fails_closed(
    tmp_path: Path,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, repository = _loop(tmp_path, by_status={}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    await loop._phase_reconcile_compute_demand()
    loop._tick_state["claimed_todos"] = []
    repository.status_summary.side_effect = RuntimeError("database unavailable")

    await loop._phase_release_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]
    assert loop._tick_state["compute_ready"] is True
    assert loop._tick_state["compute_demand"]["state"] == "unknown"


@pytest.mark.asyncio
async def test_two_workers_only_the_durable_claim_winner_provisions(
    tmp_path: Path,
) -> None:
    """A worker that lost the database claim cannot create or remove compute."""
    runner = _ExecutionEnvironmentRunner()
    winner, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loser, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    winner._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    loser._tick_state["claimed_todos"] = []

    await winner._phase_reconcile_compute_demand()
    await loser._phase_reconcile_compute_demand()
    await loser._phase_release_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]


@pytest.mark.asyncio
async def test_open_claim_transaction_cannot_provision_compute(
    tmp_path: Path,
) -> None:
    """An injected live session is not durable authority for provider effects."""
    runner = _ExecutionEnvironmentRunner()
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    loop._active_session = AsyncMock()

    await loop._phase_reconcile_compute_demand()

    assert runner.calls == []
    assert loop._tick_state["compute_ready"] is False
    assert loop._tick_state["compute_demand"] == {
        "state": "claim_transaction_open",
        "runnable_todos": 1,
        "execution_environment": "unchanged",
    }
    assert loop._tick_metrics["compute_claim_fence_rejections"] == 1


@pytest.mark.asyncio
async def test_live_session_without_provider_allows_external_dispatch(
    tmp_path: Path,
) -> None:
    """A committed compatibility claim needs no provider lifecycle call."""
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=None)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    loop._active_session = AsyncMock()

    await loop._phase_reconcile_compute_demand()

    assert loop._tick_state["compute_ready"] is True
    assert loop._tick_state["compute_demand"] == {
        "state": "externally_managed",
        "runnable_todos": 1,
        "execution_environment": "external",
    }
    assert "compute_claim_fence_rejections" not in loop._tick_metrics


@pytest.mark.asyncio
async def test_live_session_with_playbook_only_runner_allows_external_dispatch(
    tmp_path: Path,
) -> None:
    """A runner without a provider lifecycle cannot allocate compute."""
    runner = SimpleNamespace(run_playbook=lambda **_kwargs: None)
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    loop._active_session = AsyncMock()

    await loop._phase_reconcile_compute_demand()

    assert loop._tick_state["compute_ready"] is True
    assert loop._tick_state["compute_demand"] == {
        "state": "externally_managed",
        "runnable_todos": 1,
        "execution_environment": "external",
    }
    assert "compute_claim_fence_rejections" not in loop._tick_metrics


@pytest.mark.asyncio
async def test_restart_preserves_foreign_claim_compute_without_replaying_lifecycle(
    tmp_path: Path,
) -> None:
    """A fresh process neither duplicates nor tears down a predecessor's claim."""
    runner = _ExecutionEnvironmentRunner()
    owner, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    owner._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    await owner._phase_reconcile_compute_demand()

    restarted, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    restarted._tick_state["claimed_todos"] = []
    await restarted._phase_reconcile_compute_demand()
    await restarted._phase_release_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_value", "expected_ready", "expected_state"),
    [
        ([], False, "unknown"),
        ({"enabled": False}, True, "externally_managed"),
    ],
)
async def test_claim_winner_never_provisions_from_invalid_or_disabled_config(
    tmp_path: Path,
    config_value: object,
    expected_ready: bool,
    expected_state: str,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop.config["execution_environment"] = config_value
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]

    await loop._phase_reconcile_compute_demand()

    assert runner.calls == []
    assert loop._tick_state["compute_ready"] is expected_ready
    assert loop._tick_state["compute_demand"]["state"] == expected_state


@pytest.mark.asyncio
async def test_claim_winner_without_exact_project_root_fails_before_provisioning(
    tmp_path: Path,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop.config.pop("repo_root")
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]

    await loop._phase_reconcile_compute_demand()

    assert runner.calls == []
    assert loop._tick_state["compute_ready"] is False
    assert loop._tick_state["compute_demand"]["state"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summary",
    [None, {"by_status": []}],
)
async def test_malformed_release_demand_preserves_owned_compute(
    tmp_path: Path,
    summary: object,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, repository = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    await loop._phase_reconcile_compute_demand()
    repository.status_summary.return_value = summary

    await loop._phase_release_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]
    assert loop._tick_state["compute_demand"] == {
        "state": "unknown",
        "execution_environment": "preserved",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_failed_exact_release_remains_owned_for_retry(
    tmp_path: Path,
    raises: bool,
) -> None:
    runner: _ExecutionEnvironmentRunner
    if raises:
        provision_runner = _ExecutionEnvironmentRunner()
        loop, repository = _loop(
            tmp_path,
            by_status={"active": 1},
            runner=provision_runner,
        )
        loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
        await loop._phase_reconcile_compute_demand()
        runner = _RaisingExecutionEnvironmentRunner()
        loop._runner = runner
    else:
        runner = _ExecutionEnvironmentRunner(
            [
                {"status": "successful", "rc": 0},
                {"status": "failed", "rc": 1},
            ]
        )
        loop, repository = _loop(
            tmp_path,
            by_status={"active": 1},
            runner=runner,
        )
        loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
        await loop._phase_reconcile_compute_demand()
    repository.status_summary.return_value = {"by_status": {"complete": 1}}

    await loop._phase_release_compute_demand()

    assert runner.calls[-1]["state"] == "absent"
    assert loop._tick_state["compute_ready"] is True
    assert loop._tick_state["compute_demand"]["state"] == "release_failed"


@pytest.mark.asyncio
async def test_provision_provider_exception_fails_closed_after_claim(
    tmp_path: Path,
) -> None:
    runner = _RaisingExecutionEnvironmentRunner()
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]

    await loop._phase_reconcile_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]
    assert loop._tick_state["compute_ready"] is False
    assert loop._tick_state["compute_demand"]["execution_environment"] == "failed"


@pytest.mark.asyncio
async def test_empty_claim_retains_compute_owned_by_same_loop(tmp_path: Path) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    await loop._phase_reconcile_compute_demand()
    loop._tick_state["claimed_todos"] = []

    await loop._phase_reconcile_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]
    assert loop._tick_state["compute_ready"] is True
    assert loop._tick_state["compute_demand"] == {
        "state": "retained",
        "runnable_todos": 0,
        "execution_environment": "present",
    }


@pytest.mark.asyncio
async def test_non_sequence_claim_state_cannot_trigger_provisioning(tmp_path: Path) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = {"TODO-1": object()}

    await loop._phase_reconcile_compute_demand()

    assert runner.calls == []
    assert loop._tick_state["compute_demand"]["state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_result",
    [[], {"status": "successful", "rc": 1}],
)
async def test_non_successful_provider_receipt_fails_closed(
    tmp_path: Path,
    provider_result: object,
) -> None:
    runner = _ExecutionEnvironmentRunner([provider_result])
    loop, _ = _loop(tmp_path, by_status={"active": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]

    await loop._phase_reconcile_compute_demand()

    assert loop._tick_state["compute_ready"] is False
    assert loop._tick_state["compute_demand"]["execution_environment"] == "failed"


@pytest.mark.asyncio
async def test_release_reads_durable_demand_in_its_own_short_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, repository = _loop(tmp_path, by_status={"complete": 1}, runner=runner)
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    await loop._phase_reconcile_compute_demand()

    def session_factory() -> _SessionContext:
        return _SessionContext()

    loop._session_factory = cast(
        async_sessionmaker[AsyncSession],
        session_factory,
    )
    monkeypatch.setattr(
        "general_ludd.event_loop.loop.TodoRepository",
        lambda _session: repository,
    )
    await loop._phase_release_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present", "absent"]
    repository.status_summary.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("release_condition", ["missing_root", "missing_repo", "invalid_config"])
async def test_release_preserves_owned_compute_when_scope_is_not_exact(
    tmp_path: Path,
    release_condition: str,
) -> None:
    runner = _ExecutionEnvironmentRunner()
    loop, repository = _loop(
        tmp_path,
        by_status={"complete": 1},
        runner=runner,
    )
    loop._tick_state["claimed_todos"] = [SimpleNamespace(todo_id="TODO-1")]
    await loop._phase_reconcile_compute_demand()

    if release_condition == "missing_root":
        loop.config.pop("repo_root")
    elif release_condition == "missing_repo":
        loop._todo_repo = None
    else:
        loop.config["execution_environment"] = []
    repository.status_summary.return_value = {"by_status": {"complete": 1}}

    await loop._phase_release_compute_demand()

    assert [call["state"] for call in runner.calls] == ["present"]
    assert loop._execution_environment_states[str(tmp_path)][0] == "present"


@pytest.mark.asyncio
async def test_non_mapping_claim_config_uses_bounded_default(tmp_path: Path) -> None:
    loop, _ = _loop(tmp_path, by_status={}, runner=_ExecutionEnvironmentRunner())
    loop.config["event_loop"] = []

    effective_limit, active_count, pid_outputs = await loop._effective_claim_limit()

    assert (effective_limit, active_count, pid_outputs) == (10, 0, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active_todos", [True, 0, "10"])
async def test_invalid_claim_limit_fails_before_provisioning(
    tmp_path: Path,
    max_active_todos: object,
) -> None:
    loop, _ = _loop(tmp_path, by_status={}, runner=_ExecutionEnvironmentRunner())
    loop.config["event_loop"] = {"max_active_todos": max_active_todos}

    with pytest.raises(ValueError, match="max_active_todos"):
        await loop._effective_claim_limit()
