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
    from scripts import ci_failure_ledger
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


def test_local_gate_reports_missing_dead_and_aged_marker_states(
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
    output = capsys.readouterr().out
    assert "marker_age_seconds=499" in output
    assert "pid=42 pid_probe=exists" in output
    assert "gate_identity=unverified progress=unknown" in output
    assert "STALLED" not in output


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


@pytest.mark.parametrize("marker", ["RUNNING", "PASS", "FAIL"])
def test_old_gate_marker_reports_age_and_pid_probe_without_claiming_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], marker: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    gate = tmp_path / ".gate-status"
    pid_file = tmp_path / ".gate-background.pid"
    gate.write_text(f"{marker}\n", encoding="utf-8")
    pid_file.write_text("42", encoding="utf-8")
    os.utime(gate, (1, 1))
    monkeypatch.setattr(pipeline_status.time, "time", lambda: 500)
    probes: list[tuple[int, int]] = []
    monkeypatch.setattr(pipeline_status.os, "kill", lambda pid, signal: probes.append((pid, signal)))
    before = (gate.read_bytes(), pid_file.read_bytes(), gate.stat().st_mtime)

    pipeline_status.local_gate()

    output = capsys.readouterr().out
    assert f"{marker}\n" in output
    assert "marker_age_seconds=499" in output
    assert "pid=42 pid_probe=exists" in output
    assert "gate_identity=unverified progress=unknown" in output
    assert "STALLED" not in output
    assert "DEAD" not in output
    assert probes == [(42, 0)]
    assert (gate.read_bytes(), pid_file.read_bytes(), gate.stat().st_mtime) == before


def test_old_gate_marker_still_reports_an_actual_missing_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gate-status").write_text("RUNNING\n", encoding="utf-8")
    (tmp_path / ".gate-background.pid").write_text("42", encoding="utf-8")
    os.utime(tmp_path / ".gate-status", (1, 1))
    monkeypatch.setattr(pipeline_status.time, "time", lambda: 500)

    def missing(pid: int, signal: int) -> None:
        assert (pid, signal) == (42, 0)
        raise ProcessLookupError("fixture PID no longer exists")

    monkeypatch.setattr(pipeline_status.os, "kill", missing)
    pipeline_status.local_gate()
    output = capsys.readouterr().out
    assert "RUNNING\n" in output
    assert "DEAD: pid=42 no longer running" in output
    assert "pid_probe=exists" not in output
    assert "STALLED" not in output


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
        "SSH_KEY",
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


@pytest.mark.parametrize(("status", "conclusion", "expected_code", "expected_state"), [
    ("completed", "success", 0, "success"),
    ("completed", "failure", 1, "failure"),
    ("in_progress", "", 2, "pending"),
])
def test_old_local_marker_diagnostic_preserves_current_exact_sha_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    status: str, conclusion: str, expected_code: int, expected_state: str,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch, status, conclusion)
    args.remove("--remote-only")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gate-status").write_text("RUNNING\n", encoding="utf-8")
    (tmp_path / ".gate-background.pid").write_text("42", encoding="utf-8")
    os.utime(tmp_path / ".gate-status", (1, 1))
    monkeypatch.setattr(pipeline_status.time, "time", lambda: 500)
    monkeypatch.setattr(pipeline_status.os, "kill", lambda _pid, _signal: None)

    assert pipeline_status.main(args) == expected_code

    output = capsys.readouterr().out
    assert "marker_age_seconds=499" in output
    assert "progress=unknown" in output
    assert "STALLED" not in output
    current = json.loads(path.read_text())
    assert current["identity"] == {"repo": "sandboxcom/gludd", "branch": "development", "sha": SHA}
    assert current["state"] == expected_state
    assert current["release_ready"] is False
    assert current["gate_evidence"] is False
    assert current["evidence"]["local"]["gate"] == {"state": "FAIL", "status_file": ".gate-status"}


