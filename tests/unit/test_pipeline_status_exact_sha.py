"""Exact-SHA, all-workflow contracts for pipeline status reporting."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_status  # noqa: E402

if TYPE_CHECKING:
    import active_work_status
    import automatic_disk_cleanup
    import check_disk_usage
    import ci_failure_ledger
else:
    from scripts import active_work_status, automatic_disk_cleanup, check_disk_usage, ci_failure_ledger

SHA = "a" * 40
OTHER_SHA = "b" * 40


@pytest.fixture(autouse=True)
def deterministic_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep machine pressure outside exact-workflow unit-test decisions."""
    monkeypatch.setattr(pipeline_status, "automatic_disk_cleanup", automatic_disk_cleanup, raising=False)
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage", lambda: automatic_disk_cleanup.DiskSnapshot(0.0, 80.0))


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
            _run("Wrong event", 7, event="workflow_dispatch"),
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
    makefile = compose_makefile(ROOT / "Makefile")
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
        "PIPELINE_STATUS_OUTPUT",
    ]
    assert "PIPELINE_STATUS_VALIDATE_ONLY=1" in contract["behavior"]


def test_verify_state_uses_current_branch_instead_of_master_remote() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    block = makefile.split("verify-state:", maxsplit=1)[1].split("\n\ngha-usage:", maxsplit=1)[0]
    assert "refs/heads/master" not in block
    assert "refs/heads/$$BRANCH" in block


def _snapshot_local() -> dict[str, object]:
    return {"git": {"branch": "development", "head": SHA},
            "gate": {"state": "FAIL", "status_file": ".gate-status"},
            "open_task_ids": ["S98.1", "S98.4"], "observed_processes": [],
            "task_scope": {"open_count": 2}}


def _capacity_snapshot_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str = "completed", conclusion: str = "success",
) -> tuple[Path, list[str]]:
    path = tmp_path / "current-proof.json"
    runs = [_run("Build and Release", 124, status=status, conclusion=conclusion), _run("Molecule Tests", 101)]
    monkeypatch.setattr(active_work_status, "collect_status", _snapshot_local)
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: runs)
    monkeypatch.setattr(ci_failure_ledger, "fetch_run", lambda run_id, _repo: {
        **next(run for run in runs if run["databaseId"] == run_id), "jobs": [],
    })
    monkeypatch.setattr(automatic_disk_cleanup, "_automatic_cleanup",
                        lambda *_a, **_k: pytest.fail("observation must not clean"))
    return path, ["--remote-only", "--sha", SHA, "--snapshot-output", str(path),
                  "--failure-ledger", str(tmp_path / "absent-ledger.json")]


@pytest.mark.parametrize(
    ("status", "conclusion", "disk_pct", "scratch_mb", "expected_rc", "action", "blocked"),
    [
        ("completed", "success", 91.0, 173.0, 0, "clear_hard_capacity_before_local_proofs", True),
        ("in_progress", "", 91.0, 173.0, 2, "clear_hard_capacity_before_local_proofs", True),
        ("completed", "failure", 91.0, 173.0, 1, "repair_hosted_failure", True),
        ("completed", "success", 90.0, 1000.0, 0, "collect_exact_final_proofs", False),
        ("completed", "success", 80.0, 0.0, 0, "collect_exact_final_proofs", False),
    ],
)
def test_pipeline_snapshot_current_capacity_selects_safe_next_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, conclusion: str,
    disk_pct: float, scratch_mb: float, expected_rc: int, action: str, blocked: bool,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch, status, conclusion)
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(scratch_mb, disk_pct))
    assert pipeline_status.main(args) == expected_rc
    record = json.loads(path.read_text())
    capacity = record["evidence"]["capacity"]
    assert capacity["disk_pct"] == disk_pct
    assert capacity["scratch_mb"] == scratch_mb
    assert capacity["hard_blocked"] is blocked
    assert capacity["disk_limit_kind"] == "hard"
    assert capacity["scratch_limit_kind"] == "soft"
    assert capacity["disk_limit_pct"] == check_disk_usage.DISK_USAGE_PCT_LIMIT
    assert capacity["scratch_limit_mb"] == check_disk_usage.GLUDD_TMP_LIMIT_MB
    assert {item["next_action"] for item in record["evidence"]["missing_proofs"].values()} == {action}
    assert record["release_ready"] is False
    assert record["gate_evidence"] is False


