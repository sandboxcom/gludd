"""Tests for the concise GitHub Actions job-summary formatter."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci_run_summary.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "ci_run_summary_under_test",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return cast(int, module.main())


def test_empty_run_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    assert _run(module, monkeypatch, {"jobs": []}) == 1
    assert "No jobs found" in capsys.readouterr().out


def test_failed_job_is_reported_and_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    result = _run(
        module,
        monkeypatch,
        {
            "jobs": [{
                "name": "unit",
                "status": "completed",
                "conclusion": "failure",
            }],
        },
    )
    output = capsys.readouterr().out
    assert result == 1
    assert "FAIL unit" in output
    assert "1 failed" in output


def test_success_and_running_jobs_are_grouped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    result = _run(
        module,
        monkeypatch,
        {
            "jobs": [
                {
                    "name": "package",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "integration",
                    "status": "in_progress",
                    "conclusion": None,
                },
            ],
        },
    )
    output = capsys.readouterr().out
    assert result == 1
    assert "package" in output
    assert "integration" in output
    assert "1 running" in output
    assert "1 passed" in output


def test_completed_stdin_run_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    result = _run(
        module,
        monkeypatch,
        {
            "jobs": [{
                "name": "package",
                "status": "completed",
                "conclusion": "success",
            }],
        },
    )

    assert result == 0
    assert "1 passed" in capsys.readouterr().out


def _completed_run_payload(run_id: int = 123) -> dict[str, object]:
    return {
        "databaseId": run_id,
        "headSha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "url": f"https://github.com/sandboxcom/gludd/actions/runs/{run_id}",
        "workflowName": "Build",
        "jobs": [{
            "name": "unit-3b",
            "status": "completed",
            "conclusion": "success",
        }],
    }


def test_immutable_run_fetch_uses_exact_numeric_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_completed_run_payload()),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123", "--repo", "sandboxcom/gludd"]) == 0
    assert calls == [[
        "gh",
        "run",
        "view",
        "123",
        "--repo",
        "sandboxcom/gludd",
        "--json",
        "databaseId,headSha,status,conclusion,url,workflowName,jobs",
    ]]
    assert "RUN 123" in capsys.readouterr().out


def test_immutable_run_fetch_preserves_api_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            4,
            stdout="",
            stderr="HTTP 503 from GitHub API\n",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 4
    captured = capsys.readouterr()
    assert "HTTP 503 from GitHub API" in captured.err
    assert "No jobs found" not in captured.out


def test_immutable_run_fetch_rejects_mismatched_response_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_completed_run_payload(run_id=124)),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 1
    assert "requested run 123 but received 124" in capsys.readouterr().err


def test_immutable_run_fetch_rejects_unbound_head_sha(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    payload = _completed_run_payload()
    payload["headSha"] = "abc123"

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 1
    assert "invalid immutable head SHA" in capsys.readouterr().err


def test_immutable_run_fetch_rejects_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(["--run", "123"]) == 1
    assert "invalid JSON" in capsys.readouterr().err


def test_validate_only_is_network_free_and_observable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()

    def unexpected_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("validate-only must not call GitHub")

    monkeypatch.setattr(module.subprocess, "run", unexpected_run)

    assert module.main([
        "--run",
        "123",
        "--repo",
        "sandboxcom/gludd",
        "--validate-only",
    ]) == 0
    assert (
        "CI-RUN-SUMMARY VALIDATED run=123 repo=sandboxcom/gludd"
        in capsys.readouterr().out
    )


_CANONICAL_SHARDS = (
    "unit-1a1", "unit-1a2", "unit-1b", "unit-1d", "unit-2", "unit-3a", "unit-3b", "other",
)


def _timing_payload() -> dict[str, object]:
    jobs: list[dict[str, object]] = [
        {
            "databaseId": 10, "name": "gate (3.11)", "status": "completed", "conclusion": "success",
            "startedAt": "2026-10-12T12:00:00Z", "completedAt": "2026-10-12T12:01:00Z",
        },
        {
            "databaseId": 11, "name": "gate (3.12)", "status": "completed", "conclusion": "success",
            "startedAt": "2026-10-12T12:01:00Z", "completedAt": "2026-10-12T12:02:00Z",
        },
    ]
    for index, shard in enumerate(_CANONICAL_SHARDS):
        jobs.append({
            "databaseId": 20 + index, "name": f"test-shard (3.11, {shard})",
            "status": "completed", "conclusion": "success",
            "startedAt": "2026-10-12T12:02:15Z", "completedAt": "2026-10-12T12:04:00Z",
            "steps": [{
                "number": 7, "name": f"Test (shard {shard}, Python 3.11)",
                "status": "completed", "conclusion": "success",
                "startedAt": "2026-10-12T12:03:00Z", "completedAt": "2026-10-12T12:04:00Z",
            }],
        })
    payload = _completed_run_payload()
    payload["jobs"] = jobs
    return payload


def _timing_report(output: str) -> dict[str, object]:
    lines = [line.removeprefix("SHARD_TIMINGS ") for line in output.splitlines()
             if line.startswith("SHARD_TIMINGS ")]
    assert len(lines) == 1
    return cast(dict[str, object], json.loads(lines[0]))


def _run_timing_payload(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> tuple[int, list[list[str]]]:
    calls: list[list[str]] = []

    def fetch(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 15
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(module.subprocess, "run", fetch)
    return cast(int, module.main(["--run", "123", "--shard-timings"])), calls


def test_shard_timings_bind_all_eight_rows_to_one_exact_run_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    result, calls = _run_timing_payload(_load_script(), monkeypatch, _timing_payload())
    captured = capsys.readouterr()
    report = _timing_report(captured.out)
    assert result == 0 and len(calls) == 1
    assert report["run_id"] == "123" and report["head_sha"] == "a" * 40
    assert report["authority"] == "diagnostic-only"
    assert report["release_ready"] is False and report["gate_evidence"] is False
    assert report["gate_completed_at"] == "2026-10-12T12:02:00Z"
    rows = cast(list[dict[str, object]], report["shards"])
    assert [row["shard"] for row in rows] == list(_CANONICAL_SHARDS)
    for index, row in enumerate(rows):
        assert row["state"] == "success"
        assert row["test_step_duration_censored"] is False
        assert row["source_job_id"] == 20 + index
        assert row["source_job_started_at"] == "2026-10-12T12:02:15Z"
        assert row["source_job_completed_at"] == "2026-10-12T12:04:00Z"
        assert row["source_test_started_at"] == "2026-10-12T12:03:00Z"
        assert row["source_test_completed_at"] == "2026-10-12T12:04:00Z"
        assert row["test_step_seconds"] == 60
        assert row["delay_from_gate_seconds"] == 15
    assert "10 passed" in captured.out
    assert "CI_RUN_SUMMARY_FETCH_START run=123 repo=sandboxcom/gludd" in captured.err


@pytest.mark.parametrize(
    ("status", "conclusion", "expected", "seconds"),
    [
        ("queued", None, "pending", None),
        ("in_progress", None, "censored", None),
        ("completed", "failure", "failed", 60),
        ("completed", "cancelled", "censored", 60),
        ("completed", "timed_out", "censored", 60),
        ("completed", "skipped", "censored", None),
    ],
)
def test_shard_timings_never_call_partial_or_failed_intervals_successful(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    conclusion: str | None,
    expected: str,
    seconds: int | None,
) -> None:
    payload = _timing_payload()
    job = cast(list[dict[str, object]], payload["jobs"])[2]
    job["status"], job["conclusion"] = status, conclusion
    step = cast(list[dict[str, object]], job["steps"])[0]
    step["status"], step["conclusion"] = status, conclusion
    result, _calls = _run_timing_payload(_load_script(), monkeypatch, payload)
    row = cast(list[dict[str, object]], _timing_report(capsys.readouterr().out)["shards"])[0]
    assert result == 1
    assert row["state"] == expected
    assert row["source_job_conclusion"] == conclusion
    assert row["source_test_conclusion"] == conclusion
    assert row["test_step_seconds"] == seconds
    assert row["test_step_duration_censored"] is True


def test_shard_timings_emit_missing_shards_and_ignore_other_python_versions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _timing_payload()
    jobs = cast(list[dict[str, object]], payload["jobs"])
    payload["jobs"] = [*jobs[:3], {
        "databaseId": 99, "name": "test-shard (3.12, unit-1a2)",
        "status": "completed", "conclusion": "success",
    }]
    payload["status"], payload["conclusion"] = "in_progress", None
    result, _calls = _run_timing_payload(_load_script(), monkeypatch, payload)
    rows = cast(list[dict[str, object]], _timing_report(capsys.readouterr().out)["shards"])
    assert result == 1
    assert len(rows) == 8 and rows[0]["state"] == "success"
    assert all(row["state"] == "pending" and row["source_job_id"] is None for row in rows[1:])


@pytest.mark.parametrize("broken", ["timestamp", "unfinished", "reversed", "missing-step", "duplicate-job", "gate"])
def test_shard_timings_mark_incomplete_or_ambiguous_metrics_unknown(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    broken: str,
) -> None:
    payload = _timing_payload()
    jobs = cast(list[dict[str, object]], payload["jobs"])
    job = jobs[2]
    step = cast(list[dict[str, object]], job["steps"])[0]
    if broken == "timestamp":
        step["startedAt"] = "not-a-timestamp"
    elif broken == "unfinished":
        step["startedAt"] = "0001-01-01T00:00:00Z"
    elif broken == "reversed":
        step["completedAt"] = "2026-10-12T12:02:59Z"
    elif broken == "missing-step":
        job["steps"] = []
    elif broken == "duplicate-job":
        jobs.append(dict(job, databaseId=999))
    else:
        jobs[0]["completedAt"], jobs[1]["completedAt"] = "", "not-a-timestamp"
    result, _calls = _run_timing_payload(_load_script(), monkeypatch, payload)
    report = _timing_report(capsys.readouterr().out)
    row = cast(list[dict[str, object]], report["shards"])[0]
    assert result == 0
    assert row["state"] == "unknown"
    if broken == "gate":
        assert report["gate_completed_at"] is None and row["delay_from_gate_seconds"] is None
    else:
        assert row["test_step_seconds"] is None


def test_shard_timings_validate_only_does_not_fetch_or_report_durations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no lookup"))
    assert module.main(["--run", "123", "--shard-timings", "--validate-only"]) == 0
    captured = capsys.readouterr()
    assert "CI-RUN-SUMMARY VALIDATED run=123" in captured.out
    assert "SHARD_TIMINGS " not in captured.out and "FETCH_START" not in captured.err


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("gh run view", 15), OSError("missing gh")])
def test_run_fetch_is_bounded_and_reports_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: subprocess.TimeoutExpired | OSError,
) -> None:
    module = _load_script()

    def failed_fetch(_command: list[str], **kwargs: object) -> None:
        assert kwargs["timeout"] == 15
        raise error

    monkeypatch.setattr(module.subprocess, "run", failed_fetch)
    assert module.main(["--run", "123"]) == 1
    captured = capsys.readouterr()
    assert "CI_RUN_SUMMARY_FETCH_START run=123" in captured.err
    assert "ci-run-summary: gh run view failed" in captured.err


def test_shard_timings_reject_unbound_stdin_payloads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"jobs": [{
        "name": "package", "status": "completed", "conclusion": "success",
    }]})))
    assert module.main(["--shard-timings"]) == 1
    assert "immutable run identity is required for shard timings" in capsys.readouterr().err


@pytest.mark.parametrize("change", ["before-job", "after-job", "unbound-job"])
def test_shard_timings_reject_inconsistent_source_intervals(
    change: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _timing_payload()
    job = cast(list[dict[str, object]], payload["jobs"])[2]
    step = cast(list[dict[str, object]], job["steps"])[0]
    if change == "before-job":
        step["startedAt"] = "2026-10-12T12:02:00Z"
    elif change == "after-job":
        step["completedAt"] = "2026-10-12T12:04:01Z"
    else:
        job["databaseId"] = None

    result, _calls = _run_timing_payload(_load_script(), monkeypatch, payload)
    row = cast(list[dict[str, object]], _timing_report(capsys.readouterr().out)["shards"])[0]
    assert result == 0  # Diagnostic invalidity cannot rewrite the legacy job result.
    assert row["state"] == "unknown"
    assert row["test_step_seconds"] is None
    assert row["test_step_duration_censored"] is True


@pytest.mark.parametrize(
    ("status", "conclusion", "state"),
    [
        ("queued", None, "pending"),
        ("in_progress", None, "censored"),
        ("completed", "failure", "failed"),
        ("completed", "cancelled", "censored"),
    ],
)
def test_shard_timings_do_not_measure_queue_delay_before_the_whole_gate_clears(
    status: str, conclusion: str | None, state: str,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _timing_payload()
    gate = cast(list[dict[str, object]], payload["jobs"])[1]
    gate["status"], gate["conclusion"] = status, conclusion
    result, _calls = _run_timing_payload(_load_script(), monkeypatch, payload)
    report = _timing_report(capsys.readouterr().out)

    assert result == 1
    assert report["gate_barrier_complete"] is False
    assert report["gate_barrier_state"] == state
    assert all(row["delay_from_gate_seconds"] is None
               for row in cast(list[dict[str, object]], report["shards"]))


def test_shard_timings_accept_bound_stdin_without_another_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_timing_payload())))
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no lookup"))

    assert module.main(["--shard-timings"]) == 0
    report = _timing_report(capsys.readouterr().out)
    assert report["run_id"] == "123" and report["head_sha"] == "a" * 40
    assert len(cast(list[dict[str, object]], report["shards"])) == 8


@pytest.mark.parametrize(
    "arguments", [["--validate-only"], ["--run", "0"], ["--run", "123", "--repo", "invalid"]],
)
def test_invalid_summary_inputs_fail_before_any_fetch(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no lookup"))
    assert module.main(arguments) == 2
    assert "CI_RUN_SUMMARY_FETCH_START" not in capsys.readouterr().err