@pytest.mark.parametrize("clean", [True, False])
def test_pipeline_failed_family_guidance_uses_the_receipt_owner_on_first_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean: bool,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch, "completed", "failure")
    ledger_path = tmp_path / "absent-ledger.json"
    ledger = ci_failure_ledger.new_ledger()
    ci_failure_ledger.observe_payload(ledger, {
        **_run("Build and Release", 200, sha=OTHER_SHA, conclusion="failure"),
        "jobs": [
            {"name": "unit-2", "status": "completed", "conclusion": "failure", "steps": []},
            {"name": "other", "status": "completed", "conclusion": "failure", "steps": []},
        ],
    }, observed_at="first")
    ci_failure_ledger.write_ledger(ledger_path, ledger)
    before = ledger_path.read_bytes()
    probes: list[str] = []

    def checkout(sha: str) -> None:
        probes.append(sha)
        if not clean:
            raise ci_failure_ledger.LedgerError("candidate checkout is dirty")

    monkeypatch.setattr(ci_failure_ledger, "_require_repair_checkout", checkout)
    assert pipeline_status.main(args) == 1
    record = json.loads(path.read_text())
    guidance = record["evidence"]["repair_guidance"]

    assert probes == [SHA]
    assert len(guidance) == 2
    assert {tuple(item["family_ids"]) for item in guidance} == {(key,) for key in ledger["families"]}
    for item in guidance:
        assert item["candidate_sha"] == (SHA if clean else None)
        assert item["argv"] is None
        assert item["argv_template"][:2] == ["make", "ci-failure-repair"]
        assert "CI_REPAIR_ALL_OPEN=0" in item["argv_template"]
        assert "CI_REPAIR_EVIDENCE_TARGET=<explicit-evidence-target>" in item["argv_template"]
    assert ledger_path.read_bytes() == before
    assert record["release_ready"] is False


def test_pipeline_failure_without_observed_families_requests_explicit_selection_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch, "completed", "failure")
    assert pipeline_status.main(args) == 1
    guidance = json.loads(path.read_text())["evidence"]["repair_guidance"]

    assert len(guidance) == 1
    assert guidance[0]["family_ids"] == []
    assert guidance[0]["argv"] is None
    assert "CI_FAILURE_FAMILIES" in guidance[0]["required_parameters"]
    assert "CI_REPAIR_EVIDENCE_TARGET" in guidance[0]["required_parameters"]


def test_pipeline_guidance_lookup_error_invalidates_previous_current_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch)
    assert pipeline_status.main(args) == 0
    assert json.loads(path.read_text())["state"] == "success"
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch, "completed", "failure")

    def malformed_guidance(*_args: object, **_kwargs: object) -> object:
        raise ci_failure_ledger.LedgerError("family evidence cannot be read")

    monkeypatch.setattr(ci_failure_ledger, "build_repair_guidance", malformed_guidance)
    assert pipeline_status.main(args) == 2
    record = json.loads(path.read_text())
    assert record["state"] == "lookup_error"
    assert record["evidence"]["repair_guidance"] == []
    assert "family evidence cannot be read" in " ".join(record["evidence"]["errors"])
    assert {item["next_action"] for item in record["evidence"]["missing_proofs"].values()} == {
        "resolve_observation_error",
    }


@pytest.mark.parametrize(
    ("status", "conclusion", "disk_pct", "scratch_mb", "expected_rc", "action", "blocked"),
    [
        ("completed", "success", 96.0, 173.0, 0, "clear_hard_capacity_before_local_proofs", True),
        ("in_progress", "", 96.0, 173.0, 2, "clear_hard_capacity_before_local_proofs", True),
        ("completed", "failure", 96.0, 173.0, 1, "repair_hosted_failure", True),
        ("completed", "success", 95.0, 1000.0, 0, "collect_exact_final_proofs", False),
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


def test_user_authorized_95_percent_pipeline_status_does_not_invent_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch)
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(836.3, 91.0))

    assert pipeline_status.main(args) == 0
    record = json.loads(path.read_text())
    assert record["evidence"]["capacity"]["disk_limit_pct"] == 95
    assert record["evidence"]["capacity"]["hard_blocked"] is False
    assert record["evidence"]["capacity"]["scratch_limit_mb"] == 100
    assert record["evidence"]["local"]["gate"]["state"] == "FAIL"
    assert record["release_ready"] is False
    assert record["gate_evidence"] is False


def test_pipeline_snapshot_remeasures_capacity_instead_of_reusing_stale_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch)
    measurements = iter([automatic_disk_cleanup.DiskSnapshot(173.0, 96.0),
                         automatic_disk_cleanup.DiskSnapshot(173.0, 94.0)])
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



