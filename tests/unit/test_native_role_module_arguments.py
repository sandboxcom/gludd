"""Exercise native Ansible argument validation against the repaired role sources."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from tests.unit.native_role_tasks import effective_arguments, role_tasks

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS = ROOT / "collections/ansible_collections/general_ludd"
GROUPS = {
    "account_lifecycle": ("agent/roles/account_lifecycle/tasks/main.yml",),
    "enforcement_gate": (
        "agent/roles/enforcement_gate/tasks/main.yml", "agent/roles/enforcement_gate/tasks/model_analysis.yml",
    ),
    "enforcement_verify": (
        "agent/roles/enforcement_verify/tasks/main.yml", "agent/roles/enforcement_gate/tasks/model_analysis.yml",
    ),
    "git_automation": (
        *(f"agent/roles/git_automation/tasks/{name}.yml" for name in (
            "main", "assert_ref", "git_request", "required_value", "ref_request", "required_request",
            "guarded_list", "ref_result",
            "clone", "commit", "push", "merge", "branch", "state",
            "batch_push", "verify_remote", "release_delete", "ci_verdict", "ci_cancel",
            "feature_start", "feature_done", "rebranch_onto", "release_cut",
            "release_recut", "ship_commit", "worktree",
        )),
        "agent/roles/git_automation/molecule/default/verify.yml",
    ),
    "ssl_cert": ("security/roles/ssl_cert/tasks/chain_research.yml",),
}
BOOTSTRAP = (
    "import runpy, sys; from pathlib import Path; "
    "namespace = runpy.run_path(sys.argv[1]); main = namespace['main']; "
    "main.__globals__['ROOT'] = Path(sys.argv[2]); "
    "raise SystemExit(main(lint_timeout_seconds=120))"
)


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


@pytest.mark.parametrize("group", tuple(GROUPS))
def test_role_calls_pass_installed_native_argument_owners(group: str, tmp_path: Path) -> None:
    """Keep all native args/jinja rules enabled, using actual module implementations."""
    for relative in GROUPS[group]:
        source = COLLECTIONS / relative
        # Task fixtures do not shadow the real general_ludd collection's plugin loader.
        fixture = tmp_path / "collections/regression" / relative
        fixture.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, fixture)
        if group == "git_automation":
            defaults = COLLECTIONS / "agent/roles/git_automation/defaults/main.yml"
            copied = tmp_path / "collections/regression/agent/roles/git_automation/defaults/main.yml"
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(defaults, copied)
    environment = dict(os.environ)
    environment["ANSIBLE_COLLECTIONS_PATH"] = str(ROOT / "collections")
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", BOOTSTRAP,
         str(ROOT / "scripts/check_molecule_yaml.py"), str(tmp_path)],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        check=False, timeout=140,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MOLECULE_VALIDATION_LINT_END rc=0" in result.stdout


def _tasks(relative: str) -> list[dict[str, Any]]:
    loaded: object = yaml.safe_load((COLLECTIONS / relative).read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return cast(list[dict[str, Any]], loaded)


def _model_owner(role: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read both the native include context and its actual shared module task."""
    relative = Path(f"agent/roles/{role}/tasks/main.yml")
    callers = [task for task in _tasks(str(relative))
               if str(task.get("ansible.builtin.include_tasks", "")).endswith("model_analysis.yml")]
    assert len(callers) == 1
    caller = callers[0]
    included = relative.parent / caller["ansible.builtin.include_tasks"]
    owners = [task for task in _tasks(str(included)) if "general_ludd.agent.gludd_model_call" in task]
    assert len(owners) == 1
    return caller, owners[0]


@pytest.mark.parametrize(
    ("role", "context"),
    (
        ("enforcement_gate", (
            "_eg_gate_complete", "_eg_gate_passed", "_eg_gate_fresh", "_eg_gate_age_seconds",
            "_eg_push_bypass_allowed", "_eg_push_bypass_count",
        )),
        ("enforcement_verify", (
            "_ev_verdict", "_ev_silent_mechanisms", "_ev_healthy_count", "_ev_total_count",
        )),
    ),
)
def test_enforcement_prompts_retain_decision_context_and_native_text(
    role: str, context: tuple[str, ...],
) -> None:
    tasks = _tasks(f"agent/roles/{role}/tasks/main.yml")
    caller, call = _model_owner(role)
    parameters = call["general_ludd.agent.gludd_model_call"]
    assert isinstance(parameters, dict)
    prompt = caller["vars"]["_enforcement_analysis_prompt"]
    assert isinstance(prompt, str) and role in prompt
    for field in context:
        assert field in prompt, f"analysis must retain {field}"
    assert "omit" in parameters["model_profile"]
    assert "omit" in parameters["route_task_type"]
    assert parameters["daemon_url"] == "{{ daemon_url }}"
    assert parameters["psk"] == "{{ psk }}"
    assert call["no_log"] == "{{ psk | length > 0 }}"
    extraction = next(task for task in tasks if task["name"] == "Extract model analysis text")
    assert ".text" in str(extraction["ansible.builtin.set_fact"])


