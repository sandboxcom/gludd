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


def test_build_env_refuses_secret_shaped_passthrough(monkeypatch):
    """env_passthrough is UNTRUSTED (it comes from the target repo's project.yml).
    A secret-shaped name must be refused even if explicitly listed, so a malicious
    project.yml cannot opt gludd's own secrets into a child command's env."""
    from general_ludd.project_runner.runner import _build_env

    monkeypatch.setenv("ZAI_API_KEY", "sk-secret")  # pragma: allowlist secret
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")  # pragma: allowlist secret
    monkeypatch.setenv("GH_TOKEN", "ghtok")  # pragma: allowlist secret
    monkeypatch.setenv("DB_PASSWORD", "pw")  # pragma: allowlist secret
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgres://x")

    env = _build_env(
        [
            "ZAI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "GH_TOKEN",
            "DB_PASSWORD",
            "NODE_ENV",
            "DATABASE_URL",
        ]
    )

    # Secret-shaped names are refused regardless of the untrusted allowlist.
    assert "ZAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GH_TOKEN" not in env
    assert "DB_PASSWORD" not in env
    # Non-secret names are still forwarded.
    assert env.get("NODE_ENV") == "test"
    assert env.get("DATABASE_URL") == "postgres://x"


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


# --- Sanitized subprocess environment ---------------------------------------
# The child gets a minimal base (PATH/HOME/locale/temp) plus only names opted in
# via env_passthrough — gludd's own secrets must NOT leak into target commands.
# `printenv NAME` prints the value and exits 0 when set, exits non-zero when not.

def test_env_default_passthrough_is_minimal_and_safe():
    prof = ProjectProfile(commands={"test": "true"}, allowed_exec=["true"])
    assert prof.env_passthrough == ["NODE_ENV", "CI"]


def test_target_command_does_not_see_gludd_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "super-secret-should-not-leak")
    prof = ProjectProfile(
        commands={"leak": "printenv ZAI_API_KEY"}, allowed_exec=["printenv"]
    )
    res = ProjectCommandRunner(tmp_path, prof).run("leak")
    # printenv exits non-zero because the var is absent from the child's env.
    assert res.exit_code != 0
    assert "super-secret-should-not-leak" not in res.stdout_tail


def test_target_command_sees_path(tmp_path):
    prof = ProjectProfile(commands={"p": "printenv PATH"}, allowed_exec=["printenv"])
    res = ProjectCommandRunner(tmp_path, prof).run("p")
    assert res.passed
    assert res.stdout_tail.strip()  # PATH is always present so executables resolve


def test_target_command_sees_allowlisted_passthrough(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_BUILD_FLAG", "on-1234")
    prof = ProjectProfile(
        commands={"e": "printenv MY_BUILD_FLAG"},
        allowed_exec=["printenv"],
        env_passthrough=["MY_BUILD_FLAG"],
    )
    res = ProjectCommandRunner(tmp_path, prof).run("e")
    assert res.passed
    assert "on-1234" in res.stdout_tail


def test_unlisted_env_var_not_passed_through(tmp_path, monkeypatch):
    # A var present in gludd's env but NOT in env_passthrough must not appear.
    monkeypatch.setenv("SOME_UNLISTED_VAR", "nope")
    prof = ProjectProfile(
        commands={"e": "printenv SOME_UNLISTED_VAR"}, allowed_exec=["printenv"]
    )
    res = ProjectCommandRunner(tmp_path, prof).run("e")
    assert res.exit_code != 0
    assert "nope" not in res.stdout_tail


def test_output_capture_is_bounded_to_tail(tmp_path):
    # A chatty command must not blow the tail bound: yes floods stdout; head caps
    # the pipe so `yes` exits on SIGPIPE. We only need the captured tail bounded.
    from general_ludd.project_runner.runner import _TAIL_CHARS

    prof = ProjectProfile(
        commands={"chatty": "seq 1 100000"}, allowed_exec=["seq"]
    )
    res = ProjectCommandRunner(tmp_path, prof).run("chatty")
    assert res.passed
    assert len(res.stdout_tail) <= _TAIL_CHARS
    # It kept the TAIL: the last line (100000) survives, early lines are dropped.
    assert "100000" in res.stdout_tail
    assert "\n1\n" not in res.stdout_tail


# --- Memory cap + OOM detection ---------------------------------------------

def test_check_result_oom_summary():
    r = CheckResult(name="build", exit_code=-9, passed=False, duration_s=2.0, oom_killed=True)
    assert "OOM-KILLED" in r.summary()
    assert r.oom_killed is True


def test_oom_killed_defaults_false():
    r = CheckResult(name="t", exit_code=0, passed=True, duration_s=0.1)
    assert r.oom_killed is False


def test_runner_accepts_memory_limit_and_still_runs(tmp_path):
    # A generous cap must not break a tiny normal command (plumbing check — the
    # preexec RLIMIT_AS path is exercised on POSIX, a no-op on Windows).
    prof = ProjectProfile(commands={"test": "true"}, allowed_exec=["true"])
    res = ProjectCommandRunner(tmp_path, prof, memory_limit_mb=1024).run("test")
    assert res.passed and not res.oom_killed


def test_normal_failure_is_not_oom(tmp_path):
    prof = ProjectProfile(commands={"test": "false"}, allowed_exec=["false"])
    res = ProjectCommandRunner(tmp_path, prof).run("test")
    assert not res.passed and not res.oom_killed  # exit 1, not a SIGKILL