def _verdict_history_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    conclusion: str = "success",
) -> tuple[Path, list[str], Path, Path]:
    from scripts import ci_check_cooldown

    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch, conclusion=conclusion)
    history = tmp_path / "history.json"
    restart = tmp_path / "restart-count"
    history.write_text(json.dumps({"last_push_sha": SHA, "retained": "owned"}))
    restart.write_text("2")
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history)
    monkeypatch.setattr(ci_check_cooldown, "STATE_FILE", tmp_path / "cooldown.json")
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart)
    return path, args, history, restart


@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_pipeline_terminal_snapshot_records_owned_push_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conclusion: str,
) -> None:
    path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch, conclusion)
    assert pipeline_status.main(args) == (0 if conclusion == "success" else 1)
    record = json.loads(history.read_text())
    assert record["last_checked_sha"] == SHA
    assert record["last_verdict"] == conclusion
    assert record["retained"] == "owned"
    assert restart.read_text() == "0"
    proof = json.loads(path.read_text())
    assert proof["release_ready"] is False
    assert proof["gate_evidence"] is False


@pytest.mark.parametrize(
    "mode", ["pending", "failure-pending", "missing", "detail-pending", "ledger-error", "foreign-push"],
)
def test_pipeline_incomplete_or_foreign_snapshot_never_verifies_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    _path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)
    expected = 0
    if mode in {"pending", "failure-pending", "missing", "detail-pending"}:
        runs = [_run("Build and Release", 124), _run("Molecule Tests", 101)]
        if mode == "pending":
            runs[0].update(status="in_progress", conclusion="")
            expected = 2
        elif mode == "failure-pending":
            runs[0]["conclusion"] = "failure"
            runs[1].update(status="in_progress", conclusion="")
            expected = 1
        elif mode == "missing":
            runs.pop()
            expected = 2
        else:
            runs[0]["conclusion"] = "failure"
            expected = 1
        monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: runs)

        def detail(run_id: int, _repo: str) -> dict[str, object]:
            result = {**next(run for run in runs if run["databaseId"] == run_id), "jobs": []}
            if mode == "detail-pending" and run_id == 124:
                result.update(status="in_progress", conclusion="")
            return result

        monkeypatch.setattr(ci_failure_ledger, "fetch_run", detail)
    elif mode == "ledger-error":
        args.extend(["--ledger-observation-exit-code", "1"])
        expected = 2
    else:
        history.write_text(json.dumps({"last_push_sha": OTHER_SHA, "retained": "owned"}))
    before = history.read_bytes()
    assert pipeline_status.main(args) == expected
    assert history.read_bytes() == before
    assert restart.read_text() == "2"



@pytest.mark.parametrize("conclusion", ["success", "failure"])
@pytest.mark.parametrize("blocked", ["manual", "foreign-owner", "write-error", "oversized"])
def test_pipeline_rejected_publication_never_authorizes_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conclusion: str, blocked: str,
) -> None:
    if TYPE_CHECKING:
        from proof_observations import write_snapshot
    else:
        from scripts.proof_observations import write_snapshot

    path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch, conclusion)
    previous = None
    if blocked == "manual":
        path.write_text('{"retained": "manual"}')
        previous = path.read_bytes()
    elif blocked == "foreign-owner":
        write_snapshot(
            path, producer="pipeline-status",
            identity={"repo": "foreign/project", "branch": "development", "sha": SHA},
            state="success", evidence={},
        )
        previous = path.read_bytes()
    elif blocked == "write-error":
        def unavailable(*_a: object, **_k: object) -> None:
            raise OSError("snapshot destination unavailable")

        monkeypatch.setattr(pipeline_status, "write_snapshot", unavailable)
    else:
        monkeypatch.setattr(
            active_work_status, "collect_status",
            lambda: {**_snapshot_local(), "oversized": "x" * (2 * 1024 * 1024)},
        )
    before = history.read_bytes()
    assert pipeline_status.main(args) == 2
    assert history.read_bytes() == before
    assert restart.read_text() == "2"
    if previous is not None:
        assert path.read_bytes() == previous


@pytest.mark.parametrize("narrowed", ["foreign-repo", "required-subset"])
def test_pipeline_diagnostic_selectors_cannot_relax_history_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, narrowed: str,
) -> None:
    _path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)
    if narrowed == "foreign-repo":
        args.extend(["--repo", "foreign/project"])
    else:
        args.extend(["--required-workflow", "Molecule Tests"])
        runs = [_run("Molecule Tests", 101)]
        monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: runs)
        monkeypatch.setattr(ci_failure_ledger, "fetch_run", lambda *_a: {**runs[0], "jobs": []})
    before = history.read_bytes()
    assert pipeline_status.main(args) == 0
    assert history.read_bytes() == before
    assert restart.read_text() == "2"


