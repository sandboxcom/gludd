"""Managed self-improvement runs in a Gludd-owned, killable process."""

from __future__ import annotations

import asyncio
import multiprocessing
import signal
import threading
from pathlib import Path

import pytest

from general_ludd.self_improve import managed_execution as managed_execution_module
from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.managed_execution import (
    ConfiguredManagedRunnerFactory,
    ManagedSelfImproveProcessExecutor,
    managed_execution_timeout_seconds,
)
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan, TaskSpec
from general_ludd.util import owned_process as owned_process_module
from general_ludd.util.owned_process import OwnedProcessTimeout


def _return_identity(
    _factory: object,
    repo_root: str,
    plan_json: str,
) -> tuple[str, str]:
    plan = ApprovedSelfImprovePlan.from_json(plan_json)
    return repo_root, plan.identity_digest


def _wedge_managed(
    _factory: object,
    _repo_root: str,
    _plan_json: str,
) -> None:
    threading.Event().wait(60.0)


class _IdentityRunner:
    def run(self, plan: ApprovedSelfImprovePlan) -> str:
        return plan.identity_digest


class _IdentityRunnerFactory:
    def __call__(self, _repo_root: Path) -> _IdentityRunner:
        return _IdentityRunner()


class _InvalidRunnerFactory:
    def __call__(self, _repo_root: Path) -> object:
        return object()


def _executor_children() -> list[multiprocessing.Process]:
    return [
        child
        for child in multiprocessing.active_children()
        if child.name.startswith("gludd-self-improve-")
    ]


@pytest.fixture
def approved_self_improve_plan(tmp_path: Path) -> ApprovedSelfImprovePlan:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-owned-process",
        todo_id="TODO-OWNED-PROCESS",
        project_id="project-owned-process",
        repo_root=repo_root,
        task=TaskSpec(
            task_id="S83.158",
            objective="Prove application-owned stalled task termination.",
            canonical_make_commands=(
                "make test-specific TESTFILE=tests/unit/test_managed_self_improve_process.py",
            ),
        ),
        reference=CodexReference(
            baseline_sha="a" * 40,
            reference_sha="b" * 40,
            changed_files=frozenset(
                {"src/general_ludd/self_improve/managed_execution.py"}
            ),
            test_files=frozenset(
                {"tests/unit/test_managed_self_improve_process.py"}
            ),
            changed_lines=1,
            elapsed_seconds=0.1,
        ),
        prompt="Return one bounded supervision improvement.",
        required_output_tokens=256,
        max_attempts=1,
    )


def test_process_executor_round_trips_repository_and_approved_identity(
    approved_self_improve_plan: ApprovedSelfImprovePlan,
) -> None:
    events: list[str] = []
    executor = ManagedSelfImproveProcessExecutor(
        runner_factory=object(),
        timeout_seconds=5.0,
        event_sink=events.append,
        operation=_return_identity,
    )

    result = executor.run(
        approved_self_improve_plan.repo_root,
        approved_self_improve_plan,
    )

    assert result == (
        str(approved_self_improve_plan.repo_root),
        approved_self_improve_plan.identity_digest,
    )
    assert events[0].startswith("OWNED_PROCESS_STARTED name=gludd-self-improve-")
    assert _executor_children() == []


def test_managed_deadline_terminates_and_joins_wedged_execution(
    approved_self_improve_plan: ApprovedSelfImprovePlan,
) -> None:
    executor = ManagedSelfImproveProcessExecutor(
        runner_factory=object(),
        timeout_seconds=0.5,
        operation=_wedge_managed,
    )

    with pytest.raises(OwnedProcessTimeout, match="deadline exceeded"):
        executor.run(
            approved_self_improve_plan.repo_root,
            approved_self_improve_plan,
        )

    assert _executor_children() == []


@pytest.mark.asyncio
async def test_cancelling_dispatch_stops_child_before_propagating_cancel(
    approved_self_improve_plan: ApprovedSelfImprovePlan,
) -> None:
    events: list[str] = []
    executor = ManagedSelfImproveProcessExecutor(
        runner_factory=object(),
        timeout_seconds=5.0,
        event_sink=events.append,
        operation=_wedge_managed,
    )
    task = asyncio.create_task(
        executor.run_async(
            approved_self_improve_plan.repo_root,
            approved_self_improve_plan,
        )
    )
    for _ in range(100):
        if any("OWNED_PROCESS_STARTED" in event for event in events):
            break
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert any("OWNED_PROCESS_CANCELLED" in event for event in events)
    assert _executor_children() == []


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, 1800.0),
        ({}, 1800.0),
        ({"managed_execution_timeout_seconds": 45}, 45.0),
        ({"managed_execution_timeout_seconds": "90"}, 90.0),
    ],
)
def test_managed_timeout_is_bounded_configuration(
    config: dict[str, object] | None,
    expected: float,
) -> None:
    assert managed_execution_timeout_seconds(config) == expected


