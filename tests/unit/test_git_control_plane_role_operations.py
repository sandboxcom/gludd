"""Keep composite role operations on their existing authenticated Git owners."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import Mock

import pytest
from ansible.module_utils import basic
from ansible_collections.general_ludd.agent.plugins.modules import gludd_git
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError

from general_ludd.commands.make import MakeResult
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.ship_commit import ShipCommitError
from general_ludd.git_automation.types import MergeResult, WorktreeResult
from general_ludd.git_automation.worktree import WorktreeHealthViolation
from general_ludd.routers import git_history

OPERATIONS = (
    ("feature_start", {"feature_name": "topic", "base": "development"}),
    ("feature_done", {"feature_name": "topic", "target": "development"}),
    ("rebranch_onto", {"new_base": "a" * 40, "commits": ["b" * 40]}),
    ("ship_commit", {"message": "feat: bounded change", "files": ["one.py"]}),
    ("worktree_merge", {"branch": "topic", "target": "development"}),
    ("worktree_cleanup", {"branch": "topic"}),
    ("worktree_health_check", {"max_age_hours": 12, "remote_name": "local", "target_branch": "development"}),
    ("worktree_merge_all", {"target": "development"}),
)


def _request(op: str, **kwargs: Any) -> git_history.GitOperationRequest:
    return git_history.GitOperationRequest(op=cast(git_history.GitOperation, op), path="/repo", **kwargs)


@pytest.fixture
def git_owner(monkeypatch: pytest.MonkeyPatch) -> Mock:
    owner = Mock()
    monkeypatch.setattr(git_history, "GitAutomation", lambda _path: owner)
    runner = Mock()
    runner.run_gate.return_value = MakeResult(target="gate", exit_code=0, success=True, duration_s=1.0)
    monkeypatch.setattr(git_history, "MakeRunner", Mock(return_value=runner), raising=False)
    return owner


@pytest.mark.parametrize(("op", "parameters"), OPERATIONS)
def test_composite_git_operations_are_allowlisted_with_native_module_options(
    op: str, parameters: dict[str, object],
) -> None:
    request = _request(op, **parameters)
    spec = gludd_git._argument_spec()
    assert request.op == op
    assert op in spec["op"]["choices"]
    assert parameters.keys() <= spec.keys()


@pytest.mark.parametrize(
    ("op", "parameters"),
    (
        ("feature_start", {}), ("feature_done", {}), ("rebranch_onto", {}),
        ("ship_commit", {}), ("worktree_merge", {"branch": "topic"}),
        ("worktree_cleanup", {}),
    ),
)
def test_composite_git_operations_require_their_real_inputs(
    op: str, parameters: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="requires"):
        _request(op, **parameters)


def test_feature_dispatch_preserves_base_no_ff_and_cherry_pick_owners(
    git_owner: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = Mock(return_value="feature/topic")
    done = Mock(return_value={"success": True, "branch": "feature/topic"})
    rebranch = Mock(return_value={"success": True, "branch": "replacement", "original": "topic"})
    for name, owner in (("feature_start", start), ("feature_done", done), ("rebranch_onto", rebranch)):
        monkeypatch.setattr(git_history, name, owner, raising=False)
    result, changed = git_history._dispatch_git(_request("feature_start", feature_name="topic", base="development"))
    start.assert_called_once_with(git=git_owner, name="topic", base="development")
    assert result == {"branch": "feature/topic", "success": True} and changed is True
    result, changed = git_history._dispatch_git(_request("feature_done", feature_name="topic", target="development"))
    done.assert_called_once_with(git=git_owner, name="topic", target="development")
    assert result["success"] is True and changed is True
    result, changed = git_history._dispatch_git(_request("rebranch_onto", new_base="abc", commits=["def"]))
    rebranch.assert_called_once_with(git=git_owner, new_base="abc", commits=["def"])
    assert result["original"] == "topic" and changed is True


def test_ship_commit_checks_collection_and_gate_before_optional_push(
    git_owner: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    collect = Mock(side_effect=lambda _root: calls.append("collect"))
    def do_ship(*_args: object, **_kwargs: object) -> str:
        calls.append("ship")
        return "abc123"

    ship = Mock(side_effect=do_ship)
    monkeypatch.setattr(git_history, "collect_check", collect, raising=False)
    monkeypatch.setattr(git_history, "ship_commit", ship, raising=False)
    result, changed = git_history._dispatch_git(_request("ship_commit", message="change", files=["one.py"], push=True))
    collect.assert_called_once_with("/repo")
    ship.assert_called_once_with("change", git=git_owner, files=["one.py"], push=True, repo_root="/repo")
    assert calls == ["collect", "ship"]
    assert result == {"sha": "abc123", "success": True} and changed is True


def test_ship_commit_rejects_gate_bypass_in_the_control_plane() -> None:
    with pytest.raises(ValidationError, match="skip_gate"):
        _request("ship_commit", message="change", skip_gate=True)


def test_worktree_dispatch_preserves_native_owner_results_and_read_only_health(
    git_owner: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    merge = Mock(return_value=MergeResult(success=True))
    cleanup = Mock(return_value={"success": True, "cleaned": False, "branch_removed": False})
    violation = WorktreeHealthViolation(
        worktree_path="/worktrees/topic", branch="topic", reason="stale", severity="error",
    )
    health = Mock(return_value=[violation])
    merge_all = Mock(return_value={"total": 1, "merged": 1, "conflicts": 0, "skipped": 0, "errors": []})
    for name, owner in (("worktree_merge", merge), ("worktree_cleanup", cleanup),
                        ("worktree_health_check", health), ("worktree_merge_all", merge_all)):
        monkeypatch.setattr(git_history, name, owner, raising=False)
    result, changed = git_history._dispatch_git(_request("worktree_merge", branch="topic", target="development"))
    merge.assert_called_once_with("/repo", "topic", "development")
    assert result["success"] is True and changed is True
    result, changed = git_history._dispatch_git(_request("worktree_cleanup", branch="topic"))
    cleanup.assert_called_once_with("/repo", "topic")
    assert result["success"] is True and changed is False
    result, changed = git_history._dispatch_git(_request(
        "worktree_health_check", max_age_hours=12, remote_name="local", target_branch="development",
    ))
    health.assert_called_once_with("/repo", 12, "local", "development")
    assert result["violations"][0]["reason"] == "stale" and changed is False
    result, changed = git_history._dispatch_git(_request("worktree_merge_all", target="development"))
    merge_all.assert_called_once_with("/repo", "development")
    assert result["merged"] == 1 and changed is True


def test_worktree_create_preserves_explicit_base(git_owner: Mock) -> None:
    git_owner.create_worktree.return_value = WorktreeResult(path="/worktrees/topic", branch="topic", success=True)
    result, changed = git_history._dispatch_git(
        _request("worktree_create", branch="topic", worktree_path="/worktrees/topic", base="development"),
    )
    git_owner.create_worktree.assert_called_once_with("/repo", "topic", "/worktrees/topic", base_branch="development")
    assert result["success"] is True and changed is True


@pytest.mark.parametrize("base", ("HEAD", "development", "a" * 40))
def test_worktree_create_native_owner_uses_exact_base_and_owned_destination(
    base: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = GitAutomation("/repo")
    command = Mock()
    authority = Mock()
    monkeypatch.setattr(git, "_run_git", command)
    monkeypatch.setattr(git, "_reject_escaping_path", authority)
    result = git.create_worktree("/repo", "topic", "/owned/topic", base_branch=base)
    authority.assert_called_once_with("/repo", "/owned/topic")
    command.assert_called_once_with("worktree", "add", "-b", "topic", "--", "/owned/topic", base, _cwd="/repo")
    assert result.success is True


@pytest.mark.parametrize(
    ("branch", "destination", "base"),
    (("-bad", "/owned/topic", "HEAD"), ("topic", "-bad", "HEAD"),
     ("topic", "/owned/topic", "-bad"), ("topic", "/owned/topic", ""),
     ("topic", "/owned/topic", "   ")),
)
def test_worktree_create_invalid_inputs_never_mutate(
    branch: str, destination: str, base: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = GitAutomation("/repo")
    command = Mock()
    monkeypatch.setattr(git, "_run_git", command)
    monkeypatch.setattr(git, "_reject_escaping_path", lambda *_args: None)
    result = git.create_worktree("/repo", branch, destination, base_branch=base)
    assert result.success is False
    command.assert_not_called()


def test_worktree_create_destination_owner_refusal_precedes_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    git = GitAutomation("/repo")
    command = Mock()
    monkeypatch.setattr(git, "_run_git", command)
    monkeypatch.setattr(git, "_reject_escaping_path", Mock(side_effect=ValueError("unauthorized destination")))
    result = git.create_worktree("/repo", "topic", "/unowned/topic", base_branch="development")
    assert result.success is False and "unauthorized destination" in result.message
    command.assert_not_called()


def test_worktree_create_default_remains_head(monkeypatch: pytest.MonkeyPatch) -> None:
    git = GitAutomation("/repo")
    command = Mock()
    monkeypatch.setattr(git, "_run_git", command)
    monkeypatch.setattr(git, "_reject_escaping_path", lambda *_args: None)
    result = git.create_worktree("/repo", "topic", "/owned/topic")
    assert result.success is True
    assert command.call_args.args[-1] == "HEAD"


@pytest.mark.parametrize("field", ("max_age_hours",))
@pytest.mark.parametrize("value", (0, -1, 87601, True, "12"))
def test_health_budget_is_strict_and_bounded(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _request("worktree_health_check", **{field: value})


@pytest.mark.parametrize("field", ("feature_name", "base", "new_base", "remote_name", "target_branch"))
@pytest.mark.parametrize("value", ("-option", "   "))
def test_composite_ref_options_are_rejected_before_dispatch(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="Git refs"):
        _request("current_branch", **{field: value})


@pytest.mark.parametrize("commits", (["-option"], [""], ["x" * 4097]))
def test_rebranch_commit_options_are_rejected_before_dispatch(commits: list[str]) -> None:
    with pytest.raises(ValidationError):
        _request("rebranch_onto", new_base="abc", commits=commits)


class _ModuleExit(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


@pytest.fixture
def module_json(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original_args = basic._ANSIBLE_ARGS
    monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    def exit_json(_self: object, **kwargs: Any) -> None:
        raise _ModuleExit(kwargs)

    def fail_json(_self: object, **kwargs: Any) -> None:
        raise AssertionError(kwargs)

    monkeypatch.setattr(basic.AnsibleModule, "exit_json", exit_json)
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail_json)
    yield
    basic._ANSIBLE_ARGS = original_args


@pytest.mark.parametrize(("op", "parameters"), OPERATIONS)
def test_native_module_check_mode_and_authenticated_payload(
    op: str, parameters: dict[str, object], module_json: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    client.post.return_value = {"_status": 200, "result": {"success": True}, "changed": False}
    factory = Mock(return_value=client)
    monkeypatch.setattr(gludd_git, "GluddClient", factory)
    args = {"op": op, "path": "/repo", "daemon_url": "http://daemon:8000", "psk": "secret", **parameters}
    basic._ANSIBLE_ARGS = json.dumps({"ANSIBLE_MODULE_ARGS": {**args, "_ansible_check_mode": True}}).encode()
    with pytest.raises(_ModuleExit) as check:
        gludd_git.main()
    if op == "worktree_health_check":
        assert check.value.payload["changed"] is False
        assert client.post.call_count == 1
    else:
        assert check.value.payload["result"]["would_change"] is True
        factory.assert_not_called()
    factory.reset_mock()
    client.reset_mock()
    basic._ANSIBLE_ARGS = json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode()
    with pytest.raises(_ModuleExit):
        gludd_git.main()
    timeout = 3900 if op in {"feature_done", "worktree_merge", "worktree_merge_all"} else 300
    factory.assert_called_once_with(base_url="http://daemon:8000", psk="secret", timeout=timeout)
    endpoint, payload = client.post.call_args.args
    assert endpoint == "/admin/git/operation"
    assert payload["op"] == op and "psk" not in payload
    for key, value in parameters.items():
        assert payload[key] == value


def test_composite_http_gate_failures_are_explicit_and_authenticated(
    git_owner: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.capability_guard import RequireCapability

    app = FastAPI()
    git_history.register(app, {})
    ship = Mock(side_effect=ShipCommitError("Gate is red"))
    monkeypatch.setattr(git_history, "ship_commit", ship, raising=False)
    monkeypatch.setattr(git_history, "collect_check", lambda _root: None, raising=False)
    body = {"op": "ship_commit", "path": "/repo", "message": "change"}
    with TestClient(app) as client:
        forbidden = client.post("/admin/git/operation", json=body)
        assert forbidden.status_code in {401, 403}
        ship.assert_not_called()
        route = next(route for route in app.routes if getattr(route, "path", None) == "/admin/git/operation")
        assert isinstance(route, APIRoute)
        dependency = next(dep.call for dep in route.dependant.dependencies
                          if isinstance(dep.call, RequireCapability))
        app.dependency_overrides[dependency] = lambda: None
        rejected = client.post("/admin/git/operation", json=body)
        assert rejected.status_code == 422
        assert "Gate is red" in rejected.json()["detail"]