def test_pipeline_publishes_validated_snapshot_before_authorizing_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import ci_check_cooldown

    if TYPE_CHECKING:
        from proof_observations import read_snapshot
    else:
        from scripts.proof_observations import read_snapshot

    path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)
    original = ci_check_cooldown.cmd_record_verdict

    def record_after_publication(verdict: str, sha: str | None = None) -> int:
        proof = read_snapshot(
            path, producer="pipeline-status",
            identity={"repo": pipeline_status.DEFAULT_REPO, "branch": "development", "sha": SHA},
        )
        assert proof["state"] == "success"
        assert proof["release_ready"] is False
        assert proof["gate_evidence"] is False
        return original(verdict, sha)

    monkeypatch.setattr(ci_check_cooldown, "cmd_record_verdict", record_after_publication)
    assert pipeline_status.main(args) == 0
    assert json.loads(history.read_text())["last_checked_sha"] == SHA
    assert restart.read_text() == "0"


@pytest.mark.parametrize("mode", ["foreign-repo", "required-subset", "ledger-error", "foreign-push"])
def test_pipeline_explicit_verdict_requires_canonical_complete_owned_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    _path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)
    index = args.index("--snapshot-output")
    del args[index:index + 2]
    args.append("--record-verdict")
    if mode == "foreign-repo":
        args.extend(["--repo", "foreign/project"])
    elif mode == "required-subset":
        args.extend(["--required-workflow", "Molecule Tests"])
        monkeypatch.setattr(pipeline_status, "fetch_runs",
                            lambda *_a, **_k: [_run("Molecule Tests", 101)])
    elif mode == "ledger-error":
        args.extend(["--ledger-observation-exit-code", "1"])
    else:
        history.write_text(json.dumps({"last_push_sha": OTHER_SHA, "retained": "owned"}))
    before = history.read_bytes()
    assert pipeline_status.main(args) == 2
    assert history.read_bytes() == before
    assert restart.read_text() == "2"

def test_pipeline_owned_terminal_verdict_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import ci_check_cooldown

    _path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)
    record = json.loads(history.read_text())
    record.update(last_checked_sha=SHA, last_verdict="success")
    history.write_text(json.dumps(record))
    monkeypatch.setattr(ci_check_cooldown, "cmd_record_verdict",
                        lambda *_a: pytest.fail("already observed verdict must not be rewritten"))
    before = history.read_bytes()
    assert pipeline_status.main(args) == 0
    assert history.read_bytes() == before
    assert restart.read_text() == "2"


def test_pipeline_verdict_write_error_is_visible_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import ci_check_cooldown

    path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)

    def unavailable(*_a: object) -> int:
        raise OSError("owned verdict writer unavailable")

    monkeypatch.setattr(ci_check_cooldown, "cmd_record_verdict", unavailable)
    before = history.read_bytes()
    assert pipeline_status.main(args) == 2
    proof = json.loads(path.read_text())
    assert proof["state"] == "lookup_error"
    assert "owned verdict writer unavailable" in " ".join(proof["evidence"]["errors"])
    assert history.read_bytes() == before
    assert restart.read_text() == "2"


@pytest.mark.parametrize("validate_only", [False, True])
def test_pipeline_verdict_observation_requires_real_snapshot_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, validate_only: bool,
) -> None:
    _path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)
    if validate_only:
        args.append("--validate-only")
    else:
        index = args.index("--snapshot-output")
        del args[index:index + 2]
    before = history.read_bytes()
    assert pipeline_status.main(args) == 0
    assert history.read_bytes() == before
    assert restart.read_text() == "2"


@pytest.mark.parametrize("changed", ["sha", "branch"])
def test_pipeline_verdict_observation_requires_matching_snapshot_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    _path, args, history, restart = _verdict_history_sources(tmp_path, monkeypatch)
    parsed = pipeline_status._parser().parse_args(args)
    summary = pipeline_status.evaluate_runs(
        [_run("Build and Release", 124), _run("Molecule Tests", 101)],
        SHA, branch="development",
    )
    observed_sha = SHA
    if changed == "sha":
        observed_sha = OTHER_SHA
    else:
        parsed.branch = "another-branch"
    before = history.read_bytes()
    assert pipeline_status._publish_snapshot(parsed, summary, sha=observed_sha)
    assert history.read_bytes() == before
    assert restart.read_text() == "2"


