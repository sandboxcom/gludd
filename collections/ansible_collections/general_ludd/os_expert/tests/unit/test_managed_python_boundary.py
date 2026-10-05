"""Managed-host Python boundary tests for the OS expert collection."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

COLLECTION_ROOT = Path(__file__).resolve().parents[2]
ROLES_ROOT = COLLECTION_ROOT / "roles"
PREFLIGHT_FQCN = "general_ludd.os_expert.managed_python_preflight"
PYTHON_BACKEND_TASKS = (
    "android_diagnose/tasks/main.yml",
    "android_security/tasks/main.yml",
    "ios_diagnose/tasks/main.yml",
    "ios_security/tasks/main.yml",
    "linux_automation/tasks/main.yml",
    "linux_diagnose/tasks/main.yml",
    "linux_kernel/tasks/main.yml",
    "macos_automation/tasks/main.yml",
    "macos_diagnose/tasks/main.yml",
    "macos_security/tasks/main.yml",
    "windows_automation/tasks/main.yml",
)
COMMAND_KEYS = {
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "command",
    "shell",
}
AMBIENT_PYTHON = re.compile(r"(?<![./\w])python3(?=\s|$)")
BOUNDARY_AMBIENT_PYTHON = re.compile(
    r"(?:^|[\s:'\"=])"
    r"(?:/usr/bin/python3?|/usr/local/bin/python3?|python3?|py)"
    r"(?:\s|$)"
)


def _load_yaml(relative_path: str) -> Any:
    return yaml.safe_load((ROLES_ROOT / relative_path).read_text(encoding="utf-8"))


def _commands(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in COMMAND_KEYS:
                yield key, nested
            yield from _commands(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _commands(nested)


def _run_local_playbook(
    tmp_path: Path,
    tasks: list[dict[str, Any]],
) -> subprocess.CompletedProcess[str]:
    playbook = tmp_path / "preflight.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Exercise OS expert Python boundary",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "tasks": tasks,
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["ANSIBLE_COLLECTIONS_PATH"] = str(COLLECTION_ROOT.parents[2])
    environment["ANSIBLE_LOCAL_TEMP"] = str(tmp_path / "ansible-local")
    return subprocess.run(
        [
            str(Path(sys.executable).with_name("ansible-playbook")),
            "-i",
            "localhost,",
            str(playbook),
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=60,
    )


def test_python_backends_have_no_ambient_interpreter_commands() -> None:
    findings: list[str] = []
    for relative_path in PYTHON_BACKEND_TASKS:
        for _module, command in _commands(_load_yaml(relative_path)):
            if AMBIENT_PYTHON.search(yaml.safe_dump(command)):
                findings.append(relative_path)

    assert findings == []


def test_each_python_backend_preflights_before_any_role_mutation() -> None:
    for relative_path in PYTHON_BACKEND_TASKS:
        tasks = _load_yaml(relative_path)
        first_task = tasks[0]
        include = first_task["ansible.builtin.include_role"]

        assert include["name"] == PREFLIGHT_FQCN
        assert include["public"] is False
        assert first_task["vars"]["os_expert_python_preflight_contract"]
        assert "_os_expert_python_runtime.executable" in yaml.safe_dump(tasks)


def test_each_python_backend_executes_with_structured_argv() -> None:
    for relative_path in PYTHON_BACKEND_TASKS:
        contracted_commands = [
            (module, command)
            for module, command in _commands(_load_yaml(relative_path))
            if "_os_expert_python_runtime.executable" in yaml.safe_dump(command)
        ]

        assert len(contracted_commands) == 1
        module, command = contracted_commands[0]
        assert module == "ansible.builtin.command"
        assert isinstance(command, dict)
        assert "argv" in command


def test_target_preflights_expose_discovered_and_managed_contracts() -> None:
    for relative_path in PYTHON_BACKEND_TASKS[:-1]:
        first_task = _load_yaml(relative_path)[0]
        role_vars = first_task["vars"]

        assert role_vars["os_expert_python_preflight_contract"] == (
            "{{ os_expert_python_contract | default('discovered') }}"
        )
        assert role_vars["os_expert_python_preflight_managed_interpreter"] == (
            "{{ os_expert_managed_python_interpreter | default('') }}"
        )


def test_windows_controller_parser_uses_playbook_python_explicitly() -> None:
    first_task = _load_yaml("windows_automation/tasks/main.yml")[0]
    include = first_task["ansible.builtin.include_role"]

    assert include["apply"]["delegate_to"] == "localhost"
    assert first_task["run_once"] is True
    assert first_task["vars"] == {
        "os_expert_python_preflight_contract": "managed",
        "os_expert_python_preflight_managed_interpreter": (
            "{{ ansible_playbook_python }}"
        ),
    }


def test_private_preflight_contract_is_fail_closed_and_read_only() -> None:
    tasks = _load_yaml("managed_python_preflight/tasks/main.yml")
    serialized = yaml.safe_dump(tasks)

    assert "ansible_facts.python.executable" in serialized
    assert "os_expert_python_preflight_managed_interpreter" in serialized
    assert "_os_expert_python_runtime" in serialized
    assert "ignore_errors" not in serialized
    assert "failed_when: false" not in serialized
    for mutable_module in (
        "ansible.builtin.copy",
        "ansible.builtin.file",
        "ansible.builtin.package",
        "ansible.builtin.pip",
    ):
        assert mutable_module not in serialized


def test_private_preflight_argument_spec_constrains_contract() -> None:
    specs = _load_yaml("managed_python_preflight/meta/argument_specs.yml")
    options = specs["argument_specs"]["main"]["options"]

    assert options["os_expert_python_preflight_contract"]["choices"] == [
        "discovered",
        "managed",
    ]
    assert options["os_expert_python_preflight_contract"]["required"] is True
    assert options["os_expert_python_preflight_managed_interpreter"]["type"] == "str"


def test_collection_has_no_ambient_python_interpreter_fallback() -> None:
    findings: list[str] = []
    for path in sorted(ROLES_ROOT.rglob("tasks/*")):
        if not path.is_file() or path.suffix not in {".yml", ".yaml"}:
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if BOUNDARY_AMBIENT_PYTHON.search(line):
                relative = path.relative_to(COLLECTION_ROOT).as_posix()
                findings.append(f"{relative}:{line_number}")

    assert findings == []


def test_preflight_resolves_discovered_and_managed_interpreters(
    tmp_path: Path,
) -> None:
    include = {
        "ansible.builtin.include_role": {
            "name": PREFLIGHT_FQCN,
            "public": False,
            "rolespec_validate": True,
        }
    }
    result = _run_local_playbook(
        tmp_path,
        [
            {
                "name": "Resolve discovered Python",
                **include,
                "vars": {
                    "os_expert_python_preflight_contract": "discovered",
                    "os_expert_python_preflight_managed_interpreter": "",
                },
            },
            {
                "name": "Require discovered Python result",
                "ansible.builtin.assert": {
                    "that": [
                        "_os_expert_python_runtime.contract == 'discovered'",
                        "_os_expert_python_runtime.executable | length > 0",
                    ]
                },
            },
            {
                "name": "Resolve managed Python",
                **include,
                "vars": {
                    "os_expert_python_preflight_contract": "managed",
                    "os_expert_python_preflight_managed_interpreter": sys.executable,
                },
            },
            {
                "name": "Require managed Python result",
                "ansible.builtin.assert": {
                    "that": [
                        "_os_expert_python_runtime.contract == 'managed'",
                        (
                            "_os_expert_python_runtime.executable == "
                            "os_expert_expected_python"
                        ),
                    ]
                },
                "vars": {"os_expert_expected_python": sys.executable},
            },
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_failed_preflight_leaves_candidate_artifact_absent(tmp_path: Path) -> None:
    candidate_artifact = tmp_path / "candidate-artifact"
    result = _run_local_playbook(
        tmp_path,
        [
            {
                "name": "Reject a missing managed interpreter",
                "ansible.builtin.include_role": {
                    "name": PREFLIGHT_FQCN,
                    "public": False,
                    "rolespec_validate": True,
                },
                "vars": {
                    "os_expert_python_preflight_contract": "managed",
                    "os_expert_python_preflight_managed_interpreter": (
                        "/opt/gludd/missing-runtime/bin/python"
                    ),
                },
            },
            {
                "name": "Mutation that must never run",
                "ansible.builtin.file": {
                    "path": str(candidate_artifact),
                    "state": "touch",
                },
            },
        ],
    )

    assert result.returncode != 0
    assert not candidate_artifact.exists()


def test_collection_declares_external_role_runtime_dependencies() -> None:
    galaxy = yaml.safe_load(
        (COLLECTION_ROOT / "galaxy.yml").read_text(encoding="utf-8")
    )

    assert galaxy["dependencies"]["community.general"] == ">=8.0.0"