def test_account_notification_has_an_explicit_sender() -> None:
    tasks = _tasks("agent/roles/account_lifecycle/tasks/main.yml")
    send = next(task["general_ludd.agent.gludd_message"] for task in tasks
                if "general_ludd.agent.gludd_message" in task)
    assert send["sender"] == "account_lifecycle"
    assert send["recipient"] == "broadcast"


@pytest.mark.parametrize(("branch", "remote", "skip"),
                         (("master", "sandboxcom", False), ("development", "upstream", True)))
def test_release_options_preserve_branch_and_skip_policy_mapping(branch: str, remote: str, skip: bool) -> None:
    for name in ("release_cut", "release_recut"):
        call = next(effective_arguments(task, templar) for task, templar in role_tasks(
            COLLECTIONS / "agent/roles/git_automation",
            {"git_op": name, "release_tag": "v0.12.0", "release_branch": branch, "release_remote": remote,
             "release_skip_readme_check": skip, "release_skip_ci_check": skip},
        ) if task.action == "general_ludd.agent.gludd_git")
        assert call["branch"] == branch
        assert call["release_remote"] == remote
        if name == "release_cut":
            assert call["skip_readme_check"] is skip
            assert call["skip_ci_check"] is skip


def test_feature_completion_keeps_clean_tree_assertion_and_authentication() -> None:
    calls = [effective_arguments(task, templar) for task, templar in role_tasks(
        COLLECTIONS / "agent/roles/git_automation", {"git_op": "feature_done", "feature_name": "topic"},
    ) if task.action == "general_ludd.agent.gludd_git"]
    state = next(call for call in calls if call["op"] == "state")
    assert state["state_assert_clean"] is True
    role = COLLECTIONS / "agent/roles/git_automation"
    for operation in ("feature_start", "feature_done", "rebranch_onto", "release_cut",
                      "release_recut", "ship_commit", "worktree"):
        variables = {"git_op": operation, "feature_name": "topic", "new_base": "abc",
                     "worktree_op": "list", "release_tag": "v0.12.0",
                     "daemon_url": "http://daemon:8000", "psk": "secret"}
        calls = [effective_arguments(task, templar) for task, templar in role_tasks(role, variables)
                 if task.action == "general_ludd.agent.gludd_git"]
        assert calls
        for call in calls:
            assert call["daemon_url"] == "http://daemon:8000"
            assert call["psk"] == "secret"


def test_ssl_research_uses_profile_selection_and_preserves_both_prompts() -> None:
    tasks = _tasks("security/roles/ssl_cert/tasks/chain_research.yml")
    calls = [task["general_ludd.agent.gludd_model_call"] for task in tasks
             if "general_ludd.agent.gludd_model_call" in task]
    assert len(calls) == 2
    for call in calls:
        assert call["model_profile"] == "{{ ssl_cert_model_label }}"
        assert call["prompt"]
        assert call["psk"] == "{{ psk }}"


@pytest.mark.parametrize("role", ("enforcement_gate", "enforcement_verify"))
@pytest.mark.parametrize("profile", ("", "explicit-profile"))
def test_enforcement_routing_templates_choose_exactly_one_selector(
    role: str, profile: str,
) -> None:
    loader = DataLoader()
    caller, owner = _model_owner(role)
    call = owner["general_ludd.agent.gludd_model_call"]
    variables = {
        "model_profile": profile, "analysis_task_type": role, "daemon_url": "http://daemon:8000",
        "psk": "secret", "omit": "__regression_omit_token__",
        "_eg_gate_complete": True, "_eg_gate_passed": False, "_eg_gate_fresh": True,
        "_eg_gate_age_seconds": 12, "_eg_push_bypass_allowed": False, "_eg_push_bypass_count": 3,
        "_ev_verdict": "FAIL", "_ev_silent_mechanisms": ["watchdog"], "_ev_healthy_count": 2, "_ev_total_count": 3,
    }
    variables.update({key: trust_as_template(value) if isinstance(value, str) else value
                      for key, value in caller["vars"].items()})
    templar = Templar(loader=loader, variables=variables)
    templated = {key: templar.template(trust_as_template(value)) for key, value in call.items()}
    rendered = {key: value for key, value in templated.items() if value != variables["omit"]}
    assert ("model_profile" in rendered) != ("route_task_type" in rendered)
    if profile:
        assert rendered["model_profile"] == profile
    else:
        assert rendered["route_task_type"] == role
    assert rendered["psk"] == "secret"
    if role == "enforcement_gate":
        assert "passed=False" in rendered["prompt"] and "bypass count=3" in rendered["prompt"]
    else:
        assert "FAIL" in rendered["prompt"] and "watchdog" in rendered["prompt"]


def test_composite_features_are_reachable_from_the_role_entrypoint() -> None:
    for operation in ("feature_start", "feature_done", "rebranch_onto"):
        includes = [templar.template(task.args["_raw_params"])
                    for task, templar in role_tasks(COLLECTIONS / "agent/roles/git_automation",
                                                  {"git_op": operation}, expand_includes=False)
                    if task.action == "ansible.builtin.include_tasks"]
        assert includes == [f"{operation}.yml"]