def test_pipeline_snapshot_remeasures_capacity_instead_of_reusing_stale_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch)
    measurements = iter([automatic_disk_cleanup.DiskSnapshot(173.0, 91.0),
                         automatic_disk_cleanup.DiskSnapshot(173.0, 89.0)])
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage", lambda: next(measurements))
    for blocked in (True, False):
        assert pipeline_status.main(args) == 0
        record = json.loads(path.read_text())
        assert record["evidence"]["capacity"]["hard_blocked"] is blocked
        assert record["evidence"]["local"]["gate"]["state"] == "FAIL"
        assert record["gate_evidence"] is False


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1.0, 101.0])
def test_pipeline_snapshot_invalid_capacity_never_grants_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: float,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch)
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(0.0, invalid))
    assert pipeline_status.main(args) == 2
    record = json.loads(path.read_text())
    assert record["state"] == "lookup_error"
    assert record["evidence"]["capacity"] is None
    assert "capacity" in " ".join(record["evidence"]["errors"]).lower()
    assert record["gate_evidence"] is False


def test_pipeline_snapshot_capacity_lookup_error_is_observable_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch)

    def unavailable() -> automatic_disk_cleanup.DiskSnapshot:
        raise automatic_disk_cleanup.DiskInspectionError("capacity inspection unavailable")

    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage", unavailable)
    assert pipeline_status.main(args) == 2
    record = json.loads(path.read_text())
    assert record["state"] == "lookup_error"
    assert record["evidence"]["capacity"] is None
    assert "capacity inspection unavailable" in record["evidence"]["errors"]
    actions = {item["next_action"] for item in record["evidence"]["missing_proofs"].values()}
    assert actions == {"resolve_observation_error"}


@pytest.mark.parametrize(("scratch_mb", "disk_pct"), [(True, 80.0), (False, 80.0), (0.0, True), (0.0, False)])
def test_pipeline_snapshot_boolean_capacity_is_not_a_usage_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch_mb: float, disk_pct: float,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch)
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(scratch_mb, disk_pct))
    assert pipeline_status.main(args) == 2
    record = json.loads(path.read_text())
    assert record["state"] == "lookup_error"
    assert record["evidence"]["capacity"] is None


def test_pipeline_generates_current_handoff_from_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "current-proof.json"
    monkeypatch.setattr(active_work_status, "collect_status", _snapshot_local)
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: [
        _run("Build and Release", 124, status="in_progress", conclusion=""),
        _run("Molecule Tests", 101),
    ])
    monkeypatch.setattr(ci_failure_ledger, "fetch_run", lambda run_id, _repo: {
        **_run("Build and Release" if run_id == 124 else "Molecule Tests", run_id),
        "jobs": [{"name": "gate (3.11)", "status": "completed", "conclusion": "success",
                  "steps": [{"name": "early admission", "status": "completed", "conclusion": "success"}]}],
    })
    assert pipeline_status.main([
        "--remote-only", "--sha", SHA, "--snapshot-output", str(path),
        "--failure-ledger", str(tmp_path / "absent-ledger.json"),
    ]) == 2
    record = json.loads(path.read_text())
    assert record["producer"] == "pipeline-status"
    assert record["identity"] == {"repo": "sandboxcom/gludd", "branch": "development", "sha": SHA}
    assert record["state"] == "pending"
    evidence = record["evidence"]
    assert evidence["local"] == _snapshot_local()
    assert evidence["hosted"]["runs"][0]["jobs"][0]["name"] == "gate (3.11)"
    assert evidence["failure_ledger"]["observed"] is False
    assert set(evidence["missing_proofs"]) == {"S98.1", "S98.4"}
    assert evidence["missing_proofs"]["S98.4"]["hosted_sha"] == SHA
    assert evidence["missing_proofs"]["S98.4"]["canonical_gate"] == "not_collected"
    assert record["gate_evidence"] is False
    assert record["release_ready"] is False


def test_pipeline_lookup_error_replaces_previous_success_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "current-proof.json"
    monkeypatch.setattr(active_work_status, "collect_status", _snapshot_local)
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: [
        _run("Build and Release", 124), _run("Molecule Tests", 101),
    ])
    monkeypatch.setattr(ci_failure_ledger, "fetch_run", lambda run_id, _repo: {
        **_run("Build and Release" if run_id == 124 else "Molecule Tests", run_id), "jobs": [],
    })
    args = ["--remote-only", "--sha", SHA, "--snapshot-output", str(path),
            "--failure-ledger", str(tmp_path / "absent.json")]
    assert pipeline_status.main(args) == 0
    assert json.loads(path.read_text())["state"] == "success"

    def unavailable(*_a: object, **_k: object) -> list[dict[str, object]]:
        raise pipeline_status.PipelineStatusError("upstream unavailable")

    monkeypatch.setattr(pipeline_status, "fetch_runs", unavailable)
    assert pipeline_status.main(args) == 2
    record = json.loads(path.read_text())
    assert record["state"] == "lookup_error"
    assert record["evidence"]["hosted"] is None
    assert "upstream unavailable" in record["evidence"]["errors"]
    assert record["release_ready"] is False