@pytest.mark.parametrize(
    "value",
    [True, object(), 0, -1, float("inf"), "not-a-number", 7201],
)
def test_managed_timeout_rejects_invalid_or_excessive_configuration(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="managed_execution_timeout_seconds"):
        managed_execution_timeout_seconds(
            {"managed_execution_timeout_seconds": value}
        )


def test_executor_rejects_repository_mismatch_before_process_start(
    approved_self_improve_plan: ApprovedSelfImprovePlan,
    tmp_path: Path,
) -> None:
    executor = ManagedSelfImproveProcessExecutor(
        runner_factory=object(),
        timeout_seconds=5.0,
        operation=_return_identity,
    )

    with pytest.raises(ValueError, match="repository"):
        executor.run(tmp_path.resolve(), approved_self_improve_plan)

    assert _executor_children() == []


def test_default_operation_rebuilds_runner_inside_owned_child(
    approved_self_improve_plan: ApprovedSelfImprovePlan,
) -> None:
    executor = ManagedSelfImproveProcessExecutor(
        runner_factory=_IdentityRunnerFactory(),
        timeout_seconds=5.0,
    )

    assert (
        executor.run(
            approved_self_improve_plan.repo_root,
            approved_self_improve_plan,
        )
        == approved_self_improve_plan.identity_digest
    )
    assert _executor_children() == []


def test_configured_factory_passes_an_isolated_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.self_improve import runtime

    calls: list[tuple[Path, object]] = []
    sentinel = object()

    def build(repo_root: Path, *, self_improve_config: object) -> object:
        calls.append((repo_root, self_improve_config))
        return sentinel

    monkeypatch.setattr(runtime, "build_managed_self_improve_runner", build)
    config: dict[str, object] = {"nested": {"enabled": True}}
    factory = ConfiguredManagedRunnerFactory(config)

    assert factory(tmp_path) is sentinel
    nested = config["nested"]
    assert isinstance(nested, dict)
    nested["enabled"] = False
    assert calls == [(tmp_path, {"nested": {"enabled": True}})]


def test_managed_operation_rejects_invalid_factory_repository_and_runner(
    approved_self_improve_plan: ApprovedSelfImprovePlan,
    tmp_path: Path,
) -> None:
    plan_json = approved_self_improve_plan.to_json()
    with pytest.raises(TypeError, match="factory"):
        managed_execution_module._run_managed_runner(
            object(),
            str(approved_self_improve_plan.repo_root),
            plan_json,
        )

    repo_file = tmp_path / "not-a-directory"
    repo_file.write_text("file", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        managed_execution_module._run_managed_runner(
            _IdentityRunnerFactory(),
            str(repo_file),
            plan_json,
        )

    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    with pytest.raises(ValueError, match="different repository"):
        managed_execution_module._run_managed_runner(
            _IdentityRunnerFactory(),
            str(other_repo),
            plan_json,
        )

    with pytest.raises(TypeError, match="expose run"):
        managed_execution_module._run_managed_runner(
            _InvalidRunnerFactory(),
            str(approved_self_improve_plan.repo_root),
            plan_json,
        )


def test_executor_rejects_wrong_plan_type_before_process_start(
    approved_self_improve_plan: ApprovedSelfImprovePlan,
) -> None:
    executor = ManagedSelfImproveProcessExecutor(
        runner_factory=_IdentityRunnerFactory(),
        timeout_seconds=5.0,
    )

    with pytest.raises(TypeError, match="ApprovedSelfImprovePlan"):
        executor.run(
            approved_self_improve_plan.repo_root,
            object(),  # type: ignore[arg-type]
        )


class _ControlledOwnedChild:
    """Minimal deterministic child used to prove shared teardown branches."""

    def __init__(
        self,
        *,
        alive: bool,
        stop_after_joins: int | None = None,
        pid: int | None = 654_321,
    ) -> None:
        self._alive = alive
        self._stop_after_joins = stop_after_joins
        self._pid = pid
        self.join_count = 0

    @property
    def pid(self) -> int | None:
        return self._pid

    def is_alive(self) -> bool:
        return self._alive

    def join(self, _timeout: float | None = None) -> None:
        self.join_count += 1
        if self._stop_after_joins == self.join_count:
            self._alive = False

    def close(self) -> None:
        return None


def test_owned_group_signal_falls_back_to_exact_pid_and_ignores_unstarted_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing process group still receives a bounded direct-child signal."""
    direct: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        owned_process_module.os,
        "killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        owned_process_module.os,
        "kill",
        lambda pid, sent: direct.append((pid, sent)),
    )

    owned_process_module._signal_owned_group(
        _ControlledOwnedChild(alive=False, pid=None),
        signal.SIGTERM,
    )
    owned_process_module._signal_owned_group(
        _ControlledOwnedChild(alive=True),
        signal.SIGTERM,
    )

    assert direct == [(654_321, signal.SIGTERM)]
