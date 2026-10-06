"""Exact-SHA, all-workflow contracts for pipeline status reporting."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_status  # noqa: E402

SHA = "a" * 40
OTHER_SHA = "b" * 40


def _run(
    workflow: str,
    run_id: int,
    *,
    status: str = "completed",
    conclusion: str = "success",
    sha: str = SHA,
    event: str = "push",
) -> dict[str, object]:
    return {
        "workflowName": workflow,
        "databaseId": run_id,
        "status": status,
        "conclusion": conclusion,
        "headSha": sha,
        "headBranch": "development",
        "event": event,
        "createdAt": f"2026-09-29T05:{run_id % 60:02d}:00Z",
        "url": f"https://github.example/actions/runs/{run_id}",
    }


def test_exact_sha_summary_reports_every_required_workflow_and_failure() -> None:
    summary = pipeline_status.evaluate_runs(
        [
            _run("Build and Release", 124, conclusion="failure"),
            _run("Molecule Tests", 101, conclusion="failure"),
            _run("Build and Release", 99, sha=OTHER_SHA),
        ],
        SHA,
        branch="development",
    )

    assert summary.exit_code == 1
    assert summary.sha == SHA
    assert [run.workflow for run in summary.runs] == [
        "Build and Release",
        "Molecule Tests",
    ]
    assert [run.run_id for run in summary.runs] == [124, 101]
    rendered = "\n".join(summary.lines())
    assert "run 124" in rendered
    assert "run 101" in rendered
    assert "2 failed" in rendered


def test_exact_sha_summary_uses_latest_run_per_workflow() -> None:
    summary = pipeline_status.evaluate_runs(
        [
            _run("Build and Release", 120, conclusion="failure"),
            _run("Build and Release", 124, conclusion="success"),
            _run("Molecule Tests", 101, conclusion="success"),
        ],
        SHA,
        branch="development",
    )

    assert summary.exit_code == 0
    assert [run.run_id for run in summary.runs] == [124, 101]


def test_exact_sha_summary_keeps_newest_when_older_run_appears_later() -> None:
    summary = pipeline_status.evaluate_runs(
        [
            _run("Build and Release", 124, conclusion="success"),
            _run("Build and Release", 120, conclusion="failure"),
            _run("Molecule Tests", 101, conclusion="success"),
        ],
        SHA,
        branch="development",
    )

    assert summary.exit_code == 0
    assert [run.run_id for run in summary.runs] == [124, 101]


def test_exact_sha_summary_normalizes_unknown_workflow_and_invalid_run_id() -> None:
    malformed = _run("Extra Workflow", 8)
    malformed["databaseId"] = "not-an-integer"
    summary = pipeline_status.evaluate_runs(
        [
            _run("Build and Release", 124),
            _run("Molecule Tests", 101),
            malformed,
            _run("Wrong event", 7, event="schedule"),
        ],
        SHA,
        branch="development",
    )

    assert summary.exit_code == 0
    assert [run.workflow for run in summary.runs] == [
        "Build and Release",
        "Molecule Tests",
        "Extra Workflow",
    ]
    assert summary.runs[-1].run_id == 0


def test_exact_sha_summary_observes_dispatched_non_default_branch_run() -> None:
    branch = "agent/core-dependency-inventory-1b"
    dispatched = _run(
        "Build and Release",
        37407005371,
        status="queued",
        conclusion="",
        event="workflow_dispatch",
    )
    dispatched["headBranch"] = branch

    summary = pipeline_status.evaluate_runs([dispatched], SHA, branch=branch)

    assert [run.run_id for run in summary.runs] == [37407005371]
    assert summary.pending == summary.runs
    assert summary.missing_workflows == ("Molecule Tests",)
    assert summary.exit_code == 2
    rendered = "\n".join(summary.lines())
    assert "PENDING Build and Release run 37407005371" in rendered
    assert "missing required workflow: Molecule Tests" in rendered


@pytest.mark.parametrize(
    ("runs", "expected_code", "expected_text"),
    [
        (
            [_run("Build and Release", 124)],
            2,
            "missing required workflow: Molecule Tests",
        ),
        (
            [
                _run("Build and Release", 124),
                _run("Molecule Tests", 101, status="in_progress", conclusion=""),
            ],
            2,
            "1 pending",
        ),
        ([], 2, "no workflow runs found"),
    ],
)
def test_exact_sha_summary_fails_closed_when_evidence_is_incomplete(
    runs: list[dict[str, object]], expected_code: int, expected_text: str
) -> None:
    summary = pipeline_status.evaluate_runs(runs, SHA, branch="development")

    assert summary.exit_code == expected_code
    assert expected_text in "\n".join(summary.lines())


def test_fetch_runs_queries_all_workflows_for_exact_sha() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    assert pipeline_status.fetch_runs(SHA, repo="sandboxcom/gludd", runner=runner) == []
    command = calls[0]
    assert command[:3] == ["gh", "run", "list"]
    assert command[command.index("--commit") + 1] == SHA
    assert "--workflow" not in command
    assert "workflowName" in command[command.index("--json") + 1]


def test_fetch_runs_surfaces_github_lookup_failure() -> None:
    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "authentication failed")

    with pytest.raises(pipeline_status.PipelineStatusError, match="authentication failed"):
        pipeline_status.fetch_runs(SHA, repo="sandboxcom/gludd", runner=runner)


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("not-json", "invalid JSON"),
        ('{"workflowName":"Build and Release"}', "non-list"),
        (json.dumps([{}] * 100), "reached its limit"),
    ],
)
def test_fetch_runs_rejects_incomplete_or_malformed_evidence(
    stdout: str, message: str
) -> None:
    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    with pytest.raises(pipeline_status.PipelineStatusError, match=message):
        pipeline_status.fetch_runs(SHA, repo="sandboxcom/gludd", runner=runner)


def test_fetch_runs_rejects_invalid_sha_and_process_start_failure() -> None:
    with pytest.raises(pipeline_status.PipelineStatusError, match="full lowercase"):
        pipeline_status.fetch_runs("abc", repo="sandboxcom/gludd")

    def runner(_: list[str], **__: object) -> subprocess.CompletedProcess[str]:
        raise OSError("gh unavailable")

    with pytest.raises(pipeline_status.PipelineStatusError, match="gh unavailable"):
        pipeline_status.fetch_runs(SHA, repo="sandboxcom/gludd", runner=runner)


def test_remote_head_is_bound_to_requested_branch() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"{SHA}\trefs/heads/development\n", "")

    assert pipeline_status.remote_head(
        branch="development", remote="sandboxcom", runner=runner
    ) == SHA
    assert calls == [["git", "ls-remote", "sandboxcom", "refs/heads/development"]]


def test_remote_head_fails_closed_on_empty_remote_result() -> None:
    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(pipeline_status.PipelineStatusError, match="no remote head"):
        pipeline_status.remote_head(
            branch="development", remote="sandboxcom", runner=runner
        )


@pytest.mark.parametrize("mode", ["returncode", "exception"])
def test_remote_head_surfaces_transport_failure(mode: str) -> None:
    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if mode == "exception":
            raise OSError("git unavailable")
        return subprocess.CompletedProcess(argv, 1, "", "remote denied")

    expected = "git unavailable" if mode == "exception" else "remote denied"
    with pytest.raises(pipeline_status.PipelineStatusError, match=expected):
        pipeline_status.remote_head(
            branch="development", remote="sandboxcom", runner=runner
        )


def test_local_gate_reports_missing_dead_and_stalled_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    pipeline_status.local_gate()
    assert "no gate status file" in capsys.readouterr().out

    gate = tmp_path / ".gate-status"
    pid_file = tmp_path / ".gate-background.pid"
    gate.write_text("RUNNING\n", encoding="utf-8")
    pid_file.write_text("bad-pid", encoding="utf-8")
    pipeline_status.local_gate()
    assert "DEAD: pid=bad-pid" in capsys.readouterr().out

    pid_file.write_text("42", encoding="utf-8")
    os.utime(gate, (1, 1))
    monkeypatch.setattr(pipeline_status.time, "time", lambda: 500)
    monkeypatch.setattr(pipeline_status.os, "kill", lambda _pid, _signal: None)
    pipeline_status.local_gate()
    assert "STALLED" in capsys.readouterr().out


def test_local_gate_tolerates_missing_empty_and_fresh_pid_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    gate = tmp_path / ".gate-status"
    pid_file = tmp_path / ".gate-background.pid"
    gate.write_text("PASS\n", encoding="utf-8")

    pipeline_status.local_gate()
    assert "PASS" in capsys.readouterr().out

    pid_file.write_text("", encoding="utf-8")
    pipeline_status.local_gate()
    assert "DEAD" not in capsys.readouterr().out

    pid_file.write_text("42", encoding="utf-8")
    current_mtime = gate.stat().st_mtime
    monkeypatch.setattr(pipeline_status.time, "time", lambda: current_mtime)
    monkeypatch.setattr(pipeline_status.os, "kill", lambda _pid, _signal: None)
    pipeline_status.local_gate()
    output = capsys.readouterr().out
    assert "STALLED" not in output
    assert "DEAD" not in output


def test_validate_only_exercises_aggregate_without_network(capsys: pytest.CaptureFixture[str]) -> None:
    assert pipeline_status.main(["status", "--validate-only"]) == 0
    output = capsys.readouterr().out
    assert "PIPELINE_STATUS_VALIDATE_ONLY_PASS" in output
    assert "Build and Release" in output
    assert "Molecule Tests" in output


def test_main_reports_exact_remote_verdict_and_collection_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pipeline_status, "remote_head", lambda **_: SHA)
    monkeypatch.setattr(
        pipeline_status,
        "fetch_runs",
        lambda *_args, **_kwargs: [
            _run("Build and Release", 124),
            _run("Molecule Tests", 101),
        ],
    )
    assert pipeline_status.main(["--remote-only"]) == 0
    assert "2 passed" in capsys.readouterr().out

    def fail_remote(**_: object) -> str:
        raise pipeline_status.PipelineStatusError("remote unavailable")

    monkeypatch.setattr(pipeline_status, "remote_head", fail_remote)
    assert pipeline_status.main(["--remote-only"]) == 2
    assert "CI ERROR: remote unavailable" in capsys.readouterr().out


def test_main_honors_explicit_sha_and_required_workflow(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    custom = _run("Custom Release", 900)
    monkeypatch.setattr(
        pipeline_status,
        "fetch_runs",
        lambda *_args, **_kwargs: [custom],
    )
    monkeypatch.setattr(
        pipeline_status,
        "remote_head",
        lambda **_: pytest.fail("explicit SHA must bypass remote lookup"),
    )

    assert (
        pipeline_status.main(
            [
                "--remote-only",
                "--sha",
                SHA,
                "--required-workflow",
                "Custom Release",
            ]
        )
        == 0
    )
    assert "Custom Release" in capsys.readouterr().out


def test_make_target_forwards_explicit_identity_and_has_safe_contract() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = makefile.split("pipeline-status:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "PIPELINE_STATUS_REPO" in block
    assert "PIPELINE_STATUS_BRANCH" in block
    assert "PIPELINE_STATUS_REMOTE" in block
    assert "PIPELINE_STATUS_SHA" in block
    assert "PIPELINE_STATUS_FAILURE_LEDGER" in block
    assert "PIPELINE_STATUS_VALIDATE_ONLY" in block

    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contract = next(
        item for item in payload["targets"] if item["name"] == "pipeline-status"
    )
    assert contract["make_variables"] == [
        "PIPELINE_STATUS_REPO",
        "PIPELINE_STATUS_BRANCH",
        "PIPELINE_STATUS_REMOTE",
        "PIPELINE_STATUS_SHA",
        "PIPELINE_STATUS_FAILURE_LEDGER",
        "PIPELINE_STATUS_VALIDATE_ONLY",
    ]
    assert "PIPELINE_STATUS_VALIDATE_ONLY=1" in contract["behavior"]


def test_verify_state_uses_current_branch_instead_of_master_remote() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = makefile.split("verify-state:", maxsplit=1)[1].split("\n\ngha-usage:", maxsplit=1)[0]
    assert "refs/heads/master" not in block
    assert "refs/heads/$$BRANCH" in block
