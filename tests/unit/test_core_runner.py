"""Focused unit tests for thread-safe Ansible timeout isolation (forkserver-preferred, spawn fallback)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.ansible import core_runner
from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner


def test_timeout_process_uses_spawn_and_module_level_target() -> None:
    queue = MagicMock()
    queue.get_nowait.return_value = (
        "ok",
        AnsibleResult(status="successful", rc=0).model_dump(),
    )
    process = MagicMock()
    process.pid = None
    process.is_alive.return_value = False
    context = MagicMock()
    context.Queue.return_value = queue
    context.Process.return_value = process

    runner = CoreAnsibleRunner()
    with patch.object(
        core_runner.multiprocessing,
        "get_context",
        return_value=context,
    ) as get_context:
        result = runner._run_with_timeout(
            timeout=1.0,
            playbook_path="/tmp/unit-test.yml",
        )

    expected = "forkserver" if "forkserver" in core_runner.multiprocessing.get_all_start_methods() else "spawn"
    get_context.assert_called_once_with(expected)
    target = context.Process.call_args.kwargs["target"]
    assert target is core_runner._timeout_child_entry
    assert "<locals>" not in target.__qualname__
    assert result.status == "successful"


def test_timeout_child_start_failure_returns_failed_result() -> None:
    queue = MagicMock()
    process = MagicMock()
    process.start.side_effect = TypeError("cannot pickle runner")
    context = MagicMock()
    context.Queue.return_value = queue
    context.Process.return_value = process

    runner = CoreAnsibleRunner()
    with patch.object(
        core_runner.multiprocessing,
        "get_context",
        return_value=context,
    ):
        result = runner._run_with_timeout(
            timeout=1.0,
            playbook_path="/tmp/unit-test.yml",
        )

    assert result.status == "failed"
    assert result.rc != 0
    expected = "forkserver" if "forkserver" in core_runner.multiprocessing.get_all_start_methods() else "spawn"
    assert result.error == f"unable to start {expected} playbook timeout worker: TypeError: cannot pickle runner"
    queue.close.assert_called_once_with()
    queue.join_thread.assert_called_once_with()
