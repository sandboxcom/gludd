"""Keep guarded native requests faithful to their exact public result facts."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from ansible.parsing.dataloader import DataLoader

from tests.unit.native_role_tasks import effective_arguments, role_tasks
from tests.unit.test_git_list_reuse import native_facts

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "collections/ansible_collections/general_ludd/agent/roles/git_automation"


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


@pytest.mark.parametrize(("operation", "suboperation", "native_op", "alias", "task_name"), (
    ("branch", "create", "branch", "_branch_create", "Create branch"),
    ("branch", "delete", "branch_delete", "_branch_delete", "Delete branch"),
    ("worktree", "remove", "worktree_remove", "_wt_remove", "Remove worktree"),
))
@pytest.mark.parametrize("response", ("positive", "false", "default"))
@pytest.mark.parametrize("direct", (False, True))
def test_guarded_requests_share_native_result_owner_and_preserve_exact_facts(
    operation: str, suboperation: str, native_op: str, alias: str, task_name: str,
    response: str, direct: bool,
) -> None:
    result: dict[str, Any] = {"result": {"other": "must-not-be-published"}}
    if response != "default":
        result.update(branch="returned/topic", changed=response == "positive")
        result["result"].update(deleted=response == "positive", removed=response == "positive")
    variables = {"git_op": operation, f"{operation}_op": suboperation, "repo_path": "/repo",
                 "branch_name": "requested/topic", "worktree_path_param": "/owned/topic",
                 "daemon_url": "http://daemon:8000", "psk": "secret", "_git_automation_request_result": result}
    entrypoint = f"{operation}.yml" if direct else "main.yml"
    tasks = list(role_tasks(ROLE, variables, entrypoint=entrypoint))
    fact = f"git_automation_{operation}_result"
    published = [(task, native_facts(task, templar)) for task, templar in tasks
                 if task.action == "ansible.builtin.set_fact"
                 and fact in native_facts(task, templar)]
    expected: dict[str, Any] = {"op": suboperation}
    if suboperation == "create":
        expected.update(name=result.get("branch", "requested/topic"), created=response == "positive")
    elif suboperation == "delete":
        expected.update(name="requested/topic", deleted=response == "positive")
    else:
        expected.update(success=response == "positive", path="/owned/topic")
    assert len(published) == 1 and published[0][1] == {fact: expected}
    aliases = [native_facts(task, templar) for task, templar in tasks
               if task.get_name() == "Preserve the operation's native registration"]
    assert aliases == [{alias: result}]
    request_input = {"worktree_path": "/owned/topic"} if operation == "worktree" else {"branch": "requested/topic"}
    calls = [effective_arguments(task, templar) for task, templar in tasks
             if task.action == "general_ludd.agent.gludd_git"]
    assert calls == [{"path": "/repo", "op": native_op, **request_input,
                      "daemon_url": "http://daemon:8000", "psk": "secret", "timeout": 300}]
    failure = ("worktree_path_param required for worktree remove, no leading '-'" if operation == "worktree"
               else "branch_name must be set and not start with '-'")
    guards = [(effective_arguments(task, templar), templar) for task, templar in tasks
              if task.get_name() == "Validate the required Git reference"]
    assert len(guards) == 1 and guards[0][0]["fail_msg"] == failure
    assert all(guards[0][1]._engine.evaluate_conditional(check) for check in guards[0][0]["that"])
    ref_input = "worktree_path_param" if operation == "worktree" else "branch_name"
    for invalid in ({ref_input: ""}, {ref_input: "-option"}, {"repo_path": ""}):
        invalid_guards = [(effective_arguments(task, templar), templar)
                          for task, templar in role_tasks(ROLE, {**variables, **invalid}, entrypoint=entrypoint)
                          if task.get_name() == "Validate the required Git reference"]
        assert len(invalid_guards) == 1 and invalid_guards[0][0]["fail_msg"] == failure
        assert any(not invalid_guards[0][1]._engine.evaluate_conditional(check)
                   for check in invalid_guards[0][0]["that"])
    assert published[0][0].get_name() == "Publish guarded Git reference result"
    source = DataLoader().load_from_file(str(ROLE / "tasks" / f"{operation}.yml"))
    selected = next(task for task in source if task.get("name") == task_name)
    assert selected["ansible.builtin.import_tasks"] == "ref_result.yml"
    assert selected["when"] == f"{operation}_op == '{suboperation}'"
