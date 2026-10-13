"""Verify native Git role reuse preserves dispatch, authentication and results."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from ansible.module_utils import basic
from ansible.parsing.dataloader import DataLoader
from ansible_collections.general_ludd.agent.plugins.modules import gludd_git

from tests.unit.native_role_tasks import effective_arguments, role_tasks

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "collections/ansible_collections/general_ludd/agent/roles/git_automation"
CASES = (
    ("feature_start", {}, "feature_start"),
    ("feature_done", {}, "feature_done"),
    ("rebranch_onto", {}, "rebranch_onto"),
    ("ship_commit", {}, "ship_commit"),
    ("release_cut", {}, "release_cut"),
    ("release_recut", {}, "release_recut"),
    *(("worktree", {"worktree_op": operation}, f"worktree_{operation}")
      for operation in ("list", "create", "remove", "merge", "cleanup", "health_check", "merge_all")),
    ("branch", {"branch_op": "list"}, "branch_list"),
    ("branch", {"branch_op": "create"}, "branch"),
    ("branch", {"branch_op": "delete"}, "branch_delete"),
)


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


def inputs(operation: str, **overrides: object) -> dict[str, Any]:
    return {
        "git_op": operation, "repo_path": "/repo", "daemon_url": "http://daemon:8000", "psk": "secret",
        "feature_name": "topic", "new_base": "a" * 40, "commits": ["b" * 40],
        "commit_message": "change", "commit_files": ["one.py"],
        "release_tag": "v0.12.0", "worktree_branch": "topic", "branch_name": "topic",
        "worktree_path_param": "/owned/topic", **overrides,
    }


def test_git_entrypoint_has_one_native_dispatch_owner() -> None:
    loader = DataLoader()
    tasks = loader.load_from_file(str(ROLE / "tasks/main.yml"))
    includes = [task for task in tasks if "ansible.builtin.include_tasks" in task]
    assert len(includes) == 1
    selector = includes[0]["ansible.builtin.include_tasks"]
    assert isinstance(selector, dict) and "apply" in selector
    assert "general_ludd.agent.gludd_git" in selector["apply"]["module_defaults"]


@pytest.mark.parametrize(("operation", "options", "expected"), CASES)
def test_native_effective_git_arguments_preserve_identity_authentication_and_budget(
    operation: str, options: dict[str, str], expected: str,
) -> None:
    calls = [effective_arguments(task, templar) for task, templar in role_tasks(ROLE, inputs(operation, **options))
             if task.action == "general_ludd.agent.gludd_git"]
    assert [call["op"] for call in calls] == (["state", expected] if operation == "feature_done" else [expected])
    for call in calls:
        assert call["path"] == "/repo"
        assert call["daemon_url"] == "http://daemon:8000"
        assert call["psk"] == "secret"
        gate_calls = {"feature_done", "worktree_merge", "worktree_merge_all"}
        assert call["timeout"] == (3900 if call["op"] in gate_calls else 300)
    if operation == "feature_done":
        assert calls[0]["state_assert_clean"] is True
    if operation in {"release_cut", "release_recut"}:
        assert calls[-1]["branch"] == "master" and calls[-1]["release_remote"] == "sandboxcom"
    if expected == "ship_commit":
        assert calls[-1]["files"] == ["one.py"] and calls[-1]["skip_gate"] is False


@pytest.mark.parametrize(("operation", "options", "expected"), CASES)
def test_named_task_entrypoints_retain_scoped_authentication_and_exact_budget(
    operation: str, options: dict[str, str], expected: str,
) -> None:
    call = next(effective_arguments(task, templar) for task, templar in role_tasks(
        ROLE, inputs(operation, **options), entrypoint=f"{operation}.yml",
    ) if task.action == "general_ludd.agent.gludd_git" and effective_arguments(task, templar)["op"] == expected)
    assert call["daemon_url"] == "http://daemon:8000" and call["psk"] == "secret"
    gate_calls = {"feature_done", "worktree_merge", "worktree_merge_all"}
    assert call["timeout"] == (3900 if expected in gate_calls else 300)


@pytest.mark.parametrize(("operation", "options", "expected"),
                         tuple(case for case in CASES if case[0] not in {"feature_done", "release_cut"}))
def test_shared_native_request_owner_preserves_original_registered_result(
    operation: str, options: dict[str, str], expected: str,
) -> None:
    result = {"result": {"success": True, "token": "native-result"}, "changed": True, "branch": "topic"}
    tasks = list(role_tasks(ROLE, inputs(operation, **options, _git_automation_request_result=result)))
    calls = [(task, effective_arguments(task, templar)) for task, templar in tasks
             if task.action == "general_ludd.agent.gludd_git"]
    assert len(calls) == 1 and calls[0][1]["op"] == expected
    registration = calls[0][0].register
    if isinstance(registration, dict):
        assert registration == {"_git_automation_request_result": "_task.polymorphic_result"}
    else:
        assert registration == "_git_automation_request_result"
    aliases = [effective_arguments(task, templar) for task, templar in tasks
               if task.get_name() == "Preserve the operation's native registration"]
    assert len(aliases) == 1 and list(aliases[0].values()) == [result]


@pytest.mark.parametrize(
    ("operation", "options"),
    (("feature_start", {}), ("rebranch_onto", {}), ("branch", {"branch_op": "delete"}),
     ("worktree", {"worktree_op": "merge"}), ("worktree", {"worktree_op": "cleanup"})),
)
@pytest.mark.parametrize("ref", ("", "-option"))
def test_shared_ref_assertions_reject_empty_and_option_inputs(
    operation: str, options: dict[str, str], ref: str,
) -> None:
    variables = inputs(operation, **options, feature_name=ref, new_base=ref, branch_name=ref, worktree_branch=ref)
    assertions = [(effective_arguments(task, templar), templar) for task, templar in role_tasks(ROLE, variables)
                  if task.action == "ansible.builtin.assert"]
    assert any(not templar._engine.evaluate_conditional(check)
               for assertion, templar in assertions for check in assertion["that"])


class _Finished(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


@pytest.mark.parametrize(("operation", "options", "expected"), CASES)
def test_role_rendered_module_check_mode_retains_mutation_boundary_and_read_only_requests(
    operation: str, options: dict[str, str], expected: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = next(effective_arguments(task, templar) for task, templar in role_tasks(ROLE, inputs(operation, **options))
                if task.action == "general_ludd.agent.gludd_git"
                and effective_arguments(task, templar)["op"] == expected)
    factory = Mock()
    factory.return_value.post.return_value = {"_status": 200, "result": {"success": True}, "changed": False}
    monkeypatch.setattr(gludd_git, "GluddClient", factory)
    monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    arguments = {"ANSIBLE_MODULE_ARGS": {**call, "_ansible_check_mode": True}}
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps(arguments).encode())

    def finish(_self: object, **payload: Any) -> None:
        raise _Finished(payload)

    monkeypatch.setattr(basic.AnsibleModule, "exit_json", finish)
    with pytest.raises(_Finished) as result:
        gludd_git.main()
    if expected in {"branch_list", "worktree_list", "worktree_health_check"}:
        assert result.value.payload["changed"] is False
        factory.assert_called_once_with(base_url="http://daemon:8000", psk="secret", timeout=300)
    else:
        assert result.value.payload["changed"] is True
        factory.assert_not_called()
        assert result.value.payload["result"]["would_change"] is True


