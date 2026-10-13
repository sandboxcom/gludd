"""Keep composite Git HTTP budgets longer than their canonical gate owner."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from ansible.module_utils import basic
from ansible_collections.general_ludd.agent.plugins.modules import gludd_git

from general_ludd.commands.make import MakeResult, MakeRunner
from tests.unit.native_role_tasks import effective_arguments, role_tasks

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "collections/ansible_collections/general_ludd/agent/roles/git_automation"
GATE_CASES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("feature_done", {"feature_name": "topic"}),
    ("worktree_merge", {"branch": "topic", "target": "development"}),
    ("worktree_merge_all", {}),
)
ORDINARY_CASES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("current_branch", {}), ("worktree_health_check", {}),
    ("commit", {"message": "change"}), ("feature_start", {"feature_name": "topic"}),
)


class _Finished(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


class _Refused(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


@pytest.fixture
def module_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Mock]:
    original_args = basic._ANSIBLE_ARGS
    monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")

    def exit_json(_self: object, **kwargs: Any) -> None:
        raise _Finished(kwargs)

    def fail_json(_self: object, **kwargs: Any) -> None:
        raise _Refused(kwargs)

    monkeypatch.setattr(basic.AnsibleModule, "exit_json", exit_json)
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail_json)
    client = Mock()
    client.post.return_value = {"_status": 200, "result": {"success": True}, "changed": True}
    factory = Mock(return_value=client)
    monkeypatch.setattr(gludd_git, "GluddClient", factory)
    yield factory
    basic._ANSIBLE_ARGS = original_args


def _run(op: str, parameters: dict[str, Any], **options: object) -> None:
    args = {"path": "/repo", "op": op, "psk": "secret", **parameters, **options}
    basic._ANSIBLE_ARGS = json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode()
    gludd_git.main()


def _gate_seconds(monkeypatch: pytest.MonkeyPatch) -> int:
    runner = MakeRunner(cwd="/repo")
    run = Mock(return_value=MakeResult(target="gate", exit_code=0, success=True, duration_s=0))
    monkeypatch.setattr(runner, "run", run)
    runner.run_gate()
    return int(run.call_args.kwargs["timeout_s"])


@pytest.mark.parametrize(("op", "parameters"), GATE_CASES)
def test_composite_default_covers_canonical_gate_and_ordinary_cleanup_budget(
    op: str, parameters: dict[str, Any], module_client: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_seconds = _gate_seconds(monkeypatch)
    with pytest.raises(_Finished):
        _run(op, parameters)
    assert module_client.call_args.kwargs["timeout"] == gate_seconds + 300
    assert gate_seconds == 3600


@pytest.mark.parametrize(("op", "parameters"), ORDINARY_CASES)
def test_ordinary_and_read_only_defaults_remain_300_seconds(
    op: str, parameters: dict[str, Any], module_client: Mock,
) -> None:
    with pytest.raises(_Finished):
        _run(op, parameters)
    assert module_client.call_args.kwargs["timeout"] == 300


@pytest.mark.parametrize(("op", "parameters"), GATE_CASES)
@pytest.mark.parametrize("seconds", (-1, 0, 100, 300, 3600, 3899))
def test_insufficient_explicit_composite_budget_is_refused_before_http_dispatch(
    op: str, parameters: dict[str, Any], seconds: int, module_client: Mock,
) -> None:
    with pytest.raises(_Refused) as refused:
        _run(op, parameters, timeout=seconds)
    assert "3900" in refused.value.payload["msg"]
    module_client.assert_not_called()


@pytest.mark.parametrize(("op", "parameters"), GATE_CASES)
@pytest.mark.parametrize("seconds", (3900, 4200))
def test_adequate_explicit_composite_budget_is_preserved_exactly(
    op: str, parameters: dict[str, Any], seconds: int, module_client: Mock,
) -> None:
    with pytest.raises(_Finished):
        _run(op, parameters, timeout=seconds)
    assert module_client.call_args.kwargs["timeout"] == seconds


def test_transport_timeout_is_not_retried_or_reported_as_success(module_client: Mock) -> None:
    module_client.return_value.post.return_value = {"_status": 0, "_error": "request timed out"}
    with pytest.raises(_Refused) as refused:
        _run("feature_done", {"feature_name": "topic"}, timeout=3900)
    assert refused.value.payload["status"] == 0
    assert "timed out" in refused.value.payload["msg"]
    module_client.return_value.post.assert_called_once()
    assert module_client.call_args.kwargs["timeout"] == 3900


def test_user_cancellation_propagates_without_dispatch_retry(module_client: Mock) -> None:
    module_client.return_value.post.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _run("feature_done", {"feature_name": "topic"}, timeout=3900)
    module_client.return_value.post.assert_called_once()


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


@pytest.mark.parametrize("explicit_budget", (None, 100, 4200))
def test_role_gate_calls_select_the_dedicated_budget_without_clamping(explicit_budget: int | None) -> None:
    variables: dict[str, Any] = {}
    if explicit_budget is not None:
        variables["git_gate_api_timeout"] = explicit_budget
    variables.update({"feature_name": "topic", "worktree_branch": "topic", "worktree_path_param": "/owned/topic"})
    found: set[str] = set()
    for operation, options in (("feature_done", {}), *(("worktree", {"worktree_op": op}) for op in
                               ("list", "create", "remove", "merge", "cleanup", "health_check", "merge_all"))):
        for task, templar in role_tasks(ROLE, {**variables, "git_op": operation, **options}):
            if task.action != "general_ludd.agent.gludd_git":
                continue
            call = effective_arguments(task, templar)
            if call["op"] in {case[0] for case in GATE_CASES}:
                assert call["timeout"] == (explicit_budget if explicit_budget is not None else 3900)
                found.add(call["op"])
            else:
                assert call["timeout"] == 300
    assert found == {case[0] for case in GATE_CASES}


def test_collection_timeout_protocol_stays_aligned_without_core_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    assert getattr(gludd_git, "_GATE_TIMEOUT_SECONDS", None) == _gate_seconds(monkeypatch)
    assert getattr(gludd_git, "_DEFAULT_TIMEOUT_SECONDS", None) == 300
    path = ROOT / "collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_git.py"
    source = path.read_text(encoding="utf-8")
    assert "from general_ludd" not in source and "import general_ludd" not in source
