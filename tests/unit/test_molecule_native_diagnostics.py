"""Native zero-exit diagnostic regressions and portable renderer import proof."""

from __future__ import annotations

import json
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

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = Path(checker.__file__).resolve()
BOOTSTRAP = (
    "import runpy, sys; from pathlib import Path; "
    "namespace = runpy.run_path(sys.argv[1]); main = namespace['main']; "
    "main.__globals__['ROOT'] = Path(sys.argv[2]); raise SystemExit(main())"
)


def _task(root: Path, action: str = "ansible.builtin.debug: {msg: valid}") -> Path:
    path = root / "collections/ansible_collections/example/sample/roles/demo/tasks/main.yml"
    path.parent.mkdir(parents=True)
    path.write_text(f"- name: Native diagnostic fixture\n  {action}\n", encoding="utf-8")
    return path


def _invoke(root: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY)
    return subprocess.run(
        [sys.executable, "-W", "error", "-c", BOOTSTRAP, str(SCRIPT), str(root)],
        cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=60,
    )


@pytest.mark.parametrize(
    "diagnostic",
    (
        "WARNING  Unable to load module native.missing for options validation",
        "WARNING  Ignored exception from ArgsRule.matchtasks while processing late.yml: 'port'",
        "ERROR    Failed to find templated playbook.",
        "CRITICAL native runner failed",
        "FATAL native runner failed",
        "[WARNING]: native Ansible warning",
        "[ERROR]: native Ansible error",
    ),
)
def test_zero_exit_native_diagnostic_is_rejected_without_hiding_it(
    tmp_path: Path, diagnostic: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _task(tmp_path)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "validate_action_option_names", lambda _paths: None)

    def native(command: list[str], log: Path, **_kwargs: object) -> int:
        config = Path(command[command.index("--config-file") + 1])
        policy = json.loads(config.read_text(encoding="utf-8"))
        assert policy["enable_list"] == ["args", "jinja"]
        assert policy["skip_list"] == policy["warn_list"] == policy["exclude_paths"] == []
        log.write_text(diagnostic + "\nPassed: 0 failure(s), 0 warning(s)\n", encoding="utf-8")
        print(diagnostic)
        return 0

    monkeypatch.setattr(owner, "stream_command", native)
    environment = dict(os.environ)
    directory = Path.cwd()
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert diagnostic in captured.out
    assert "MOLECULE_VALIDATION_LINT_END rc=0" in captured.out
    assert "MOLECULE_VALIDATION_DIAGNOSTIC_FAILED" in captured.err
    assert "proof=none" in captured.err
    assert dict(os.environ) == environment
    assert Path.cwd() == directory


@pytest.mark.parametrize("returncode", (2, 124))
def test_nonzero_native_status_remains_failure(
    tmp_path: Path, returncode: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(tmp_path)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "validate_action_option_names", lambda _paths: None)
    monkeypatch.setattr(owner, "stream_command", lambda *_args, **_kwargs: returncode)
    assert checker.main() == 1


def test_native_zero_status_requires_real_readable_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _task(tmp_path)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "validate_action_option_names", lambda _paths: None)
    monkeypatch.setattr(owner, "stream_command", lambda *_args, **_kwargs: 0)
    assert checker.main() == 1
    assert "MOLECULE_VALIDATION_FAILED" in capsys.readouterr().err


def test_clean_native_output_is_accepted_and_keeps_mandatory_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(tmp_path)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "validate_action_option_names", lambda _paths: None)

    def native(command: list[str], log: Path, **kwargs: object) -> int:
        assert "--strict" in command
        assert command[:3] == [sys.executable, "-X", "cpu_count=8"]
        assert kwargs["max_seconds"] == 120
        assert kwargs["heartbeat_seconds"] == 10
        log.write_text(
            "MOLECULE_VALIDATION_CAPACITY workers=8 syntax_inputs=0 active_maximum=0\n"
            "INFO documentation mentions WARNING without emitting a warning\n"
            "Passed: 0 failure(s), 0 warning(s) in 1 file\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(owner, "stream_command", native)
    assert checker.main(lint_timeout_seconds=120) == 0


def test_real_native_missing_module_warning_does_not_count_as_acceptance(tmp_path: Path) -> None:
    _task(tmp_path, "ansible.builtin.htpasswd: {path: /tmp/not-executed, name: fixture}")
    result = _invoke(tmp_path)
    output = result.stdout + result.stderr
    assert "Unable to load module ansible.builtin.htpasswd" in output
    assert "MOLECULE_VALIDATION_LINT_END rc=0" in result.stdout
    assert result.returncode == 1, output
    assert "MOLECULE_VALIDATION_DIAGNOSTIC_FAILED" in result.stderr
    assert "proof=none" in result.stderr


def test_real_native_ignored_args_exception_does_not_count_as_acceptance(tmp_path: Path) -> None:
    _task(tmp_path, "example.sample.failure: {config: {port: '{{ runtime_port }}'}}")
    module = tmp_path / "collections/ansible_collections/example/sample/plugins/modules/failure.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from ansible.module_utils.basic import AnsibleModule\n"
        "def main():\n"
        "    AnsibleModule(argument_spec={'config': {'type': 'dict', 'options': {'port': {'type': 'int'}}}})\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    result = _invoke(tmp_path)
    output = result.stdout + result.stderr
    assert "Ignored exception from ArgsRule.matchtasks" in output, output
    assert "'port'" in output
    assert "MOLECULE_VALIDATION_LINT_END rc=0" in result.stdout
    assert result.returncode == 1, output
    assert "MOLECULE_VALIDATION_DIAGNOSTIC_FAILED" in result.stderr


@pytest.mark.parametrize("invalid_child", (False, True))
def test_shipped_renderer_import_is_portable_and_keeps_native_child_validation(
    tmp_path: Path, invalid_child: bool,
) -> None:
    source = REPOSITORY / "molecule/playbooks/system_facts_renderer/default/converge.yml"
    scenario = tmp_path / "molecule/playbooks/system_facts_renderer/default/converge.yml"
    scenario.parent.mkdir(parents=True)
    scenario.write_bytes(source.read_bytes())
    child = tmp_path / "playbooks/renderers/system_facts.yml"
    child.parent.mkdir(parents=True)
    child.write_text(
        "- name: Imported renderer fixture\n  hosts: localhost\n  gather_facts: false\n"
        "  tasks:\n    - name: Native child expression\n      ansible.builtin.debug: {msg: valid}\n"
        + ('      failed_when: "value ! incomplete"\n' if invalid_child else ""),
        encoding="utf-8",
    )
    result = _invoke(tmp_path)
    output = result.stdout + result.stderr
    assert "Failed to find" not in output, output
    if invalid_child:
        assert result.returncode == 1, output
        assert "jinja[invalid]" in output
        assert "system_facts.yml" in output
    else:
        assert result.returncode == 0, output
        assert "WARNING" not in output and "ERROR" not in output
