"""One pipeline CLI preserves independent native ledger and status observations."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

if TYPE_CHECKING:
    import active_work_status
    import automatic_disk_cleanup
    import pipeline_status as owner
    from proof_observations import read_snapshot, write_snapshot
    from scripts import ci_check_cooldown, ci_failure_ledger
else:
    from scripts import active_work_status, automatic_disk_cleanup, ci_check_cooldown, ci_failure_ledger
    from scripts import pipeline_status as owner
    from scripts.proof_observations import read_snapshot, write_snapshot

SHA = "a" * 40
OTHER_SHA = "b" * 40


class _FlushFaultOutput(io.StringIO):
    def __init__(self, label: str, attempts: list[str]) -> None:
        super().__init__()
        self.label = label
        self.attempts = attempts
        self.fail_next = False

    def flush(self) -> None:
        self.attempts.append(self.label)
        if self.fail_next:
            self.fail_next = False
            raise OSError(f"fixture {self.label} flush unavailable")
        super().flush()


@pytest.mark.parametrize(("ledger_rc", "status_rc"), [(0, 0), (5, 0), (0, 7), (5, 7)])
def test_public_ledger_runs_first_and_retains_both_exit_precedences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ledger_rc: int, status_rc: int,
) -> None:
    phases: list[str] = []
    observed: list[list[str]] = []
    ledger = tmp_path / "ledger.json"

    def observe(argv: Sequence[str] | None = None) -> int:
        assert argv is not None
        observed.append(list(argv))
        phases.append("ledger")
        print("LEDGER-OUTPUT")
        return ledger_rc

    def status(args: argparse.Namespace) -> int:
        phases.append("status")
        assert args.ledger_observation_exit_code == ledger_rc
        assert args.failure_ledger == ledger
        assert args.snapshot_output == tmp_path / "snapshot.json"
        print("STATUS-OUTPUT")
        return status_rc

    monkeypatch.setattr(ci_failure_ledger, "main", observe)
    monkeypatch.setattr(owner, "_status", status, raising=False)
    result = owner.main([
        "status", "--observe-ledger", "--repo", "example/project",
        "--branch", "release-candidate/v012", "--remote", "origin", "--sha", SHA,
        "--failure-ledger", str(ledger), "--snapshot-output", str(tmp_path / "snapshot.json"),
    ])

    assert result == (ledger_rc or status_rc)
    assert phases == ["ledger", "status"]
    assert observed == [[
        "observe-sha", "--repo", "example/project", "--branch", "release-candidate/v012",
        "--remote", "origin", "--ledger", str(ledger), "--sha", SHA,
    ]]
    output = capsys.readouterr().out
    assert output.index("LEDGER-OUTPUT") < output.index("STATUS-OUTPUT")
    failure = f"pipeline-status: failure-ledger observation failed rc={ledger_rc}"
    assert (failure in output) is bool(ledger_rc)
    if ledger_rc:
        assert output.index("STATUS-OUTPUT") < output.index(failure)


@pytest.mark.parametrize("pinned", [False, True])
def test_public_ledger_and_status_keep_independent_fresh_native_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned: bool,
) -> None:
    phases: list[tuple[str, ...]] = []

    def ledger_head(branch: str, remote: str) -> str:
        phases.append(("ledger-head", branch, remote))
        return SHA

    def status_head(*, branch: str, remote: str) -> str:
        phases.append(("status-head", branch, remote))
        return OTHER_SHA

    def index(sha: str, repository: str) -> list[dict[str, object]]:
        phases.append(("ledger-query", sha, repository))
        return []

    def runs(sha: str, *, repo: str) -> list[dict[str, object]]:
        phases.append(("status-query", sha, repo))
        return []

    monkeypatch.setattr(ci_failure_ledger, "remote_head", ledger_head)
    monkeypatch.setattr(ci_failure_ledger, "fetch_run_index", index)
    monkeypatch.setattr(owner, "remote_head", status_head)
    monkeypatch.setattr(owner, "fetch_runs", runs)
    monkeypatch.setattr(owner, "local_gate", lambda: phases.append(("local",)))
    argv = [
        "--observe-ledger", "--repo", "example/project", "--branch", "candidate",
        "--remote", "origin", "--failure-ledger", str(tmp_path / "ledger.json"),
    ]
    if pinned:
        argv.extend(["--sha", SHA])
    assert owner.main(argv) == 2
    assert phases == (
        [("ledger-query", SHA, "example/project"), ("local",),
         ("status-query", SHA, "example/project")]
        if pinned else
        [("ledger-head", "candidate", "origin"),
         ("ledger-query", SHA, "example/project"), ("local",),
         ("status-head", "candidate", "origin"),
         ("status-query", OTHER_SHA, "example/project")]
    )


@pytest.mark.parametrize(
    ("exit_value", "expected_rc"),
    [(3, 3), (-1, 255), ("native observer stopped", 1), (None, 0), ("unexpected", 1)],
)
def test_observer_process_failures_do_not_prevent_the_status_observation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    exit_value: int | str | None, expected_rc: int,
) -> None:
    phases: list[str] = []

    def observe(argv: Sequence[str] | None = None) -> NoReturn:
        phases.append("ledger")
        if exit_value == "unexpected":
            raise RuntimeError("native observer exception")
        raise SystemExit(exit_value)

    def status(args: argparse.Namespace) -> int:
        phases.append("status")
        assert args.ledger_observation_exit_code == expected_rc
        return 7

    monkeypatch.setattr(ci_failure_ledger, "main", observe)
    monkeypatch.setattr(owner, "_status", status, raising=False)
    assert owner.main(["--observe-ledger"]) == (expected_rc or 7)
    assert phases == ["ledger", "status"]
    captured = capsys.readouterr()
    if exit_value == "unexpected":
        assert "Traceback" in captured.err
        assert "RuntimeError: native observer exception" in captured.err
    elif isinstance(exit_value, str):
        assert exit_value in captured.err


def test_failed_fresh_ledger_invalidates_prior_success_and_terminal_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = tmp_path / "current.json"
    identity = {"repo": owner.DEFAULT_REPO, "branch": "development", "sha": SHA}
    write_snapshot(snapshot, producer="pipeline-status", identity=identity,
                         state="success", evidence={})
    phases: list[str] = []
    runs = [
        {"workflowName": workflow, "databaseId": number, "headSha": SHA,
         "headBranch": "development", "event": "push", "status": "completed",
         "conclusion": "success", "createdAt": "2026-10-20T00:00:00Z", "url": ""}
        for number, workflow in enumerate(owner.DEFAULT_REQUIRED_WORKFLOWS, start=1)
    ]

    def unavailable(sha: str, repository: str) -> NoReturn:
        phases.append("ledger")
        assert sha == SHA and repository == owner.DEFAULT_REPO
        raise ci_failure_ledger.LedgerError("fresh ledger unavailable")

    def fetch(sha: str, *, repo: str) -> list[dict[str, object]]:
        phases.append("status")
        assert sha == SHA and repo == owner.DEFAULT_REPO
        return runs

    def detail(run_id: int, repository: str) -> dict[str, object]:
        assert repository == owner.DEFAULT_REPO
        return {**next(run for run in runs if run["databaseId"] == run_id), "jobs": []}

    def forbidden_history(*args: object) -> NoReturn:
        raise AssertionError("failed ledger cannot record a successful verdict")

    monkeypatch.setattr(ci_failure_ledger, "fetch_run_index", unavailable)
    monkeypatch.setattr(ci_failure_ledger, "fetch_run", detail)
    monkeypatch.setattr(owner, "fetch_runs", fetch)
    monkeypatch.setattr(active_work_status, "collect_status", lambda: {
        "open_task_ids": [], "git": {"head": SHA},
    })
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(0.0, 80.0))
    monkeypatch.setattr(ci_check_cooldown, "cmd_record_verdict", forbidden_history)
    assert owner.main([
        "--observe-ledger", "--remote-only", "--sha", SHA,
        "--failure-ledger", str(tmp_path / "ledger.json"), "--snapshot-output", str(snapshot),
    ]) == 2
    assert phases == ["ledger", "status"]

    current = read_snapshot(snapshot, producer="pipeline-status", identity=identity)
    assert current["state"] == "lookup_error"
    assert current["evidence"]["errors"] == ["failure-ledger observation failed rc=2"]
    assert current["release_ready"] is False
    output = capsys.readouterr()
    assert "PIPELINE-TERMINAL-OBSERVED" not in output.out
    assert "fresh ledger unavailable" in output.err


def test_validate_only_runs_both_existing_plans_without_queries_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("validation cannot query or publish")

    monkeypatch.setattr(ci_failure_ledger, "fetch_run_index", forbidden)
    monkeypatch.setattr(owner, "fetch_runs", forbidden)
    monkeypatch.setattr(owner, "remote_head", forbidden)
    monkeypatch.setattr(owner, "write_snapshot", forbidden)
    ledger = tmp_path / "ledger.json"
    snapshot = tmp_path / "snapshot.json"
    assert owner.main([
        "--observe-ledger", "--validate-only", "--failure-ledger", str(ledger),
        "--snapshot-output", str(snapshot),
    ]) == 0
    output = capsys.readouterr().out
    assert output.index("CI_FAILURE_LEDGER_OBSERVE_SHA_VALIDATE_ONLY_PASS") < output.index(
        "PIPELINE_STATUS_VALIDATE_ONLY_PASS",
    )
    assert not ledger.exists()
    assert not snapshot.exists()


@pytest.mark.parametrize("ledger_rc", [0, 5])
@pytest.mark.parametrize("status_error", ["exception", "exit"])
def test_status_process_failure_retains_observer_exit_precedence(
    monkeypatch: pytest.MonkeyPatch, ledger_rc: int, status_error: str,
) -> None:
    def fail(args: argparse.Namespace) -> NoReturn:
        if status_error == "exit":
            raise SystemExit(7)
        raise RuntimeError("native status exception")

    monkeypatch.setattr(ci_failure_ledger, "main", lambda argv: ledger_rc)
    monkeypatch.setattr(owner, "_status", fail)
    assert owner.main(["--observe-ledger"]) == (ledger_rc or (7 if status_error == "exit" else 1))


@pytest.mark.parametrize("pinned", [False, True])
def test_single_cli_still_launches_both_native_github_queries(tmp_path: Path, pinned: bool) -> None:
    capture = tmp_path / "native.jsonl"
    for name in ("git", "gh"):
        executable = tmp_path / name
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\nfrom pathlib import Path\n"
            f"with Path({str(capture)!r}).open('a') as stream:\n"
            f"    stream.write(json.dumps([{name!r}, *sys.argv[1:]]) + '\\n')\n"
            + (f"print({SHA!r} + '\\trefs/heads/development')\n" if name == "git" else "print('[]')\n"),
            encoding="utf-8",
        )
        executable.chmod(0o755)
    command = [
        sys.executable, str(Path(owner.__file__).resolve()), "status",
        "--observe-ledger", "--remote-only", "--repo", owner.DEFAULT_REPO,
        "--branch", "development", "--remote", "sandboxcom",
        "--failure-ledger", str(tmp_path / "ledger.json"),
    ]
    if pinned:
        command.extend(["--sha", SHA])
    result = subprocess.run(
        command, cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
             "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [call[0] for call in calls] == (["gh", "gh"] if pinned else ["git", "gh", "git", "gh"])
    for call in calls:
        if call[0] == "git":
            assert call == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]
        else:
            assert call[:3] == ["gh", "run", "list"]
            assert call[call.index("--commit") + 1] == SHA
            assert call[call.index("--limit") + 1] == "100"
            assert owner.DEFAULT_REPO in call
            assert {"headSha", "headBranch", "workflowName", "event"}.issubset(
                call[call.index("--json") + 1].split(","),
            )


@pytest.mark.parametrize("ledger_error", [False, True])
def test_ledger_output_is_flushed_before_the_next_native_observation(
    monkeypatch: pytest.MonkeyPatch, ledger_error: bool,
) -> None:
    class FlushedOutput(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.flushed = ""

        def flush(self) -> None:
            self.flushed = self.getvalue()
            super().flush()

    output, errors = FlushedOutput(), FlushedOutput()
    observed: list[tuple[str, str]] = []

    def observe(argv: Sequence[str] | None = None) -> int:
        print("LEDGER-OUTPUT")
        print("LEDGER-DIAGNOSTIC", file=sys.stderr)
        if ledger_error:
            raise RuntimeError("ledger failed after output")
        return 0

    def status(args: argparse.Namespace) -> int:
        observed.append((output.flushed, errors.flushed))
        return 0

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", output)
        scoped.setattr(sys, "stderr", errors)
        scoped.setattr(ci_failure_ledger, "main", observe)
        scoped.setattr(owner, "_status", status)
        assert owner.main(["--observe-ledger"]) == int(ledger_error)
    assert len(observed) == 1
    assert observed[0][0] == "LEDGER-OUTPUT\n"
    assert observed[0][1].startswith("LEDGER-DIAGNOSTIC\n")
    if ledger_error:
        assert "RuntimeError: ledger failed after output" in observed[0][1]


@pytest.mark.parametrize("broken_stream_name", ["stdout", "stderr", "both"])
@pytest.mark.parametrize("cleanup_stage", ["ledger", "status"])
@pytest.mark.parametrize("ledger_code", [0, 5])
def test_cleanup_failure_attempts_both_streams_and_preserves_native_precedence(
    monkeypatch: pytest.MonkeyPatch, broken_stream_name: str, cleanup_stage: str, ledger_code: int,
) -> None:
    attempts: list[str] = []
    output = _FlushFaultOutput("stdout", attempts)
    errors = _FlushFaultOutput("stderr", attempts)
    phases: list[str] = []
    observer_codes: list[int] = []

    def arm() -> None:
        output.fail_next = broken_stream_name in {"stdout", "both"}
        errors.fail_next = broken_stream_name in {"stderr", "both"}

    def ledger(argv: Sequence[str] | None = None) -> int:
        phases.append("ledger")
        if cleanup_stage == "ledger":
            arm()
        return ledger_code

    def status(args: argparse.Namespace) -> int:
        phases.append("status")
        observer_codes.append(args.ledger_observation_exit_code)
        if cleanup_stage == "status":
            arm()
        return 7

    result: int | None = None
    raised = ""
    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", output)
        scoped.setattr(sys, "stderr", errors)
        scoped.setattr(ci_failure_ledger, "main", ledger)
        scoped.setattr(owner, "_status", status)
        try:
            result = owner.main(["--observe-ledger"])
        except OSError as exc:
            raised = str(exc)

    assert raised == ""
    assert phases == ["ledger", "status"]
    assert attempts == ["stdout", "stderr", "stdout", "stderr"]
    assert observer_codes == [120 if cleanup_stage == "ledger" else ledger_code]
    assert result == (120 if cleanup_stage == "ledger" else ledger_code or 120)
    assert f"fixture {broken_stream_name if broken_stream_name != 'both' else 'stdout'}" in errors.getvalue()


@pytest.mark.parametrize("broken_stream_name", ["stdout", "stderr"])
def test_cleanup_failure_replaces_prior_success_without_authorizing_terminal_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken_stream_name: str,
) -> None:
    snapshot = tmp_path / "current.json"
    identity = {"repo": owner.DEFAULT_REPO, "branch": owner.DEFAULT_BRANCH, "sha": SHA}
    write_snapshot(snapshot, producer="pipeline-status", identity=identity, state="success", evidence={})
    attempts: list[str] = []
    output = _FlushFaultOutput("stdout", attempts)
    errors = _FlushFaultOutput("stderr", attempts)
    output.fail_next = broken_stream_name == "stdout"
    errors.fail_next = broken_stream_name == "stderr"
    phases: list[str] = []
    recorded: list[tuple[object, ...]] = []
    runs = [
        dict(workflowName=name, databaseId=number, headSha=SHA, headBranch=owner.DEFAULT_BRANCH,
             status="completed", conclusion="success", event="push",
             createdAt="2026-10-20T00:00:00Z", url="")
        for number, name in enumerate(owner.DEFAULT_REQUIRED_WORKFLOWS, start=1)
    ]

    def ledger(argv: Sequence[str] | None = None) -> int:
        phases.append("ledger")
        return 5

    def query(sha: str, *, repo: str) -> list[dict[str, object]]:
        phases.append("status")
        return runs

    def detail(run_id: int, repository: str) -> dict[str, object]:
        return dict(next(run for run in runs if run["databaseId"] == run_id), jobs=[])

    monkeypatch.setattr(ci_failure_ledger, "main", ledger)
    monkeypatch.setattr(ci_failure_ledger, "fetch_run", detail)
    monkeypatch.setattr(owner, "fetch_runs", query)
    monkeypatch.setattr(active_work_status, "collect_status", lambda: {
        "open_task_ids": [], "git": {"head": SHA},
    })
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(0.0, 80.0))
    monkeypatch.setattr(ci_check_cooldown, "cmd_record_verdict", lambda *args: recorded.append(args))
    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", output)
        scoped.setattr(sys, "stderr", errors)
        result = owner.main([
            "--observe-ledger", "--remote-only", "--sha", SHA,
            "--failure-ledger", str(tmp_path / "ledger.json"), "--snapshot-output", str(snapshot),
        ])

    assert result == 120
    assert phases == ["ledger", "status"]
    assert attempts[:2] == ["stdout", "stderr"]
    current = read_snapshot(snapshot, producer="pipeline-status", identity=identity)
    assert current["state"] == "lookup_error"
    assert current["evidence"]["errors"] == ["failure-ledger observation failed rc=120"]
    assert current["release_ready"] is False
    assert current["gate_evidence"] is False
    assert recorded == []
    assert "PIPELINE-TERMINAL-OBSERVED" not in output.getvalue()


@pytest.mark.parametrize("fault_kind", ["exit-message", "traceback"])
def test_report_write_failure_retains_native_child_code_and_runs_status(
    monkeypatch: pytest.MonkeyPatch, fault_kind: str,
) -> None:
    class ReportFailure(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = True

        def write(self, text: str) -> int:
            if self.fail_next:
                self.fail_next = False
                raise OSError("fixture report write unavailable")
            return super().write(text)

    native_source = (
        "import io, sys\n"
        "class ReportFailure(io.StringIO):\n"
        "    def __init__(self): super().__init__(); self.fail_next = True\n"
        "    def write(self, text):\n"
        "        if self.fail_next:\n"
        "            self.fail_next = False\n"
        "            raise OSError('fixture report write unavailable')\n"
        "        return super().write(text)\n"
        "sys.stderr = ReportFailure()\n"
        + ("raise SystemExit('fixture exit')\n" if fault_kind == "exit-message"
           else "raise RuntimeError('fixture observer')\n")
    )
    native = subprocess.run([sys.executable, "-W", "error", "-c", native_source],
                            text=True, capture_output=True, check=False, timeout=15)
    phases: list[str] = []
    observer_codes: list[int] = []

    def ledger(argv: Sequence[str] | None = None) -> NoReturn:
        phases.append("ledger")
        if fault_kind == "exit-message":
            raise SystemExit("fixture exit")
        raise RuntimeError("fixture observer")

    def status(args: argparse.Namespace) -> int:
        phases.append("status")
        observer_codes.append(args.ledger_observation_exit_code)
        return 0

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stderr", ReportFailure())
        scoped.setattr(ci_failure_ledger, "main", ledger)
        scoped.setattr(owner, "_status", status)
        result = owner.main(["--observe-ledger"])

    assert native.returncode == 1
    assert phases == ["ledger", "status"]
    assert observer_codes == [native.returncode]
    assert result == native.returncode


@pytest.mark.parametrize("closed_stream_name", ["stdout", "stderr"])
def test_closed_standard_stream_is_not_flushed(
    monkeypatch: pytest.MonkeyPatch, closed_stream_name: str,
) -> None:
    attempts: list[str] = []
    closed = _FlushFaultOutput(closed_stream_name, attempts)
    closed.close()
    with monkeypatch.context() as scoped:
        scoped.setattr(sys, closed_stream_name, closed)
        result = owner._cli_exit_code(lambda: 0)
    assert result == 0
    assert attempts == []


def test_cleanup_diagnostic_failure_still_attempts_both_streams_and_runs_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    reports: list[str] = []
    observer_codes: list[int] = []
    output = _FlushFaultOutput("stdout", attempts)
    errors = _FlushFaultOutput("stderr", attempts)
    output.fail_next = errors.fail_next = True

    def blocked_report(exc: BaseException, **kwargs: object) -> NoReturn:
        reports.append(str(exc))
        raise OSError("fixture cleanup diagnostic unavailable")

    def status(args: argparse.Namespace) -> int:
        observer_codes.append(args.ledger_observation_exit_code)
        return 7

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", output)
        scoped.setattr(sys, "stderr", errors)
        scoped.setattr(traceback, "print_exception", blocked_report)
        scoped.setattr(ci_failure_ledger, "main", lambda argv: 5)
        scoped.setattr(owner, "_status", status)
        result = owner.main(["--observe-ledger"])

    assert result == 120
    assert observer_codes == [120]
    assert attempts == ["stdout", "stderr", "stdout", "stderr"]
    assert reports == ["fixture stdout flush unavailable", "fixture stderr flush unavailable"]


@pytest.mark.parametrize("absent_stream_name", ["stdout", "stderr"])
@pytest.mark.parametrize("absence_kind", ["none", "deleted"])
def test_absent_standard_stream_does_not_turn_completion_into_an_error(
    monkeypatch: pytest.MonkeyPatch, absent_stream_name: str, absence_kind: str,
) -> None:
    with monkeypatch.context() as scoped:
        if absence_kind == "none":
            scoped.setattr(sys, absent_stream_name, None)
        else:
            scoped.delattr(sys, absent_stream_name)
        result = owner._cli_exit_code(lambda: 0)
    assert result == 0


def test_observer_and_legacy_exit_carrier_cannot_conflict(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object) -> NoReturn:
        raise AssertionError("conflicting CLI inputs cannot start observations")

    monkeypatch.setattr(ci_failure_ledger, "main", forbidden)
    monkeypatch.setattr(owner, "_status", forbidden)
    with pytest.raises(SystemExit) as caught:
        owner.main(["--observe-ledger", "--ledger-observation-exit-code", "0"])
    assert caught.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
