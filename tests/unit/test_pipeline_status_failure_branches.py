"""Cover exact-source lookup and local-marker failure branches without hosted claims."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from scripts import pipeline_status as status

SHA = "a" * 40
REPO = "sandboxcom/gludd"
FETCH_COMMAND = [
    "gh", "run", "list", "--commit", SHA, "--limit", "100", "-R", REPO, "--json",
    "conclusion,databaseId,status,headSha,headBranch,workflowName,event,createdAt,url",
]
REMOTE_COMMAND = ["git", "ls-remote", "sandboxcom", "refs/heads/development"]


def _runner(
    expected: list[str], *, returncode: int = 0, stdout: str = "", stderr: str = "",
) -> status.Runner:
    """Check the real owner's bounded CLI request at its existing I/O seam."""
    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert command == expected
        assert options == {"capture_output": True, "text": True, "timeout": 15, "check": False}
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run


@pytest.mark.parametrize("sha", ["", "abc123", "A" * 40, "g" * 40])
def test_fetch_rejects_noncanonical_sha_before_lookup(sha: str) -> None:
    """An invalid identity must not reach the native lookup boundary."""
    def unexpected(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        pytest.fail(f"lookup attempted for invalid identity: {command!r}, {options!r}")

    with pytest.raises(status.PipelineStatusError, match="full lowercase 40-character"):
        status.fetch_runs(sha, repo=REPO, runner=unexpected)


@pytest.mark.parametrize("owner", ["fetch", "remote"])
def test_lookup_os_errors_remain_visible(owner: str) -> None:
    """Missing native tools are errors, never empty evidence or a successful head."""
    def unavailable(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert command == (FETCH_COMMAND if owner == "fetch" else REMOTE_COMMAND)
        raise OSError("native tool unavailable")

    with pytest.raises(status.PipelineStatusError, match="lookup failed: native tool unavailable"):
        if owner == "fetch":
            status.fetch_runs(SHA, repo=REPO, runner=unavailable)
        else:
            status.remote_head(branch="development", remote="sandboxcom", runner=unavailable)


@pytest.mark.parametrize(
    ("stdout", "message"),
    [("{", "invalid JSON"), ("{}", "non-list"), (json.dumps([{}] * 100), "reached its limit")],
)
def test_fetch_rejects_malformed_or_truncated_evidence(stdout: str, message: str) -> None:
    """Neither bad JSON nor the API page limit can masquerade as complete evidence."""
    with pytest.raises(status.PipelineStatusError, match=message):
        status.fetch_runs(SHA, repo=REPO, runner=_runner(FETCH_COMMAND, stdout=stdout))


@pytest.mark.parametrize("owner", ["fetch", "remote"])
@pytest.mark.parametrize(("stdout", "stderr"), [("", "native error"), ("native error", ""), ("", "")])
def test_failed_cli_lookup_retains_native_failure(owner: str, stdout: str, stderr: str) -> None:
    """Both adapters retain diagnostics and fail even when the failed tool is silent."""
    expected = FETCH_COMMAND if owner == "fetch" else REMOTE_COMMAND
    runner = _runner(expected, returncode=1, stdout=stdout, stderr=stderr)
    with pytest.raises(status.PipelineStatusError, match="lookup failed") as failure:
        if owner == "fetch":
            status.fetch_runs(SHA, repo=REPO, runner=runner)
        else:
            status.remote_head(branch="development", remote="sandboxcom", runner=runner)
    assert "native error" in str(failure.value) if stdout or stderr else "failed" in str(failure.value)


@pytest.mark.parametrize("stdout", ["", "[]", '[null, 7, "invalid", {"databaseId": 17}]'])
def test_fetch_projects_only_object_runs_with_fixed_native_request(stdout: str) -> None:
    """Run records remain dictionary evidence, never arbitrary JSON scalars."""
    expected = [{"databaseId": 17}] if "databaseId" in stdout else []
    assert status.fetch_runs(SHA, repo=REPO, runner=_runner(FETCH_COMMAND, stdout=stdout)) == expected


@pytest.mark.parametrize("stdout", ["", "\n  \n", "abc123\trefs/heads/development\n", "A" * 40, "not-a-head"])
def test_remote_head_rejects_missing_or_noncanonical_identity(stdout: str) -> None:
    """A partial, uppercase or absent remote SHA is not exact pushed identity."""
    with pytest.raises(status.PipelineStatusError, match="no remote head"):
        status.remote_head(
            branch="development", remote="sandboxcom", runner=_runner(REMOTE_COMMAND, stdout=stdout),
        )


def test_remote_head_accepts_only_the_full_native_sha() -> None:
    """Whitespace before the first native record must not break exact identity."""
    assert status.remote_head(
        branch="development", remote="sandboxcom",
        runner=_runner(REMOTE_COMMAND, stdout=f"\n \n{SHA}\trefs/heads/development\n"),
    ) == SHA


@pytest.mark.parametrize("database_id", ["not-an-integer", ["not-an-integer"]])
def test_evaluation_retains_missing_proof_for_malformed_run_id(database_id: str | list[str]) -> None:
    """Malformed IDs remain inert; they cannot invent another required workflow."""
    summary = status.evaluate_runs([
        {"headSha": SHA, "headBranch": "development", "event": "push",
         "workflowName": "Build and Release", "databaseId": database_id,
         "status": "completed", "conclusion": "success"},
    ], SHA, branch="development")
    assert summary.runs[0].run_id == 0
    assert summary.exit_code == 2
    assert summary.missing_workflows == ("Molecule Tests",)


def test_newest_exact_workflow_failure_supersedes_stale_success() -> None:
    """Out-of-order stale success never replaces the newest failed native run."""
    shared = {"headSha": SHA, "headBranch": "development", "event": "push",
              "workflowName": "Build and Release", "status": "completed"}
    summary = status.evaluate_runs([
        dict(shared, databaseId=3, createdAt="2026-10-14T00:00:00Z", conclusion="failure"),
        dict(shared, databaseId=2, createdAt="2026-10-13T00:00:00Z", conclusion="success"),
        dict(shared, databaseId=1, createdAt="2026-10-14T00:00:00Z", conclusion="success"),
    ], SHA, branch="development")
    assert summary.runs[0].run_id == 3
    assert summary.runs[0].state == "failure"
    assert summary.exit_code == 1


def test_local_marker_absence_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """An absent local marker is reported without fabricated state."""
    monkeypatch.chdir(tmp_path)
    status.local_gate()
    assert capsys.readouterr().out == "=== LOCAL GATE ===\n( no gate status file )\n"


@pytest.mark.parametrize("pid", [None, ""])
def test_local_marker_without_active_pid_has_no_liveness_claim(
    pid: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing or empty PID files preserve the actual marker text only."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gate-status").write_text("GATE PENDING\n", encoding="utf-8")
    if pid is not None:
        (tmp_path / ".gate-background.pid").write_text(pid, encoding="utf-8")
    status.local_gate()
    assert capsys.readouterr().out == "=== LOCAL GATE ===\nGATE PENDING\n"


@pytest.mark.parametrize("age", [0, 181])
def test_local_marker_observes_real_process_and_stale_timestamp(
    age: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Signal-zero inspection of this process distinguishes fresh from stale markers."""
    monkeypatch.chdir(tmp_path)
    gate = tmp_path / ".gate-status"
    gate.write_text("GATE PENDING\n", encoding="utf-8")
    (tmp_path / ".gate-background.pid").write_text(str(os.getpid()), encoding="utf-8")
    marker_time = time.time() - age
    os.utime(gate, (marker_time, marker_time))
    status.local_gate()
    output = capsys.readouterr().out
    assert "GATE PENDING" in output
    assert "DEAD" not in output
    assert ("STALLED" in output) is (age > 120)


def test_local_marker_malformed_pid_is_dead_not_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed PID content is explicit failure and never an active-work claim."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gate-status").write_text("GATE PENDING\n", encoding="utf-8")
    (tmp_path / ".gate-background.pid").write_text("malformed", encoding="utf-8")
    status.local_gate()
    assert "DEAD: pid=malformed no longer running" in capsys.readouterr().out
