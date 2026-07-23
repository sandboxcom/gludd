from __future__ import annotations

import general_ludd.git_automation.types as git_types


def test_git_state_result_uses_independent_default_lists() -> None:
    first = git_types.GitStateResult(success=True, branch="master", head="abc123")
    second = git_types.GitStateResult(success=True, branch="master", head="def456")

    first.status.append(" M Makefile")
    first.errors.append("dirty")

    assert second.status == []
    assert second.errors == []
    assert first.remote == "sandboxcom"
