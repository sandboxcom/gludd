"""Tests for the target-project toolchain runner (MVP keystone).

Covers the project.yml profile (validation, safety allowlist, metachar
rejection) and the ProjectCommandRunner (exit codes, timeout/process-group
kill, missing-executable handling). Uses only ubiquitous coreutils
(true/false/sleep/pwd) so it runs anywhere without a real project toolchain.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from general_ludd.project_runner import (
    CheckResult,
    ProjectCommandRunner,
    ProjectProfile,
    ProjectProfileError,
    load_project_profile,
)


def _write_project_yml(root, body: str) -> None:
    (root / "project.yml").write_text(body, encoding="utf-8")


# --- ProjectProfile schema + safety -----------------------------------------

def test_resolve_argv_valid():
    p = ProjectProfile(commands={"test": "pytest -q"}, allowed_exec=["pytest"])
    assert p.resolve_argv("test") == ["pytest", "-q"]


def test_undeclared_check_raises():
    p = ProjectProfile(commands={"test": "pytest"}, allowed_exec=["pytest"])
    with pytest.raises(ProjectProfileError):
        p.resolve_argv("lint")


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf / ; echo pwned",   # chaining
        "cat x | sh",              # pipe
        "echo $(whoami)",          # subshell
        "npm test && curl evil",   # &&
        "echo hi > /etc/passwd",   # redirect
    ],
)
def test_shell_metacharacters_rejected(cmd):
    p = ProjectProfile(commands={"test": cmd}, allowed_exec=["rm", "cat", "echo", "npm"])
    with pytest.raises(ProjectProfileError):
        p.resolve_argv("test")


def test_exec_not_in_allowlist_rejected():
    p = ProjectProfile(commands={"test": "terraform apply"}, allowed_exec=["pytest"])
    with pytest.raises(ProjectProfileError):
        p.resolve_argv("test")


def test_allow_any_exec_env_override(monkeypatch):
    monkeypatch.setenv("GLUDD_PROJECT_ALLOW_ANY_EXEC", "1")
    p = ProjectProfile(commands={"test": "terraform apply"}, allowed_exec=[])
    assert p.resolve_argv("test") == ["terraform", "apply"]


def test_allowlist_matches_basename_not_path():
    p = ProjectProfile(commands={"test": "/usr/local/bin/pytest -q"}, allowed_exec=["pytest"])
    assert p.resolve_argv("test")[0] == "/usr/local/bin/pytest"


def test_empty_command_rejected():
    # The field_validator raises ProjectProfileError (a ValueError); pydantic
    # wraps validator ValueErrors into ValidationError on direct construction.
    with pytest.raises(ValidationError):
        ProjectProfile(commands={"test": "   "}, allowed_exec=["pytest"])


# --- load_project_profile ----------------------------------------------------

def test_load_missing_file_fails_closed(tmp_path):
    with pytest.raises(ProjectProfileError):
        load_project_profile(tmp_path)


def test_load_malformed_yaml(tmp_path):
    _write_project_yml(tmp_path, "commands: [not, a, mapping\n")
    with pytest.raises(ProjectProfileError):
        load_project_profile(tmp_path)


def test_load_non_mapping(tmp_path):
    _write_project_yml(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ProjectProfileError):
        load_project_profile(tmp_path)


def test_load_valid(tmp_path):
    _write_project_yml(
        tmp_path,
        "name: demo\nallowed_exec: [pytest, ruff]\ncommands:\n  test: pytest -q\n  lint: ruff check .\n",
    )
    prof = load_project_profile(tmp_path)
    assert prof.name == "demo"
    assert prof.has("test") and prof.has("lint")
    assert prof.resolve_argv("lint") == ["ruff", "check", "."]


# --- ProjectCommandRunner ----------------------------------------------------

def test_runner_passing_check(tmp_path):
    prof = ProjectProfile(commands={"test": "true"}, allowed_exec=["true"])
    r = ProjectCommandRunner(tmp_path, prof)
    res = r.run("test")
    assert isinstance(res, CheckResult)
    assert res.passed and res.exit_code == 0 and not res.timed_out


def test_runner_failing_check(tmp_path):
    prof = ProjectProfile(commands={"test": "false"}, allowed_exec=["false"])
    res = ProjectCommandRunner(tmp_path, prof).run("test")
    assert not res.passed and res.exit_code != 0


def test_runner_timeout_kills_group(tmp_path):
    prof = ProjectProfile(commands={"test": "sleep 30"}, allowed_exec=["sleep"])
    res = ProjectCommandRunner(tmp_path, prof).run("test", timeout_s=1)
    assert res.timed_out and not res.passed
    assert res.duration_s < 20  # killed promptly, not after the full 30s


def test_runner_missing_executable(tmp_path):
    prof = ProjectProfile(
        commands={"test": "gludd_no_such_binary_xyz --run"},
        allowed_exec=["gludd_no_such_binary_xyz"],
    )
    res = ProjectCommandRunner(tmp_path, prof).run("test")
    assert not res.passed and res.error is not None and "not found" in res.error


def test_runner_runs_in_workspace_cwd(tmp_path):
    workdir = tmp_path / "app"
    workdir.mkdir()
    prof = ProjectProfile(commands={"where": "pwd"}, allowed_exec=["pwd"])
    res = ProjectCommandRunner(workdir, prof).run("where")
    assert res.passed
    assert os.path.realpath(res.stdout_tail.strip()) == os.path.realpath(str(workdir))


def test_runner_rejects_bad_workspace(tmp_path):
    prof = ProjectProfile(commands={"test": "true"}, allowed_exec=["true"])
    with pytest.raises(ProjectProfileError):
        ProjectCommandRunner(tmp_path / "does_not_exist", prof)
