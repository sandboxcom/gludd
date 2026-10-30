"""Native result guidance preserves the no-commit merge owner's real outcome."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_git_merge_nc_safety import (
    RepositoryFixture,
    invoke,
    run_git,
    snapshot,
)
from tests.unit.test_git_merge_nc_safety import (
    repository as repository,
)


@pytest.mark.parametrize("merge_outcome", ("clean", "dirty", "missing", "conflict", "existing"))
def test_result_guidance_preserves_native_status_and_proof_scope(
    repository: RepositoryFixture, tmp_path: Path, merge_outcome: str,
) -> None:
    """Each terminal result directs the next action without granting release proof."""
    root, environment = repository.root, repository.environment
    branch = "feature/clean"
    if merge_outcome == "dirty":
        (root / "keep.txt").write_bytes(b"retain the owned dirty file\n")
    elif merge_outcome == "existing":
        native = run_git(
            root, environment, "-c", "merge.autoStash=false", "merge",
            "--no-autostash", "--no-ff", "--no-commit", "feature/conflict", check=False,
        )
        assert native.returncode == 1
    elif merge_outcome == "missing":
        branch = "missing-ref"
    elif merge_outcome == "conflict":
        branch = "feature/conflict"
    before = snapshot(root, environment)
    result, calls = invoke(root, environment, tmp_path, branch)
    expected_native = {"clean": 0, "dirty": 2, "existing": 2, "missing": 128, "conflict": 1}[merge_outcome]
    assert result.returncode == (0 if expected_native == 0 else 2), result.stdout + result.stderr
    if expected_native:
        assert f"Error {expected_native}" in result.stderr
    expected_outcome = (
        "passed" if merge_outcome == "clean"
        else "refused" if merge_outcome in {"dirty", "existing"} else "failed"
    )
    assert f"AGENT_NEXT target=git-merge-nc outcome={expected_outcome}" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert ("make git-diff-full" if merge_outcome == "clean" else "make worktree-state") in result.stderr
    if merge_outcome == "conflict":
        assert result.stderr.index("AGENT_NEXT") > result.stderr.index("GIT_MERGE_NC_ABORTED")
        after = snapshot(root, environment)
        for key in ("head", "refs", "stashes", "entries", "status", "files", "merge"):
            assert after[key] == before[key], key
    elif merge_outcome == "clean":
        after = snapshot(root, environment)
        for key in ("head", "refs", "stashes"):
            assert after[key] == before[key]
        assert (root / "feature.txt").read_bytes() == b"feature payload\n"
    else:
        assert snapshot(root, environment) == before
        assert not any("merge" in arguments for arguments in calls)
