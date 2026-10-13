"""Exercise the one advisory model owner with native Ansible task arguments."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.task import Task
from ansible.template import Templar, trust_as_template

REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROLES = SOURCE_ROOT / "collections/ansible_collections/general_ludd/agent/roles"
MODEL_MODULE = (
    REPOSITORY
    / "collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_model_call.py"
)
ROLES_AND_PREFIXES = (("enforcement_gate", "_eg"), ("enforcement_verify", "_ev"))


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


def _tasks(loader: DataLoader, source: Path) -> list[dict[str, object]]:
    loaded: object = loader.load_from_file(str(source))
    assert isinstance(loaded, list)
    return cast(list[dict[str, object]], loaded)


def _owner(
    loader: DataLoader, role: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    source = ROLES / role / "tasks/main.yml"
    tasks = _tasks(loader, source)
    assert not any("general_ludd.agent.gludd_model_call" in task for task in tasks)
    includes = [
        task for task in tasks
        if "ansible.builtin.include_tasks" in task
        and "_enforcement_analysis_prompt" in cast(dict[str, object], task.get("vars", {}))
    ]
    assert len(includes) == 1, f"{role} must reach the shared model owner exactly once"
    include = includes[0]
    reference = include["ansible.builtin.include_tasks"]
    assert isinstance(reference, str)
    owner_path = (source.parent / reference).resolve()
    assert owner_path == (ROLES / "enforcement_gate/tasks/model_analysis.yml").resolve()
    owner_tasks = _tasks(loader, owner_path)
    assert len(owner_tasks) == 1
    call = owner_tasks[0]
    assert "general_ludd.agent.gludd_model_call" in call
    extraction = next(task for task in tasks if task["name"] == "Extract model analysis text")
    assert tasks.index(include) < tasks.index(extraction)
    return include, call, extraction


def _variables(role: str, profile: str, psk: str) -> dict[str, object]:
    return {
        "model_profile": profile, "analysis_task_type": role,
        "daemon_url": "http://daemon.invalid:8000", "psk": psk,
        "omit": "__gludd_enforcement_omit__", "enable_model_call": True,
        "_eg_gate_complete": True, "_eg_gate_passed": False,
        "_eg_gate_fresh": True, "_eg_gate_age_seconds": 12,
        "_eg_push_bypass_allowed": False, "_eg_push_bypass_count": 3,
        "_ev_verdict": "FAIL", "_ev_silent_mechanisms": ["watchdog"],
        "_ev_healthy_count": 2, "_ev_total_count": 3,
    }


def _arguments(task: Task, templar: Templar) -> dict[str, object]:
    return {
        key: templar.template(trust_as_template(value)) for key, value in task.args.items()
    }


@pytest.mark.parametrize(("role", "prefix"), ROLES_AND_PREFIXES)
@pytest.mark.parametrize("profile", ("", "explicit-profile"))
@pytest.mark.parametrize("psk", ("", "gludd-regression-psk"))
def test_shared_model_owner_templates_and_validates_real_module_arguments(
    role: str, prefix: str, profile: str, psk: str,
) -> None:
    loader = DataLoader()
    include, call, extraction = _owner(loader, role)
    variables = _variables(role, profile, psk)
    variables.update({
        key: trust_as_template(value)
        for key, value in cast(dict[str, object], include["vars"]).items()
    })
    templar = Templar(loader=loader, variables=variables)
    task = Task.load(call, loader=loader)
    args = {
        key: value for key, value in _arguments(task, templar).items()
        if value != variables["omit"]
    }
    assert ("model_profile" in args) != ("route_task_type" in args)
    assert args["model_profile" if profile else "route_task_type"] == (profile or role)
    assert args["daemon_url"] == variables["daemon_url"]
    assert args["psk"] == psk
    assert templar.template(trust_as_template(task.no_log)) is bool(psk)
    assert call["failed_when"] is False
    assert call["register"] == "_enforcement_model_analysis"
    prompt = args["prompt"]
    assert isinstance(prompt, str) and role in prompt
    assert "advisory" in prompt and "deterministic checks own" in prompt
    if prefix == "_eg":
        for fragment in (
            "complete=True", "passed=False", "fresh=True", "age_seconds=12",
            "allowed=False", "bypass count=3",
        ):
            assert fragment in prompt
    else:
        for fragment in ("Verdict=FAIL", '"watchdog"', "healthy count=2", "total count=3"):
            assert fragment in prompt
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY / "collections")
    result = subprocess.run(
        [sys.executable, "-W", "error", str(MODEL_MODULE)],
        input=json.dumps({"ANSIBLE_MODULE_ARGS": {**args, "_ansible_check_mode": True}}),
        cwd=REPOSITORY, env=environment, capture_output=True, text=True,
        check=False, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    native_result = json.loads(result.stdout)
    assert native_result["text"] == "[check-mode: model call skipped]"
    assert native_result["changed"] is False
    variables["_enforcement_model_analysis"] = loader.load(json.dumps(native_result))
    facts = Task.load(extraction, loader=loader)
    fact_args = _arguments(facts, Templar(loader=loader, variables=variables))
    result_alias = fact_args[f"{prefix}_model_analysis"]
    assert isinstance(result_alias, dict)
    assert result_alias["text"] == native_result["text"]
    assert fact_args[f"{prefix}_model_response"] == native_result["text"]
    assert fact_args[f"{prefix}_model_confidence"] == 0.0


@pytest.mark.parametrize(("role", "prefix"), ROLES_AND_PREFIXES)
@pytest.mark.parametrize("enabled", (False, True))
def test_optional_analysis_condition_controls_include_and_role_result(
    role: str, prefix: str, enabled: bool,
) -> None:
    loader = DataLoader()
    include, _call, extraction = _owner(loader, role)
    variables = {"enable_model_call": enabled}
    templar = Templar(loader=loader, variables=variables)
    for source in (include, extraction):
        Task.load(source, loader=loader)
        condition = trust_as_template(cast(str, source["when"]))
        assert templar.evaluate_conditional(condition) is enabled
    assert prefix in str(extraction["ansible.builtin.set_fact"])


@pytest.mark.parametrize(("role", "prefix"), ROLES_AND_PREFIXES)
@pytest.mark.parametrize("native_result", (
    {"text": "advisory explanation", "confidence": 0.625, "changed": True},
    {"failed": True, "msg": "daemon unreachable", "changed": False},
))
def test_role_result_preserves_native_text_and_advisory_failure_defaults(
    role: str, prefix: str, native_result: dict[str, object],
) -> None:
    loader = DataLoader()
    _include, _call, extraction = _owner(loader, role)
    variables = {
        "_enforcement_model_analysis": loader.load(json.dumps(native_result)),
        "enable_model_call": True,
    }
    task = Task.load(extraction, loader=loader)
    args = _arguments(task, Templar(loader=loader, variables=variables))
    assert args[f"{prefix}_model_analysis"] == native_result
    assert args[f"{prefix}_model_response"] == native_result.get("text", "")
    assert args[f"{prefix}_model_confidence"] == native_result.get("confidence", 0.0)
    assert all(str(key).startswith(f"{prefix}_model_") for key in args)
