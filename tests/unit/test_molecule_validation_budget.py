"""Bounded budget CLI, native fixture and plan-mode regression contracts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    import check_molecule_yaml as checker
    import stream_command as owner
else:
    from scripts import check_molecule_yaml as checker
    from scripts import stream_command as owner

REPOSITORY = Path(__file__).resolve().parents[2]


def _fixture_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "fixture-project"
    tasks = project / "collections/ansible_collections/example/sample/roles/demo/tasks"
    tasks.mkdir(parents=True)
    path = tasks / "main.yml"
    path.write_text("- name: Native example\n  ansible.builtin.debug: {msg: valid}\n", encoding="utf-8")
    return project, path


def _fixture_cli(tmp_path: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    project, _tasks = _fixture_project(tmp_path)
    script = project / "scripts" / "check_molecule_yaml.py"
    script.parent.mkdir()
    shutil.copyfile(Path(checker.__file__), script)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY)
    environment.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=project, env=environment, text=True, capture_output=True, timeout=60, check=False,
    )


@pytest.mark.parametrize("value", ("0", "121", "invalid", "1.5"))
def test_cli_refuses_invalid_budget_before_native_work(tmp_path: Path, value: str) -> None:
    completed = _fixture_cli(tmp_path, ["--lint-timeout-seconds", value])
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "MOLECULE_VALIDATION_CONFIG_START" not in completed.stdout
    assert "MOLECULE_VALIDATION_LINT_START" not in completed.stdout


@pytest.mark.parametrize("arguments, expected", (([], 45), (["--lint-timeout-seconds", "120"], 120)))
def test_real_small_fixture_cli_propagates_native_budget(
    tmp_path: Path, arguments: list[str], expected: int,
) -> None:
    completed = _fixture_cli(tmp_path, arguments)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"MOLECULE_VALIDATION_LINT_START files=1 timeout={expected}" in completed.stdout
    assert "MOLECULE_VALIDATION_CAPACITY workers=" in completed.stdout
    assert "syntax_inputs=0 active_maximum=0" in completed.stdout
    assert "MOLECULE_VALIDATION_LINT_END rc=0" in completed.stdout


def test_real_cli_plan_is_mutation_free_and_starts_no_native_work(tmp_path: Path) -> None:
    completed = _fixture_cli(tmp_path, ["--lint-timeout-seconds", "120", "--validate-only"])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "MOLECULE_VALIDATION_PLAN" in completed.stdout
    assert "lint_timeout_seconds=120" in completed.stdout
    assert "proof=none" in completed.stdout
    assert "MOLECULE_VALIDATION_CONFIG_START" not in completed.stdout
    assert "MOLECULE_VALIDATION_CAPACITY" not in completed.stdout
    assert "MOLECULE_VALIDATION_LINT_START" not in completed.stdout
    assert not (tmp_path / "fixture-project" / ".ansible").exists()


@pytest.mark.parametrize("value", (0, 121, False, "45", 45.0))
def test_direct_main_refuses_wrong_type_or_range_before_work(
    value: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(_root: Path) -> tuple[Path, ...]:
        pytest.fail("invalid budget must be refused before discovery")

    monkeypatch.setattr(checker, "discover_inputs", forbidden)
    assert checker.main(lint_timeout_seconds=cast(int, value)) == 1
    assert "lint timeout must be an integer from 1 through 120" in capsys.readouterr().err


@pytest.mark.parametrize("arguments, expected", (([], 45), (["--lint-timeout-seconds", "1"], 1),
                                                    (["--lint-timeout-seconds", "120"], 120)))
def test_plan_preserves_environment_and_never_calls_validator(
    arguments: list[str], expected: int, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> int:
        pytest.fail("plan mode must not enter native validation")

    monkeypatch.setattr(checker, "main", forbidden)
    environment = dict(os.environ)
    directory = Path.cwd()
    assert checker.cli([*arguments, "--validate-only"]) == 0
    output = capsys.readouterr().out
    assert f"lint_timeout_seconds={expected}" in output
    assert "maximum_syntax_workers=8" in output
    assert "profile=min enable_list=args,jinja" in output
    assert "proof=none" in output
    assert dict(os.environ) == environment
    assert Path.cwd() == directory


@pytest.mark.parametrize("arguments, expected", (([], 45), (["--lint-timeout-seconds", "120"], 120)))
def test_cli_delegates_exact_budget_to_existing_validator(
    arguments: list[str], expected: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets: list[int] = []

    def native(*, lint_timeout_seconds: int = 45) -> int:
        budgets.append(lint_timeout_seconds)
        return 1

    monkeypatch.setattr(checker, "main", native)
    assert checker.cli(arguments) == 1
    assert budgets == [expected]


@pytest.mark.parametrize("budget", (None, 120))
def test_budget_keeps_existing_owner_scope_profile_and_resource_policy(
    tmp_path: Path, budget: int | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, tasks = _fixture_project(tmp_path)
    monkeypatch.setattr(checker, "ROOT", project)
    monkeypatch.setattr(checker, "validate_action_option_names", lambda _paths: None)
    observed: dict[str, object] = {}

    def bounded(command: list[str], _log_path: Path, **kwargs: object) -> int:
        observed.update(kwargs)
        assert command[:3] == [sys.executable, "-X", "cpu_count=8"]
        assert command[command.index("--project-dir") + 1] == str(project)
        assert command[command.index("--") + 1:] == [str(tasks.resolve())]
        config = Path(command[command.index("--config-file") + 1])
        assert json.loads(config.read_text()) == {
            "profile": "min", "enable_list": ["args", "jinja"], "warn_list": [],
            "skip_list": [], "exclude_paths": [], "offline": True,
            "kinds": [
                {"tasks": "**/molecule/shared/mock_daemon_start.yml"},
                {"tasks": "**/molecule/shared/mock_daemon_stop.yml"},
            ],
        }
        ignore = Path(command[command.index("--ignore-file") + 1])
        assert ignore.read_text() == ""
        assert os.environ["PYTHON_CPU_COUNT"] == "8"
        _log_path.write_text("Passed: 0 failure(s), 0 warning(s)\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(owner, "stream_command", bounded)
    environment = dict(os.environ)
    directory = Path.cwd()
    result = checker.main() if budget is None else checker.main(lint_timeout_seconds=budget)
    assert result == 0
    assert observed["max_seconds"] == (45 if budget is None else budget)
    assert observed["heartbeat_seconds"] == 10
    assert observed["label"] == "gludd-molecule-yaml"
    assert dict(os.environ) == environment
    assert Path.cwd() == directory


def test_cli_fixture_source_is_identical_and_coverage_maps_to_its_owner(tmp_path: Path) -> None:
    """Copied native CLI fixtures must not invent separate production files."""
    from coverage import Coverage
    from coverage.files import PathAliases

    result = _fixture_cli(tmp_path, ["--validate-only"])
    assert result.returncode == 0, result.stdout + result.stderr
    copied = tmp_path / "fixture-project/scripts/check_molecule_yaml.py"
    assert copied.read_bytes() == Path(checker.__file__).read_bytes()
    config = Path(__file__).resolve().parents[2] / "config/coverage_molecule_yaml.ini"
    coverage = Coverage(config_file=str(config))
    aliases = PathAliases()
    for paths in coverage.config.paths.values():
        for pattern in paths[1:]:
            aliases.add(pattern, paths[0])
    assert Path(aliases.map(str(copied.resolve()))).resolve() == Path(checker.__file__).resolve()