@pytest.mark.parametrize(("case", "code", "action", "outcome"), [
    ("missing", 2, "establish_exact_sha_push_evidence", "missing"),
    ("partial", 2, "establish_exact_sha_push_evidence", "missing"),
    ("pending", 2, "await_exact_sha_runs", "pending"),
    ("failed", 1, "repair_hosted_failure", "failed"),
    ("passed", 0, "collect_required_deployment_proof", "passed"),
])
def test_hosted_guidance_distinguishes_missing_runs_from_active_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    case: str, code: int, action: str, outcome: str,
) -> None:
    runs = [_run("Build and Release", 124), _run("Molecule Tests", 101)]
    if case == "missing":
        runs = []
    elif case == "partial":
        runs = runs[:1]
    elif case == "pending":
        runs[0] = _run("Build and Release", 124, status="in_progress", conclusion="")
    elif case == "failed":
        runs[0] = _run("Build and Release", 124, conclusion="failure")
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: runs)
    assert pipeline_status.main(["--remote-only", "--sha", SHA]) == code
    captured = capsys.readouterr()
    assert f"AGENT_NEXT target=pipeline-status outcome={outcome}" in captured.err
    assert f"hosted_next_action={action}" in captured.err
    assert f"sha={SHA}" in captured.err
    assert "gate_evidence=false release_ready=false" in captured.err
    assert "AGENT_NEXT" not in captured.out
    marker = {0: "CI GREEN", 1: "CI RED", 2: "CI PENDING"}[code]
    assert f"{marker}: sha={SHA}" in captured.out


@pytest.mark.parametrize("missing", [False, True])
def test_hosted_next_action_remains_separate_from_hard_local_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: bool,
) -> None:
    path, args = _capacity_snapshot_sources(tmp_path, monkeypatch, "in_progress", "")
    if missing:
        monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: [])
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(173.0, 96.0))
    assert pipeline_status.main(args) == 2
    record = json.loads(path.read_text())
    assert record["evidence"]["capacity"]["hard_blocked"] is True
    for item in record["evidence"]["missing_proofs"].values():
        assert item["next_action"] == "clear_hard_capacity_before_local_proofs"
        assert item["hosted_next_action"] == (
            "establish_exact_sha_push_evidence" if missing else "await_exact_sha_runs"
        )
    assert record["release_ready"] is False and record["gate_evidence"] is False


@pytest.mark.parametrize("case", ["planned", "lookup-error", "snapshot-error"])
def test_hosted_guidance_preserves_plans_and_observation_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str,
) -> None:
    def lookup(*_a: object, **_k: object) -> list[dict[str, object]]:
        if case == "planned":
            pytest.fail("offline plan must not query GitHub")
        if case == "lookup-error":
            raise pipeline_status.PipelineStatusError("fixture lookup refusal")
        return [_run("Build and Release", 124), _run("Molecule Tests", 101)]
    monkeypatch.setattr(pipeline_status, "fetch_runs", lookup)
    if case == "snapshot-error":
        monkeypatch.setattr(pipeline_status, "_publish_snapshot", lambda *_a, **_k: False)
    args = ["--remote-only", "--sha", SHA]
    if case == "planned":
        args.append("--validate-only")
    assert pipeline_status.main(args) == (0 if case == "planned" else 2)
    captured = capsys.readouterr()
    outcome = "planned" if case == "planned" else "lookup_error"
    assert f"AGENT_NEXT target=pipeline-status outcome={outcome}" in captured.err
    assert "gate_evidence=false release_ready=false" in captured.err
    assert "outcome=passed" not in captured.err


def test_advisory_hosted_guidance_cannot_replace_native_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RefusedGuidance:
        calls = 0
        def write(self, _text: str) -> None:
            self.calls += 1
            raise OSError("fixture advisory stream refusal")
    stream = RefusedGuidance()
    monkeypatch.setattr(pipeline_status, "fetch_runs", lambda *_a, **_k: [])
    with monkeypatch.context() as scope:
        scope.setattr(sys, "stderr", stream)
        assert pipeline_status.main(["--remote-only", "--sha", SHA]) == 2
    assert stream.calls == 1
