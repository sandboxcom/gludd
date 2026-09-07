"""Contracts for Gludd-owned Podman and execution-environment bootstrap."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "roles"
    / "execution_environment_bootstrap"
)
PLAYBOOK = ROOT / "playbooks" / "bootstrap_execution_environment.yml"
MOLECULE = ROOT / "molecule" / "playbooks" / "role_execution_environment_bootstrap"


def _yaml(relative: str) -> Any:
    return yaml.safe_load((ROLE / relative).read_text(encoding="utf-8"))


def _tasks(relative: str) -> list[dict[str, Any]]:
    value = _yaml(relative)
    assert isinstance(value, list)
    return value


def _serialized(relative: str) -> str:
    rendered = yaml.safe_dump(_yaml(relative), sort_keys=True)
    assert isinstance(rendered, str)
    return rendered


def _command_argv(relative: str) -> list[list[str]]:
    commands: list[list[str]] = []

    def walk(tasks: list[dict[str, Any]]) -> None:
        for task in tasks:
            command = task.get("ansible.builtin.command")
            if isinstance(command, dict) and isinstance(command.get("argv"), list):
                commands.append([str(item) for item in command["argv"]])
            for section in ("block", "rescue", "always"):
                nested = task.get(section)
                if isinstance(nested, list):
                    walk(nested)

    walk(_tasks(relative))
    return commands


def test_role_has_complete_lifecycle_surface() -> None:
    expected = {
        "README.md",
        "defaults/main.yml",
        "meta/main.yml",
        "tasks/main.yml",
        "tasks/validate.yml",
        "tasks/install.yml",
        "tasks/machine.yml",
        "tasks/build.yml",
        "tasks/verify.yml",
        "tasks/absent.yml",
    }
    assert expected <= {
        str(path.relative_to(ROLE)) for path in ROLE.rglob("*") if path.is_file()
    }


def test_molecule_scenario_exercises_non_mutating_present_and_absent_plans() -> None:
    molecule = (MOLECULE / "molecule.yml").read_text(encoding="utf-8")
    converge = (MOLECULE / "default/converge.yml").read_text(encoding="utf-8")
    verify = (MOLECULE / "default/verify.yml").read_text(encoding="utf-8")
    assert "idempotence" in molecule
    assert "general_ludd.agent.execution_environment_bootstrap" in converge
    assert "execution_environment_bootstrap_validate_only: true" in converge
    assert "execution_environment_bootstrap_state: present" in converge
    assert "execution_environment_bootstrap_state: absent" in verify
    assert "requested_state == 'present'" in converge
    assert "requested_state == 'absent'" in verify


def test_role_uses_the_non_deprecated_ansible_facts_namespace() -> None:
    role_text = "\n".join(
        path.read_text(encoding="utf-8") for path in ROLE.rglob("*.yml")
    )
    assert "ansible_system" not in role_text
    assert "ansible_env" not in role_text
    assert "ansible_facts['system']" in role_text
    assert "ansible_facts['env']" in role_text


def test_defaults_are_namespaced_bounded_and_zdd_safe() -> None:
    defaults = _yaml("defaults/main.yml")
    assert defaults["execution_environment_bootstrap_state"] == "present"
    assert defaults["execution_environment_bootstrap_validate_only"] is False
    assert defaults["execution_environment_bootstrap_install_podman"] is True
    machine_name = defaults["execution_environment_bootstrap_machine_name"]
    assert machine_name.startswith("gludd-ee-")
    assert "hash('sha256')" in machine_name
    assert defaults["execution_environment_bootstrap_machine_cpus"] >= 1
    assert defaults["execution_environment_bootstrap_machine_memory_mb"] >= 2048
    assert defaults["execution_environment_bootstrap_machine_disk_gb"] >= 10
    assert defaults["execution_environment_bootstrap_remove_image_on_absent"] is True
    assert defaults["execution_environment_bootstrap_ephemeral"] is False
    assert defaults["execution_environment_bootstrap_container_runtime"] == "podman"
    assert defaults["execution_environment_bootstrap_image"].startswith("localhost/")
    assert "execution_environment_bootstrap_machine_name" in defaults["execution_environment_bootstrap_image"]
    assert ":latest" not in defaults["execution_environment_bootstrap_image"]
    assert "/gludd/" in defaults["execution_environment_bootstrap_state_root"]
    assert "execution_environment_bootstrap_machine_name" in defaults["execution_environment_bootstrap_state_root"]
    assert defaults["execution_environment_bootstrap_cleanup_on_failure"] is True


def test_validation_fails_closed_on_ownership_and_resource_bounds() -> None:
    text = _serialized("tasks/validate.yml")
    for contract in (
        "execution_environment_bootstrap_state in ['present', 'absent']",
        "execution_environment_bootstrap_machine_name is match('^gludd-[a-z0-9][a-z0-9-]{2,62}$')",
        "execution_environment_bootstrap_machine_cpus | int <= 16",
        "execution_environment_bootstrap_machine_memory_mb | int <= 65536",
        "execution_environment_bootstrap_machine_disk_gb | int <= 256",
        "execution_environment_bootstrap_state_root is match",
        "execution_environment_bootstrap_project_root | length > 0",
    ):
        assert contract in text
    assert "fail closed" in text.lower()


def test_install_uses_os_package_manager_and_pinned_official_macos_installer() -> None:
    text = _serialized("tasks/install.yml")
    defaults_text = _serialized("defaults/main.yml")
    assert "ansible.builtin.package" in text
    assert "ansible.builtin.get_url" in text
    assert "ansible.builtin.command" in text
    assert "podman-installer-macos-universal.pkg" in defaults_text
    assert "github.com/containers/podman/releases/download/v5.8.2" in defaults_text
    assert "sha256:17992d3fc5a9dc7bd6f591d1c5f5dcf13e064cc2e672e9cd456d4a5707ff97e1" in defaults_text
    assert "/usr/sbin/installer" in text
    assert "community.general.homebrew" not in text
    assert "curl" not in text


def test_machine_lifecycle_is_exactly_named_connection_scoped_and_observable() -> None:
    text = _serialized("tasks/machine.yml")
    tasks = _tasks("tasks/machine.yml")
    commands = _command_argv("tasks/machine.yml")
    assert any(argv[1:3] == ["machine", "inspect"] for argv in commands)
    assert any(argv[1:3] == ["machine", "init"] for argv in commands)
    assert any(argv[1:3] == ["machine", "start"] for argv in commands)
    assert any(argv[1:4] == ["system", "connection", "default"] for argv in commands)
    assert "execution_environment_bootstrap_machine_name" in text
    assert "_execution_environment_bootstrap_tool_environment" in text
    assert "CONTAINER_CONNECTION" in _serialized("tasks/validate.yml")
    assert "async_status" in text
    assert "until" in text
    assert "retries" in text
    assert "bootstrap heartbeat" in text.lower()
    assert "podman machine init\n" not in text
    assert any("block" in task and "always" in task for task in tasks)


def test_build_reuses_canonical_builder_driver_without_make_or_shell() -> None:
    text = _serialized("tasks/build.yml")
    assert "scripts/ansible_runtime_artifacts.py" in text
    assert "--runtime" in text
    assert "--image" in text
    assert "--context" in text
    assert "_execution_environment_bootstrap_tool_environment" in text
    assert "CONTAINER_CONNECTION" in _serialized("tasks/validate.yml")
    assert "ansible.builtin.async_status" in text
    assert "ANSIBLE_EE_BUILD_HEARTBEAT" in text
    assert "ansible.builtin.shell" not in text
    assert not re.search(r"(^|\s)make(\s|$)", text)


def test_verify_is_network_isolated_and_checks_opentofu_and_ansible() -> None:
    text = _serialized("tasks/verify.yml")
    commands = _command_argv("tasks/verify.yml")
    assert any(argv[1:3] == ["image", "inspect"] for argv in commands)
    assert "--network=none" in text
    assert "/usr/local/bin/tofu" in text
    assert "import ansible, ansible_runner" in text
    assert "_execution_environment_bootstrap_tool_environment" in text
    assert "CONTAINER_CONNECTION" in _serialized("tasks/validate.yml")
    assert "gludd_execution_environment" in text
    assert "verified" in text


def test_absent_only_removes_owned_image_context_and_machine() -> None:
    text = _serialized("tasks/absent.yml")
    commands = _command_argv("tasks/absent.yml")
    assert any(argv[1:3] == ["image", "rm"] for argv in commands)
    assert "state: absent" in text
    assert any(argv[1:3] == ["machine", "stop"] for argv in commands)
    assert any(argv[1:3] == ["machine", "rm"] for argv in commands)
    assert "execution_environment_bootstrap_machine_name" in text
    assert "_execution_environment_bootstrap_candidate_context" in text
    assert "_execution_environment_bootstrap_tool_environment" in text
    assert "CONTAINER_CONNECTION" in _serialized("tasks/validate.yml")
    assert "prune" not in text.lower()
    assert "system reset" not in text.lower()


def test_main_routes_validate_present_absent_and_ephemeral_cleanup() -> None:
    text = _serialized("tasks/main.yml")
    assert "validate.yml" in text
    assert "install.yml" in text
    assert "machine.yml" in text
    assert "build.yml" in text
    assert "verify.yml" in text
    assert "absent.yml" in text
    assert "execution_environment_bootstrap_validate_only" in text
    assert "execution_environment_bootstrap_ephemeral" in text
    assert "execution_environment_bootstrap_cleanup_on_failure" in text
    assert "rescue" in text


def test_controller_playbook_exposes_role_without_make() -> None:
    playbook = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    assert len(playbook) == 1
    play = playbook[0]
    assert play["hosts"] == "localhost"
    assert play["connection"] == "local"
    assert play["gather_facts"] is True
    role = play["roles"][0]
    assert role["role"] == "general_ludd.agent.execution_environment_bootstrap"
    assert "playbook_dir" in str(role["vars"]["execution_environment_bootstrap_project_root"])
    assert play["vars"]["ansible_python_interpreter"] == "{{ ansible_playbook_python }}"


def test_bootstrap_has_no_external_collection_dependency_cycle() -> None:
    requirements = yaml.safe_load(
        (ROOT / "config/ansible/requirements.yml").read_text(encoding="utf-8")
    )["collections"]
    assert all(item["name"] != "containers.podman" for item in requirements)
    galaxy = yaml.safe_load(
        (
            ROOT
            / "collections/ansible_collections/general_ludd/agent/galaxy.yml"
        ).read_text(encoding="utf-8")
    )
    assert "containers.podman" not in galaxy["dependencies"]
    role_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROLE / "tasks").glob("*.yml")
    )
    assert "containers.podman" not in role_text


def test_docs_record_official_guidance_and_long_lived_user_reports() -> None:
    docs = (ROLE / "README.md").read_text(encoding="utf-8")
    for marker in (
        "Podman Installation",
        "Ansible Builder",
        "AWX EE",
        "CONTAINER_CONNECTION",
        "forum.ansible.com",
        "reddit.com/r/ansible",
        "Podman machine",
        "OpenTofu",
        "Azure Python SDK",
        "only required host executable",
        "zero-downtime",
    ):
        assert marker in docs


def test_host_boundary_never_requires_az_terraform_or_tofu() -> None:
    commands = [
        argv
        for task_file in (ROLE / "tasks").glob("*.yml")
        for argv in _command_argv(str(task_file.relative_to(ROLE)))
    ]
    assert commands
    forbidden_host_executables = {"az", "terraform", "tofu", "/usr/local/bin/tofu"}
    assert all(argv[0] not in forbidden_host_executables for argv in commands)


def test_managed_python_can_invoke_ansible_builder_without_a_host_wrapper() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ansible_builder", "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[WARNING]" not in result.stderr
    assert "3." in result.stdout


@pytest.mark.e2e
def test_validate_only_playbook_runs_end_to_end_without_host_mutation() -> None:
    executable = shutil.which("ansible-playbook")
    assert executable is not None
    env = os.environ.copy()
    env["ANSIBLE_COLLECTIONS_PATH"] = str(ROOT / "collections")
    result = subprocess.run(
        [
            executable,
            "-i",
            "localhost,",
            "-c",
            "local",
            str(PLAYBOOK),
            "-e",
            json.dumps({"execution_environment_bootstrap_validate_only": True}),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ANSIBLE_EE_BOOTSTRAP_PHASE" in result.stdout
    assert "changed=0" in result.stdout
    assert "failed=0" in result.stdout


@pytest.mark.e2e
def test_present_and_absent_lifecycle_runs_with_isolated_engine_contract(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state_root = tmp_path / "gludd" / "state"
    runtime_lock = project / "config" / "ansible" / "runtime-lock.json"
    driver = project / "scripts" / "ansible_runtime_artifacts.py"
    podman = tmp_path / "podman"
    engine_state = tmp_path / "engine-state"
    engine_log = tmp_path / "engine-log.jsonl"
    runtime_lock.parent.mkdir(parents=True)
    driver.parent.mkdir(parents=True)
    runtime_lock.write_text('{"schema_version": 1}\n', encoding="utf-8")
    driver.write_text(
        "import sys\nprint('ANSIBLE_EE_BUILD_START fake=1', flush=True)\nsys.exit(0)\n",
        encoding="utf-8",
    )
    podman.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import pathlib
            import sys

            args = sys.argv[1:]
            log = pathlib.Path({str(engine_log)!r})
            marker = pathlib.Path({str(engine_state)!r})
            with log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(args) + "\\n")
            if args[:1] == ["version"]:
                print('{{"Version":"5.8.2"}}')
            elif args[:3] == ["system", "connection", "list"]:
                print('[{{"Name":"another-project","Default":true}}]')
            elif args[:2] == ["machine", "inspect"]:
                if not marker.exists():
                    raise SystemExit(125)
                print('[{{"Name":"gludd-e2e-builder","State":"running"}}]')
            elif args[:2] == ["machine", "init"]:
                marker.touch()
            elif args[:2] == ["machine", "start"]:
                marker.touch()
            elif args[:2] == ["machine", "rm"]:
                marker.unlink(missing_ok=True)
            elif args[:2] == ["image", "inspect"]:
                print('[{{"Id":"' + ('a' * 64) + '"}}]')
            elif args[:2] == ["run", "--rm"] and "/usr/local/bin/tofu" in args:
                print("OpenTofu v1.12.6")
            elif args[:2] == ["run", "--rm"]:
                print("ANSIBLE_EE_IMPORT_OK")
            """
        ),
        encoding="utf-8",
    )
    podman.chmod(0o755)
    executable = shutil.which("ansible-playbook")
    assert executable is not None
    env = os.environ.copy()
    env["ANSIBLE_COLLECTIONS_PATH"] = str(ROOT / "collections")
    common_vars: dict[str, Any] = {
        "execution_environment_bootstrap_project_root": str(project),
        "execution_environment_bootstrap_state_root": str(state_root),
        "execution_environment_bootstrap_machine_name": "gludd-e2e-builder",
        "execution_environment_bootstrap_podman_executable": str(podman),
        "execution_environment_bootstrap_machine_start_timeout_seconds": 30,
        "execution_environment_bootstrap_build_timeout_seconds": 300,
        "execution_environment_bootstrap_poll_seconds": 1,
    }

    def run(state: str) -> subprocess.CompletedProcess[str]:
        extra_vars = {**common_vars, "execution_environment_bootstrap_state": state}
        return subprocess.run(
            [
                executable,
                "-i",
                "localhost,",
                "-c",
                "local",
                str(PLAYBOOK),
                "-e",
                json.dumps(extra_vars),
            ],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    present = run("present")
    assert present.returncode == 0, present.stdout + present.stderr
    assert "[WARNING]" not in present.stderr
    assert "OpenTofu v1.12.6" in present.stdout
    assert "ANSIBLE_EE_IMPORT_OK" in present.stdout
    assert '"verified": true' in present.stdout
    assert engine_state.exists()

    absent = run("absent")
    assert absent.returncode == 0, absent.stdout + absent.stderr
    assert "[WARNING]" not in absent.stderr
    assert '"state": "absent"' in absent.stdout
    assert not engine_state.exists()
    calls = [json.loads(line) for line in engine_log.read_text(encoding="utf-8").splitlines()]
    for expected in (["machine", "init"], ["image", "inspect"], ["image", "rm"], ["machine", "rm"]):
        assert any(call[:2] == expected for call in calls)

    driver.write_text(
        "import sys\nprint('ANSIBLE_EE_BUILD_START fake_failure=1', flush=True)\nsys.exit(7)\n",
        encoding="utf-8",
    )
    failed = run("present")
    assert failed.returncode != 0
    assert "[WARNING]" not in failed.stderr
    assert "ANSIBLE_EE_BOOTSTRAP_FAILED" in failed.stdout
    assert "Clean exact Gludd-owned resources after a failed candidate" in failed.stdout
    assert not engine_state.exists()
