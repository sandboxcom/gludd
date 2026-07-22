from __future__ import annotations

import general_ludd.git_automation.repo as git_repo


def test_workflow_state_status_helpers_classify_porcelain_lines() -> None:
    lines = git_repo.GitAutomation._state_status_lines(" M Makefile\nA  new.py\n?? scratch.txt\n")

    assert lines == [" M Makefile", "A  new.py", "?? scratch.txt"]
    assert git_repo.GitAutomation._state_staged_count(lines) == 1
    assert git_repo.GitAutomation._state_untracked_count(lines) == 1


def test_workflow_state_remote_head_extracts_first_ls_remote_sha() -> None:
    assert git_repo.GitAutomation._state_remote_head("abc123\trefs/heads/master\n") == "abc123"
    assert git_repo.GitAutomation._state_remote_head("") == ""
