"""The native tooling profile façade is locked, inexact and literal-value safe."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from scripts.check_make_help import help_targets_from_makefile

ROOT = Path(os.environ.get("GLUDD_FORMAT_FACADE_ROOT", Path(__file__).resolve().parents[2]))
TARGET = "sync-ansible-native-format"
ENVIRONMENT = "ANSIBLE_FORMAT_SYNC_ENVIRONMENT"
MODE = "ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY"


@pytest.fixture
def work(tmp_path: Path) -> Path:
    foundation = (ROOT / "make/00-foundation.mk").read_text()
    marker = "ANSIBLE_FORMAT_SYNC_ENVIRONMENT ?="
    recipe = foundation[foundation.index(marker):foundation.index("\nuv-cache-path:")] if marker in foundation else ""
    (tmp_path / "Makefile").write_text(recipe)
    executable = tmp_path / "uv-observer"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path(os.environ['GLUDD_TEST_UV_RECORD']).write_text(json.dumps({"
        "'argv': sys.argv[1:], 'environment': {name: os.environ.get(name) for name in "
        "['UV_PROJECT_ENVIRONMENT', 'VIRTUAL_ENV', 'UV_NO_SYNC', 'UV_PYTHON_DOWNLOADS', "
        "'ANSIBLE_FORMAT_SYNC_ENVIRONMENT', 'ANSIBLE_FORMAT_SYNC_VALIDATE_ONLY']}}))\n"
        "print('native-uv-visible-output', flush=True)\n"
        "raise SystemExit(int(os.environ.get('GLUDD_TEST_UV_EXIT', '0')))\n"
    )
    executable.chmod(0o700)
    return tmp_path


def _environment(work: Path, name: str = "existing-tooling-environment") -> Path:
    environment = work / name
    (environment / "bin").mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    (environment / "bin/python").symlink_to(sys.executable)
    return environment


def _run(
    work: Path, environment: str, mode: str | None = "1", native_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    command = ["make", TARGET, "UV=" + str(work / "uv-observer"), ENVIRONMENT + "=" + environment]
    if mode is not None:
        command.append(MODE + "=" + mode)
    private = dict(os.environ, GLUDD_TEST_UV_RECORD=str(work / "native.json"), GLUDD_TEST_UV_EXIT=str(native_exit))
    private.pop("MAKEFLAGS", None)
    return subprocess.run(command, cwd=work, env=private, capture_output=True, text=True, timeout=5, check=False)


@pytest.mark.parametrize("mode", [None, "1", "0"])
def test_locked_native_profile_plan_and_apply_have_one_owner(work: Path, mode: str | None) -> None:
    environment = _environment(work)
    result = _run(work, str(environment), mode)
    assert result.returncode == 0, result.stdout + result.stderr
    document = cast(dict[str, object], json.loads((work / "native.json").read_text()))
    argv = cast(list[str], document["argv"])
    expected = ["sync", "--project", str(work / "requirements/profiles/dev-ansible"),
                "--locked", "--inexact", "--python", str(environment / "bin/python")]
    if mode != "0":
        expected.append("--dry-run")
    assert argv == expected
    private = cast(dict[str, str | None], document["environment"])
    assert private["UV_PROJECT_ENVIRONMENT"] == private["VIRTUAL_ENV"] == str(environment)
    assert private["UV_NO_SYNC"] == "0" and private["UV_PYTHON_DOWNLOADS"] == "never"
    assert private[ENVIRONMENT] is None and private[MODE] is None


@pytest.mark.parametrize("mode", ["2", "", "$(error mode-must-stay-data)"])
def test_invalid_mode_refuses_before_native_tool(work: Path, mode: str) -> None:
    result = _run(work, str(_environment(work)), mode)
    assert result.returncode != 0
    assert "must be 0 or 1" in result.stdout
    assert not (work / "native.json").exists()


def test_missing_environment_never_creates_or_syncs(work: Path) -> None:
    absent = work / "never-create-an-environment"
    result = _run(work, str(absent), "0")
    assert result.returncode != 0 and "existing tooling environment" in result.stdout
    assert not absent.exists() and not (work / "native.json").exists()


def test_relative_environment_stays_bound_to_the_caller_absolute_environment(work: Path) -> None:
    environment = _environment(work)
    result = _run(work, environment.name)
    assert result.returncode == 0, result.stdout + result.stderr
    document = cast(dict[str, object], json.loads((work / "native.json").read_text()))
    private = cast(dict[str, str | None], document["environment"])
    assert private["UV_PROJECT_ENVIRONMENT"] == private["VIRTUAL_ENV"] == str(environment)
    argv = cast(list[str], document["argv"])
    assert argv[-2] == str(environment / "bin/python")


def test_native_environment_path_is_literal_make_and_shell_data(work: Path) -> None:
    name = "gludd env ' " + "$(error literal-path)" + " ; ${ignored} `touch no-side-effect`"
    environment = _environment(work, name)
    result = _run(work, str(environment))
    assert result.returncode == 0, result.stdout + result.stderr
    document = cast(dict[str, object], json.loads((work / "native.json").read_text()))
    argv = cast(list[str], document["argv"])
    assert argv[-2] == str(environment / "bin/python")
    assert not (work / "no-side-effect").exists()


def test_native_tool_failure_and_output_remain_visible(work: Path) -> None:
    result = _run(work, str(_environment(work)), "0", native_exit=17)
    assert result.returncode != 0 and "native-uv-visible-output" in result.stdout
    assert (work / "native.json").exists()


def test_facade_has_explicit_safe_plan_contract_and_help() -> None:
    assert TARGET in help_targets_from_makefile(ROOT / "Makefile")
    contract = cast(dict[str, object], json.loads((ROOT / "config/make_target_contract.json").read_text()))
    rows = cast(list[dict[str, object]], contract["targets"])
    entries = [row for row in rows if row["name"] == TARGET]
    assert len(entries) == 1
    assert entries[0]["make_variables"] == [ENVIRONMENT, MODE]
    assert entries[0]["behavior"] == f"make {TARGET} {ENVIRONMENT}=.venv {MODE}=1"
