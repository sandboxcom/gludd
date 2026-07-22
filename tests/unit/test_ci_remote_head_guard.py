from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from subprocess import CompletedProcess

from scripts.ci_remote_head_guard import guard_remote_head


def _cp(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess[str]:
    return CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeGit:
    def __init__(self, responses: dict[tuple[str, ...], CompletedProcess[str] | str]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> CompletedProcess[str]:
        key = tuple(argv)
        self.calls.append(key)
        value = self.responses[key]
        if isinstance(value, CompletedProcess):
            return value
        return _cp(stdout=value)


def _happy_responses() -> dict[tuple[str, ...], CompletedProcess[str] | str]:
    branch = "release-sync-beta1"
    head = "abcdef1234567890"
    return {
        ("git", "branch", "--show-current"): branch + chr(10),
        ("git", "status", "--porcelain=v1", "--untracked-files=all"): "",
        ("git", "rev-parse", "HEAD"): head + chr(10),
        ("git", "ls-remote", "sandboxcom", "refs/heads/" + branch): head + chr(9) + "refs/heads/" + branch + chr(10),
    }


def test_guard_passes_when_clean_local_head_matches_remote_branch() -> None:
    run = FakeGit(_happy_responses())
    result = guard_remote_head(remote="sandboxcom", run=run)
    assert result.ok is True
    assert "matches local HEAD" in result.message


def test_guard_blocks_dirty_tree_before_remote_lookup() -> None:
    responses = _happy_responses()
    responses[("git", "status", "--porcelain=v1", "--untracked-files=all")] = " M Makefile" + chr(10)
    run = FakeGit(responses)
    result = guard_remote_head(remote="sandboxcom", run=run)
    assert result.ok is False
    assert "uncommitted changes" in result.message
    assert ("git", "rev-parse", "HEAD") not in run.calls


def test_guard_blocks_ref_that_does_not_match_current_branch() -> None:
    run = FakeGit(_happy_responses())
    result = guard_remote_head(ref="master", remote="sandboxcom", run=run)
    assert result.ok is False
    assert "does not match current branch" in result.message
    assert ("git", "status", "--porcelain=v1", "--untracked-files=all") not in run.calls


def test_guard_blocks_missing_remote_branch() -> None:
    responses = _happy_responses()
    branch = "release-sync-beta1"
    responses[("git", "ls-remote", "sandboxcom", "refs/heads/" + branch)] = ""
    run = FakeGit(responses)
    result = guard_remote_head(remote="sandboxcom", run=run)
    assert result.ok is False
    assert "remote branch sandboxcom/release-sync-beta1 does not exist" in result.message


def test_guard_blocks_local_head_that_differs_from_remote() -> None:
    responses = _happy_responses()
    branch = "release-sync-beta1"
    responses[("git", "ls-remote", "sandboxcom", "refs/heads/" + branch)] = (
        "1111111111111111" + chr(9) + "refs/heads/" + branch + chr(10)
    )
    run = FakeGit(responses)
    result = guard_remote_head(remote="sandboxcom", run=run)
    assert result.ok is False
    assert "differs from sandboxcom/release-sync-beta1" in result.message
    assert "push exact HEAD first" in result.message


def test_guard_blocks_detached_head() -> None:
    responses = _happy_responses()
    responses[("git", "branch", "--show-current")] = ""
    run = FakeGit(responses)
    result = guard_remote_head(remote="sandboxcom", run=run)
    assert result.ok is False
    assert "detached HEAD" in result.message


def test_makefile_ci_trigger_depends_on_remote_head_guard() -> None:
    content = Path("Makefile").read_text()
    assert "ci-trigger: ci-remote-head-guard _require-gh" in content
    assert "scripts/ci_remote_head_guard.py" in content
    trigger_block = content.split("ci-trigger: ci-remote-head-guard _require-gh", 1)[1].split("# List currently", 1)[0]
    assert "--ref master" not in trigger_block