def test_pipeline_validate_only_does_not_create_or_replace_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "manual-history.json"
    path.write_text('{"historical": true}')
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: pytest.fail("no network"))
    assert pipeline_status.main(["--snapshot-output", str(path), "--validate-only"]) == 0
    assert json.loads(path.read_text()) == {"historical": True}


def test_pipeline_snapshot_exposes_failed_job_before_workflow_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "current-proof.json"
    runs = [_run("Build and Release", 124, status="in_progress", conclusion=""),
            _run("Molecule Tests", 101)]
    monkeypatch.setattr(active_work_status, "collect_status", _snapshot_local)
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: runs)
    monkeypatch.setattr(ci_failure_ledger, "fetch_run", lambda run_id, _repo: {
        **next(run for run in runs if run["databaseId"] == run_id),
        "jobs": [{"name": "ansible-ee", "status": "completed", "conclusion": "failure",
                  "steps": [{"name": "Build Ansible execution environment", "conclusion": "failure"}]}]
        if run_id == 124 else [],
    })
    assert pipeline_status.main([
        "--remote-only", "--sha", SHA, "--snapshot-output", str(path),
        "--failure-ledger", str(tmp_path / "absent.json"),
    ]) == 2
    record = json.loads(path.read_text())
    assert record["state"] == "failure"
    assert record["evidence"]["hosted_failures"] == [{
        "workflow": "Build and Release", "run_id": 124, "job": "ansible-ee",
        "steps": ["Build Ansible execution environment"],
    }]
    assert record["evidence"]["missing_proofs"]["S98.4"]["next_action"] == "repair_hosted_failure"
    assert not (tmp_path / "absent.json").exists()


def test_pipeline_snapshot_accepts_the_existing_ledger_detail_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "current-proof.json"
    runs = [_run("Build and Release", 124, status="in_progress", conclusion=""),
            _run("Molecule Tests", 101)]
    monkeypatch.setattr(active_work_status, "collect_status", _snapshot_local)
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: runs)

    def detail(run_id: int, _repo: str) -> dict[str, object]:
        payload = {key: value for key, value in next(
            run for run in runs if run["databaseId"] == run_id
        ).items() if key not in {"event", "createdAt"}}
        payload["jobs"] = []
        return payload

    monkeypatch.setattr(ci_failure_ledger, "fetch_run", detail)
    assert pipeline_status.main([
        "--remote-only", "--sha", SHA, "--snapshot-output", str(path),
        "--failure-ledger", str(tmp_path / "absent.json"),
    ]) == 2
    record = json.loads(path.read_text())
    assert record["state"] == "pending"
    assert record["evidence"]["errors"] == []
    assert record["evidence"]["hosted"]["runs"][0]["source_created_at"] is None


@pytest.mark.parametrize(
    "mode", ["wrong_identity", "wrong_run", "changed_success", "malformed_jobs",
             "malformed_steps", "contradictory_success", "ledger_error", "manual_history"],
)
def test_pipeline_snapshot_collection_failures_never_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    path = tmp_path / "current-proof.json"
    runs = [_run("Build and Release", 124), _run("Molecule Tests", 101)]
    monkeypatch.setattr(active_work_status, "collect_status", _snapshot_local)
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: runs)

    def detail(run_id: int, _repo: str) -> dict[str, object]:
        payload = {**next(run for run in runs if run["databaseId"] == run_id), "jobs": []}
        if mode == "wrong_identity":
            payload["headSha"] = OTHER_SHA
        elif mode == "wrong_run":
            payload["databaseId"] = run_id + 1
        elif mode == "changed_success":
            payload["conclusion"] = "failure"
        elif mode == "malformed_jobs":
            payload["jobs"] = {}
        elif mode in {"malformed_steps", "contradictory_success"}:
            payload["jobs"] = [{"name": "failed", "conclusion": "failure",
                                "steps": None if mode == "malformed_steps" else []}]
        return payload

    monkeypatch.setattr(ci_failure_ledger, "fetch_run", detail)
    if mode == "manual_history":
        path.write_text('{"keep": true}')
    args = ["--remote-only", "--sha", SHA, "--snapshot-output", str(path),
            "--failure-ledger", str(tmp_path / "absent.json")]
    if mode == "ledger_error":
        args.extend(["--ledger-observation-exit-code", "1"])
    assert pipeline_status.main(args) == 2
    record = json.loads(path.read_text())
    if mode == "manual_history":
        assert record == {"keep": True}
    else:
        assert record["state"] == "lookup_error"
        assert record["evidence"]["errors"]
        assert record["release_ready"] is False
