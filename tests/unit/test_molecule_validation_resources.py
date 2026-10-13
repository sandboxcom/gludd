"""Bounded native validation and owned-grandchild cleanup regression contracts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import check_molecule_yaml as checker
    import stream_command as owner
else:
    from scripts import check_molecule_yaml as checker
    from scripts import stream_command as owner


def test_native_lint_uses_existing_bounded_owner_and_restores_private_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = tmp_path / "collections/ansible_collections/example/sample/roles/demo/tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text("- name: Example\n  ansible.builtin.debug: {msg: valid}\n")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "validate_action_option_names", lambda _paths: None)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 124))
    original_environment = dict(os.environ)
    original_directory = Path.cwd()
    observed: dict[str, object] = {}

    def bounded(command: list[str], log_path: Path, **kwargs: object) -> int:
        observed.update(
            command=command, log_path=log_path, cwd=Path.cwd(),
            ansible_home=os.environ["ANSIBLE_HOME"], **kwargs,
        )
        assert os.environ["PYTHON_CPU_COUNT"] == "8"
        assert log_path.parent == Path.cwd()
        assert Path.cwd().name.startswith("gludd-molecule-yaml-")
        return 124

    monkeypatch.setattr(owner, "stream_command", bounded)
    assert checker.main() == 1
    assert observed, "native lint must route through the existing process-group owner"
    assert observed["max_seconds"] == 45
    assert observed["heartbeat_seconds"] == 10
    assert Path(str(observed["ansible_home"])).name == "ansible-home"
    command = observed["command"]
    assert isinstance(command, list)
    assert command[:3] == [sys.executable, "-X", "cpu_count=8"]
    assert "--offline" in command and "--nocolor" in command
    assert Path.cwd() == original_directory
    assert dict(os.environ) == original_environment
    assert not Path(str(observed["cwd"])).exists()


@pytest.mark.parametrize("workers", (0, 9))
def test_native_worker_preflight_refuses_unsafe_capacity_before_lint(
    tmp_path: Path, workers: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy

    from ansiblelint import runner

    bootstrap = getattr(checker, "LINT_BOOTSTRAP", None)
    assert isinstance(bootstrap, str), "native lint needs an in-child capacity admission"
    paths = []
    for index in range(9):
        path = tmp_path / f"playbook-{index}.yml"
        path.write_text("- hosts: localhost\n  gather_facts: false\n  tasks: []\n")
        paths.append(str(path))
    monkeypatch.setattr(sys, "argv", ["-c", "--", *paths])
    monkeypatch.setattr(runner, "threads", lambda: workers)
    calls: list[str] = []
    monkeypatch.setattr(runpy, "run_module", lambda module, **_kwargs: calls.append(module))
    with pytest.raises(SystemExit):
        exec(compile(bootstrap, "<native-lint-admission>", "exec"), {})
    assert not calls


def test_native_worker_preflight_keeps_the_real_cli_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy

    from ansiblelint import runner

    bootstrap = getattr(checker, "LINT_BOOTSTRAP", None)
    assert isinstance(bootstrap, str)
    path = tmp_path / "playbook.yml"
    path.write_text("- hosts: localhost\n  gather_facts: false\n  tasks: []\n")
    monkeypatch.setattr(sys, "argv", ["-c", "--", str(path)])
    monkeypatch.setattr(runner, "threads", lambda: 8)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        runpy, "run_module", lambda module, **kwargs: calls.append((module, kwargs["run_name"])),
    )
    exec(compile(bootstrap, "<native-lint-admission>", "exec"), {})
    assert calls == [("ansiblelint", "__main__")]


def test_old_python_large_cpu_count_does_not_reject_a_small_native_workload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy

    from ansiblelint import runner

    bootstrap = getattr(checker, "LINT_BOOTSTRAP", None)
    assert isinstance(bootstrap, str)
    path = tmp_path / "playbook.yml"
    path.write_text("- hosts: localhost\n  gather_facts: false\n  tasks: []\n")
    monkeypatch.setattr(sys, "argv", ["-c", "--", str(path)])
    monkeypatch.setattr(runner, "threads", lambda: 64)
    calls: list[str] = []
    monkeypatch.setattr(runpy, "run_module", lambda module, **_kwargs: calls.append(module))
    exec(compile(bootstrap, "<native-lint-admission>", "exec"), {})
    assert calls == ["ansiblelint"]
