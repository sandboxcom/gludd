"""Real bounded native CLI controls and failing-first included-child coverage."""

from __future__ import annotations

import json
import os
import sys
from contextlib import chdir
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import check_molecule_yaml as checker
    from stream_command import stream_command
else:
    from scripts import check_molecule_yaml as checker
    from scripts.stream_command import stream_command

REPOSITORY = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "fixture-project"
    playbook = project / "molecule" / "default" / "converge.yml"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(
        "- name: Native closure proof\n"
        "  hosts: localhost\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        "    - name: Load task file outside discover patterns\n"
        "      ansible.builtin.include_tasks: ../../shared/late.yml\n",
        encoding="utf-8",
    )
    child = project / "shared" / "late.yml"
    child.parent.mkdir()
    child.write_text(
        "- name: Malformed dynamically included child\n"
        "  ansible.builtin.debug: [unterminated\n",
        encoding="utf-8",
    )
    assert child.resolve() not in checker.discover_inputs(project)
    assert checker.discover_inputs(project) == (playbook.resolve(),)
    return project, playbook, child


def _native_lint(
    tmp_path: Path,
    project: Path,
    playbook: Path,
    explicit_project: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, str]:
    state = tmp_path / ("native-explicit" if explicit_project else "native-default")
    state.mkdir()
    (state / "tmp").mkdir()
    config = state / "ansible-lint.yml"
    config.write_text(
        json.dumps(
            {"profile": "min", "enable_list": ["args", "jinja"],
             "warn_list": [], "skip_list": [], "exclude_paths": [], "offline": True}
        ),
        encoding="utf-8",
    )
    ignore = state / "ansible-lint-ignore"
    ignore.write_text("", encoding="utf-8")
    command = [
        sys.executable, "-X", "cpu_count=8", "-c", checker.LINT_BOOTSTRAP,
        "--offline", "--nocolor", "--config-file", str(config),
        "--ignore-file", str(ignore),
    ]
    if explicit_project:
        command.extend(["--project-dir", str(project)])
    command.extend(["--", str(playbook)])
    with monkeypatch.context() as context, chdir(state):
        context.delenv("VIRTUAL_ENV", raising=False)
        for name, value in {
            "ANSIBLE_HOME": str(state / "ansible-home"),
            "ANSIBLE_LOCAL_TEMP": str(state / "tmp"),
            "ANSIBLE_LINT_SKIP_SCHEMA_UPDATE": "1",
            "ANSIBLE_COLLECTIONS_PATH": str(REPOSITORY / "collections"),
            "PYTHONWARNINGS": "error",
            "PYTHON_CPU_COUNT": "8",
            "TMPDIR": str(state / "tmp"),
        }.items():
            context.setenv(name, value)
        log = state / "ansible-lint.log"
        returncode = stream_command(
            command, log, observed_root=state / "observed",
            label="gludd-molecule-closure-proof", max_seconds=45, heartbeat_seconds=10,
        )
    return returncode, log.read_text(encoding="utf-8")


def test_real_native_cli_project_root_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, playbook, child = _project(tmp_path)
    before, before_output = _native_lint(tmp_path, project, playbook, False, monkeypatch)
    after, after_output = _native_lint(tmp_path, project, playbook, True, monkeypatch)
    print(f"CLOSURE-CONTROL before_rc={before} after_rc={after} child={child}")
    assert "late.yml" not in before_output
    assert before == 0
    assert after not in {0, 124}
    assert "late.yml" in after_output
    assert "load-failure" in after_output or "parser-error" in after_output


def test_existing_checker_rejects_malformed_dynamic_child_outside_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This must fail before the explicit project-root repair and pass after it."""
    project, _playbook, _child = _project(tmp_path)
    monkeypatch.setattr(checker, "ROOT", project)
    original_environment = dict(os.environ)
    original_cwd = Path.cwd()
    assert checker.main() == 1
    assert dict(os.environ) == original_environment
    assert Path.cwd() == original_cwd
