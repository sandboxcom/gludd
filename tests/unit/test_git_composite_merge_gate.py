"""Require the canonical gate before authenticated composite merge mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from general_ludd.commands.make import MakeResult
from general_ludd.routers import git_history
from tests.unit.native_role_tasks import effective_arguments, role_tasks

ROOT = Path(__file__).resolve().parents[2]
CASES = (
    ("feature_done", {"feature_name": "topic", "target": "development"}),
    ("worktree_merge", {"branch": "topic", "target": "development"}),
    ("worktree_merge_all", {"target": "development"}),
)
FAILURES = (
    MakeResult(target="gate", exit_code=1, success=False, duration_s=1.0),
    MakeResult(target="gate", exit_code=None, success=False, duration_s=1.0, error="unavailable"),
    MakeResult(target="gate", exit_code=0, success=True, duration_s=1.0, timed_out=True),
    MakeResult(target="gate", exit_code=0, success=True, duration_s=1.0, oom_killed=True),
    MakeResult(target="gate", exit_code=2, success=True, duration_s=1.0),
    MakeResult(target="gate", exit_code=None, success=True, duration_s=1.0),
    MakeResult(target="gate-lite", exit_code=0, success=True, duration_s=1.0),
    MakeResult(target="gate", exit_code=0, success=True, duration_s=1.0, error="incomplete"),
)


def _request(op: str, parameters: dict[str, Any]) -> git_history.GitOperationRequest:
    return git_history.GitOperationRequest(op=cast(git_history.GitOperation, op), path="/repo", **parameters)


@pytest.mark.parametrize(("op", "parameters"), CASES)
@pytest.mark.parametrize("gate_result", FAILURES)
def test_composite_merge_refuses_every_unproved_gate_before_mutation(
    op: str, parameters: dict[str, Any], gate_result: MakeResult, monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = Mock()
    mutation = Mock(return_value={"success": True, "merged": 1})
    runner = Mock()
    runner.run_gate.return_value = gate_result
    factory = Mock(return_value=runner)
    monkeypatch.setattr(git_history, "GitAutomation", lambda _path: git)
    monkeypatch.setattr(git_history, op, mutation)
    monkeypatch.setattr(git_history, "MakeRunner", factory, raising=False)
    with pytest.raises(ValueError, match="canonical gate"):
        git_history._dispatch_git(_request(op, parameters))
    factory.assert_called_once_with(cwd="/repo")
    runner.run_gate.assert_called_once_with()
    mutation.assert_not_called()
    assert not git.mock_calls


@pytest.mark.parametrize(("op", "parameters"), CASES)
def test_composite_merge_uses_canonical_gate_and_ignores_weaker_request_commands(
    op: str, parameters: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def gate() -> MakeResult:
        events.append("gate")
        return MakeResult(target="gate", exit_code=0, success=True, duration_s=1.0)

    def merge(*_args: object, **_kwargs: object) -> dict[str, object]:
        events.append("merge")
        return {"success": True, "merged": 1}

    runner = Mock()
    runner.run_gate.side_effect = gate
    monkeypatch.setattr(git_history, "GitAutomation", lambda _path: Mock())
    monkeypatch.setattr(git_history, op, merge)
    monkeypatch.setattr(git_history, "MakeRunner", Mock(return_value=runner), raising=False)
    result, changed = git_history._dispatch_git(_request(op, {**parameters, "gate_cmd": ["make", "gate-lite"]}))
    assert events == ["gate", "merge"]
    assert result["success"] is True and changed is True
    runner.run_gate.assert_called_once_with()
    runner.run.assert_not_called()


@pytest.mark.parametrize(("op", "parameters"), CASES)
def test_master_promotion_is_refused_before_gate_or_merge(
    op: str, parameters: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = Mock()
    mutation = Mock(return_value={"success": True, "merged": 1})
    monkeypatch.setattr(git_history, "MakeRunner", factory, raising=False)
    monkeypatch.setattr(git_history, "GitAutomation", lambda _path: Mock())
    monkeypatch.setattr(git_history, op, mutation)
    with pytest.raises(ValueError, match="release-promote"):
        git_history._dispatch_git(_request(op, {**parameters, "target": "master"}))
    factory.assert_not_called()
    mutation.assert_not_called()


def test_feature_done_control_plane_and_role_default_to_development(monkeypatch: pytest.MonkeyPatch) -> None:
    mutation = Mock(return_value={"success": True})
    git = Mock()
    runner = Mock()
    runner.run_gate.return_value = MakeResult(target="gate", exit_code=0, success=True, duration_s=1.0)
    monkeypatch.setattr(git_history, "GitAutomation", lambda _path: git)
    monkeypatch.setattr(git_history, "feature_done", mutation)
    monkeypatch.setattr(git_history, "MakeRunner", Mock(return_value=runner), raising=False)
    git_history._dispatch_git(_request("feature_done", {"feature_name": "topic"}))
    mutation.assert_called_once_with(git=git, name="topic", target="development")
    role = ROOT / "collections/ansible_collections/general_ludd/agent/roles/git_automation"
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        calls = [effective_arguments(task, templar) for task, templar in role_tasks(
            role, {"git_op": "feature_done", "repo_path": "/repo", "feature_name": "topic"},
        ) if task.action == "general_ludd.agent.gludd_git"]
    call = next(call for call in calls if call["op"] == "feature_done")
    assert call["target"] == "development"
