"""Keep shared native list/remove owners faithful to their exact public results."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.play_context import PlayContext
from ansible.playbook.task import Task
from ansible.plugins.action.set_fact import ActionModule
from ansible.template import Templar

from tests.unit.native_role_tasks import effective_arguments, role_tasks

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "collections/ansible_collections/general_ludd/agent/roles/git_automation"


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


def native_facts(task: Task, templar: Templar) -> dict[str, Any]:
    """Run the actual fact action, recording host publication without a remote host."""
    rendered = task.copy()
    rendered.args = effective_arguments(task, templar)
    action = ActionModule(task=rendered, connection=Mock(), play_context=PlayContext(),
                          loader=DataLoader(), templar=templar)
    if hasattr(action, "register_host_variables"):
        action.register_host_variables = Mock()
    return cast(dict[str, Any], action.run()["ansible_facts"])


@pytest.mark.parametrize(("operation", "field", "alias"),
                         (("branch", "branches", "_branch_list"), ("worktree", "worktrees", "_wt_list")))
@pytest.mark.parametrize("values", ([], ["topic"], [{"branch": "topic", "path": "/owned/topic"}]))
@pytest.mark.parametrize("direct", (False, True))
def test_guarded_lists_share_native_owner_and_keep_exact_result_fields(
    operation: str, field: str, alias: str, values: list[Any], direct: bool,
) -> None:
    result = {"result": {field: values, "other": ["must-not-be-selected"]}, "changed": False}
    variables = {"git_op": operation, f"{operation}_op": "list", "repo_path": "/repo",
                 "daemon_url": "http://daemon:8000", "psk": "secret", "_git_automation_request_result": result}
    tasks = list(role_tasks(ROLE, variables, entrypoint=f"{operation}.yml" if direct else "main.yml"))
    fact = f"git_automation_{operation}_result"
    published = [(task, native_facts(task, templar)) for task, templar in tasks
                 if task.action == "ansible.builtin.set_fact"
                 and fact in native_facts(task, templar)]
    assert len(published) == 1 and published[0][1] == {fact: {"op": "list", field: values}}
    assert published[0][0].get_name() == "Publish guarded Git list result"
    aliases = [native_facts(task, templar) for task, templar in tasks
               if task.get_name() == "Preserve the operation's native registration"]
    assert aliases == [{alias: result}]
    calls = [effective_arguments(task, templar) for task, templar in tasks
             if task.action == "general_ludd.agent.gludd_git"]
    assert calls == [{"path": "/repo", "op": f"{operation}_list", "daemon_url": "http://daemon:8000",
                      "psk": "secret", "timeout": 300}]


@pytest.mark.parametrize("removed", (True, False, None))
def test_remove_reuses_ref_request_and_preserves_exact_result_and_ref_guard(removed: bool | None) -> None:
    loader = DataLoader()
    source = loader.load_from_file(str(ROLE / "tasks/worktree.yml"))
    remove = next(task for task in source if task.get("name") == "Remove worktree")
    assert remove["ansible.builtin.import_tasks"] == "ref_result.yml"
    payload = {} if removed is None else {"removed": removed}
    result = {"result": payload, "changed": bool(removed)}
    variables = {"git_op": "worktree", "worktree_op": "remove", "repo_path": "/repo",
                 "worktree_path_param": "/owned/topic", "_git_automation_request_result": result}
    tasks = list(role_tasks(ROLE, variables))
    facts = [native_facts(task, templar) for task, templar in tasks
             if task.action == "ansible.builtin.set_fact"
             and "git_automation_worktree_result" in native_facts(task, templar)]
    assert facts == [{"git_automation_worktree_result":
                      {"op": "remove", "success": bool(removed), "path": "/owned/topic"}}]
    aliases = [native_facts(task, templar) for task, templar in tasks
               if task.get_name() == "Preserve the operation's native registration"]
    assert aliases == [{"_wt_remove": result}]
    invalid = {**variables, "worktree_path_param": "-option"}
    assert any(not templar._engine.evaluate_conditional(check)
               for task, templar in role_tasks(ROLE, invalid) if task.action == "ansible.builtin.assert"
               for check in effective_arguments(task, templar)["that"])
