"""Regression tests for GitAutomation subprocess isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import general_ludd.git_automation.repo as git_repo


def test_workflow_state_status_helpers_classify_porcelain_lines() -> None:
    lines = git_repo.GitAutomation._state_status_lines(" M Makefile\nA  new.py\n?? scratch.txt\n")

    assert lines == [" M Makefile", "A  new.py", "?? scratch.txt"]
    assert git_repo.GitAutomation._state_staged_count(lines) == 1
    assert git_repo.GitAutomation._state_untracked_count(lines) == 1


def test_workflow_state_remote_head_extracts_first_ls_remote_sha() -> None:
    assert git_repo.GitAutomation._state_remote_head("abc123\trefs/heads/master\n") == "abc123"
    assert git_repo.GitAutomation._state_remote_head("") == ""


def test_run_git_disables_detached_auto_maintenance(
    monkeypatch, tmp_path: Path
) -> None:
    """A completed Git call must not leave pack writers racing repo teardown."""
    observed: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["argv"] = argv
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(git_repo.subprocess, "run", fake_run)
    git_repo.GitAutomation(str(tmp_path))._run_git("status")

    env = observed["env"]
    assert isinstance(env, dict)
    count = int(env["GIT_CONFIG_COUNT"])
    config = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(count)
    }
    assert config["gc.auto"] == "0"
    assert config["maintenance.auto"] == "false"
