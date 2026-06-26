"""Regression test for the push_to_remote network-timeout gap (GA-1 real defect).

`GitAutomation.push_to_remote` does network I/O. Before the fix it called
`subprocess.run` with NO timeout, so an unresponsive remote (or a credential
prompt) could hang the daemon indefinitely inside a tick — the exact failure
`_run_git` was built to prevent. The fix mirrors `_run_git`: a
`_GIT_TIMEOUT_SECONDS` timeout + the non-interactive git env, with a clean
`PushResult(success=False, ...)` on timeout instead of a hang.
"""

from __future__ import annotations

import subprocess

import general_ludd.git_automation.repo as repo_mod
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import PushResult


def test_push_to_remote_times_out_cleanly(monkeypatch) -> None:
    git = GitAutomation(repo_path="/tmp/whatever")

    def _raise_timeout(*args, **kwargs):
        # The fix must pass a finite timeout through to subprocess.run.
        assert kwargs.get("timeout") == repo_mod._GIT_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(cmd="git push", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(repo_mod.subprocess, "run", _raise_timeout)

    result = git.push_to_remote("/tmp/repo", "origin", "main")
    assert isinstance(result, PushResult)
    assert result.success is False
    assert "timed out" in result.message
    assert result.remote == "origin"
    assert result.branch == "main"


def test_push_to_remote_passes_timeout_env_and_cwd(monkeypatch) -> None:
    git = GitAutomation(repo_path="/tmp/whatever")
    captured: dict = {}

    class _CompletedProcess:
        returncode = 0
        stderr = ""
        stdout = "Everything up-to-date"

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return _CompletedProcess()

    monkeypatch.setattr(repo_mod.subprocess, "run", _capture)

    result = git.push_to_remote("/tmp/repo", "origin", "main")
    assert result.success is True
    # The hardening: finite timeout, non-interactive env, repo cwd.
    assert captured.get("timeout") == repo_mod._GIT_TIMEOUT_SECONDS
    assert captured.get("cwd") == "/tmp/repo"
    env = captured.get("env") or {}
    assert env.get("GIT_TERMINAL_PROMPT") == "0"
    assert env.get("GIT_ASKPASS") == "echo"
