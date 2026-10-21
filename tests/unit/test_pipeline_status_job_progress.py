"""Bounded stdout job progress reuses exact validated snapshot observations."""

from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts import ci_check_cooldown, ci_failure_ledger, pipeline_status

if TYPE_CHECKING:
    import active_work_status
    import automatic_disk_cleanup
else:
    from scripts import active_work_status, automatic_disk_cleanup

SHA = "a" * 40
RUN_ID = 39587418003
STATES = (
    "queued", "pending", "requested", "waiting", "in_progress", "success", "failure",
    "cancelled", "timed_out", "action_required", "startup_failure", "stale", "neutral", "skipped", "unknown",
)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _rows(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [_mapping(item) for item in value]


def _run(workflow: str, run_id: int, *, status: str = "queued", conclusion: str = "") -> dict[str, object]:
    return {
        "workflowName": workflow, "databaseId": run_id, "headSha": SHA, "headBranch": "development",
        "event": "push", "status": status, "conclusion": conclusion, "url": "",
    }


def _step(name: object, status: object, conclusion: object, number: object = 1) -> dict[str, object]:
    return {"name": name, "status": status, "conclusion": conclusion, "number": number}


def _job(name: object, status: object = "in_progress", conclusion: object = "") -> dict[str, object]:
    return {"name": name, "status": status, "conclusion": conclusion, "steps": []}


def _sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobs: object,
) -> tuple[Path, list[str], list[tuple[str, object]], dict[str, object]]:
    runs = [
        _run("Build and Release", RUN_ID),
        _run("Molecule Tests", 101, status="completed", conclusion="success"),
    ]
    payload = {**runs[0], "jobs": jobs}
    calls: list[tuple[str, object]] = []
    output = tmp_path / "current.json"

    def fetch(sha: str, *, repo: str) -> list[dict[str, object]]:
        calls.append(("runs", (sha, repo)))
        return deepcopy(runs)

    def detail(run_id: int, repo: str) -> dict[str, object]:
        calls.append(("detail", (run_id, repo)))
        return deepcopy(payload if run_id == RUN_ID else {**runs[1], "jobs": []})

    def history() -> dict[str, object]:
        calls.append(("history", None))
        return {}

    monkeypatch.setattr(pipeline_status, "fetch_runs", fetch)
    monkeypatch.setattr(ci_failure_ledger, "fetch_run", detail)
    monkeypatch.setattr(active_work_status, "collect_status", lambda: {
        "git": {"head": SHA, "branch": "development"}, "open_task_ids": ["S98.4"],
    })
    monkeypatch.setattr(automatic_disk_cleanup, "inspect_usage",
                        lambda: automatic_disk_cleanup.DiskSnapshot(0.0, 80.0))
    monkeypatch.setattr(ci_check_cooldown, "load_history", history)
    monkeypatch.setattr(ci_check_cooldown, "cmd_record_verdict",
                        lambda *_args: pytest.fail("job progress must not authorize a workflow verdict"))
    args = ["--remote-only", "--sha", SHA, "--snapshot-output", str(output),
            "--failure-ledger", str(tmp_path / "missing-ledger.json")]
    return output, args, calls, payload


def _header(output: str) -> str:
    return next(line for line in output.splitlines() if line.startswith(f"PIPELINE-JOBS run={RUN_ID} "))


def _progress(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(f"PIPELINE-JOB-PROGRESS run={RUN_ID} ")]


def _assert_counts(header: str, *, total: int, nonzero: dict[str, int]) -> None:
    assert f" total={total} " in header
    assert sum(nonzero.values()) == total
    for state in STATES:
        assert f" {state}={nonzero.get(state, 0)} " in header + " "


def test_original_native_queued_workflow_displays_its_active_gate_and_actual_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Exact job/step fields from the retained 2f6ad0d observation at 10:57:04 UTC.
    gate = {
        "databaseId": 119179699824, "name": "gate (3.11)", "status": "in_progress", "conclusion": "",
        "steps": [
            {"name": "Test collection check", "number": 18, "status": "completed", "conclusion": "success",
             "startedAt": "2026-10-21T10:53:29Z", "completedAt": "2026-10-21T10:57:00Z"},
            {"name": "Smoke test", "number": 19, "status": "in_progress", "conclusion": "",
             "startedAt": "2026-10-21T10:57:00Z", "completedAt": "0001-01-01T00:00:00Z"},
        ],
    }
    path, args, calls, payload = _sources(tmp_path, monkeypatch, [gate])
    before = deepcopy(payload)
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    assert "status=queued" in output
    _assert_counts(_header(output), total=1, nonzero={"in_progress": 1})
    assert _progress(output) == [
        f'PIPELINE-JOB-PROGRESS run={RUN_ID} state=in_progress job="gate (3.11)" '
        'current="Smoke test" latest_success="Test collection check" active_steps=1',
    ]
    assert calls == [
        ("runs", (SHA, "sandboxcom/gludd")),
        ("detail", (RUN_ID, "sandboxcom/gludd")), ("detail", (101, "sandboxcom/gludd")),
    ]
    record = _mapping(json.loads(path.read_text(encoding="utf-8")))
    evidence = _mapping(record["evidence"])
    hosted = _mapping(evidence["hosted"])
    actual = _rows(hosted["runs"])[0]
    assert actual["jobs"] == before["jobs"] and payload == before
    assert set(actual) == {
        "workflow", "run_id", "status", "conclusion", "url", "jobs",
        "source_created_at", "source_updated_at", "source_attempt",
    }
    assert record["state"] == "pending" and record["release_ready"] is False and record["gate_evidence"] is False
    assert evidence["hosted_failures"] == []
    assert _mapping(_mapping(evidence["missing_proofs"])["S98.4"])["hosted_status"] == "pending"


def test_empty_validated_job_payload_has_complete_zero_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    path, args, calls, _payload = _sources(tmp_path, monkeypatch, [])
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    _assert_counts(_header(output), total=0, nonzero={})
    assert " progress_shown=0 progress_omitted=0" in _header(output)
    assert _progress(output) == []
    assert len(calls) == 3
    assert _mapping(json.loads(path.read_text()))["state"] == "pending"


def test_complete_counts_distinguish_all_states_without_relabeling_skips_or_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    jobs = [_job(state, state, None) for state in ("queued", "pending", "requested", "waiting", "in_progress")]
    jobs.extend(_job(state, "completed", state) for state in (
        "success", "failure", "cancelled", "timed_out", "action_required",
        "startup_failure", "stale", "neutral", "skipped",
    ))
    jobs.append(_job("future", "new-status", None))
    path, args, _calls, _payload = _sources(tmp_path, monkeypatch, jobs)
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    _assert_counts(_header(output), total=15, nonzero=dict.fromkeys(STATES, 1))
    assert " progress_shown=8 progress_omitted=0" in _header(output)
    rows = _progress(output)
    assert len(rows) == 8 and " state=in_progress " in rows[0]
    assert any(" state=cancelled " in row for row in rows)
    assert all(
        " state=success " not in row and " state=skipped " not in row and " state=neutral " not in row
        for row in rows
    )
    assert _mapping(json.loads(path.read_text()))["state"] == "failure"


@pytest.mark.parametrize("progress_payload_defect", (
    "foreign-sha", "foreign-branch", "foreign-workflow", "foreign-run", "foreign-event",
    "jobs-not-list", "job-not-object", "steps-not-list", "step-not-object",
))
def test_invalid_job_observation_is_refused_before_any_unvalidated_progress_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    progress_payload_defect: str,
) -> None:
    job = _job("UNVALIDATED")
    path, args, calls, payload = _sources(tmp_path, monkeypatch, [job])
    fields = {
        "foreign-sha": ("headSha", "b" * 40), "foreign-branch": ("headBranch", "foreign"),
        "foreign-workflow": ("workflowName", "foreign"), "foreign-run": ("databaseId", 99),
        "foreign-event": ("event", "workflow_dispatch"),
    }
    if progress_payload_defect in fields:
        key, value = fields[progress_payload_defect]
        payload[key] = value
    elif progress_payload_defect == "jobs-not-list":
        payload["jobs"] = {}
    elif progress_payload_defect == "job-not-object":
        payload["jobs"] = ["UNVALIDATED"]
    elif progress_payload_defect == "steps-not-list":
        job["steps"] = None
    else:
        job["steps"] = ["UNVALIDATED"]
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    assert "PIPELINE-JOBS " not in output and "PIPELINE-JOB-PROGRESS " not in output
    assert "UNVALIDATED" not in output
    record = _mapping(json.loads(path.read_text()))
    assert record["state"] == "lookup_error" and record["release_ready"] is False
    assert _mapping(record["evidence"])["errors"]
    assert len(calls) == 2


@pytest.mark.parametrize(("progress_status", "progress_conclusion"), (
    ("future-status", None), ("completed", "future-conclusion"), ("completed", None),
    (None, None), (["not-a-string"], ""), ("in_progress", "success"),
))
def test_novel_or_missing_state_fields_are_unknown_without_changing_payload_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    progress_status: object, progress_conclusion: object,
) -> None:
    job = _job("new metadata", progress_status, progress_conclusion)
    path, args, _calls, payload = _sources(tmp_path, monkeypatch, [job])
    before = deepcopy(payload)
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    _assert_counts(_header(output), total=1, nonzero={"unknown": 1})
    assert " state=unknown " in _progress(output)[0]
    record = _mapping(json.loads(path.read_text()))
    assert record["state"] == "pending" and _mapping(record["evidence"])["errors"] == []
    assert _rows(_mapping(_mapping(record["evidence"])["hosted"])["runs"])[0]["jobs"] == before["jobs"]
    assert payload == before


def test_large_control_character_names_are_inert_bounded_and_explicitly_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    hostile = "gate\n\r\x1b[31m\t\u202e\x07" + "\u2603" * 100000
    job = _job(hostile)
    job["steps"] = [_step(hostile, "completed", "success", 1), _step(hostile, "in_progress", "", 2)]
    path, args, _calls, payload = _sources(tmp_path, monkeypatch, [job])
    before = deepcopy(payload)
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    row = _progress(output)[0]
    assert len(row) <= 1500 and row.isascii()
    assert row.count("[truncated]") == 3
    assert all(ord(character) >= 32 for character in row)
    assert "\x1b" not in output and "\r" not in output and "\u202e" not in output and "\x07" not in output
    assert payload == before
    record = _mapping(json.loads(path.read_text()))
    assert _rows(_mapping(_mapping(record["evidence"])["hosted"])["runs"])[0]["jobs"] == before["jobs"]


def test_progress_limit_accounts_for_every_omitted_row_and_prioritizes_actual_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    jobs = [_job(f"failed-{index}", "completed", "failure") for index in range(12)]
    jobs.extend(_job(f"success-{index}", "completed", "success") for index in range(40))
    jobs.extend(_job(f"unknown-{index}", "future", None) for index in range(2))
    jobs.append(_job("ACTIVE-LAST"))
    _path, args, calls, _payload = _sources(tmp_path, monkeypatch, jobs)
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    header = _header(output)
    _assert_counts(header, total=55, nonzero={"failure": 12, "success": 40, "unknown": 2, "in_progress": 1})
    assert " progress_shown=8 progress_omitted=7" in header
    assert f"PIPELINE-JOBS-TRUNCATED run={RUN_ID} omitted=7 limit=8" in output
    rows = _progress(output)
    assert len(rows) == 8 and 'job="ACTIVE-LAST"' in rows[0]
    assert all(" state=failure " in row for row in rows[1:])
    assert len(calls) == 3


@pytest.mark.parametrize("step_metadata_case", ("missing", "empty", "multiple-active", "missing-name"))
def test_current_step_unknowns_are_visible_without_fabricating_a_current_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    step_metadata_case: str,
) -> None:
    job = _job("running")
    if step_metadata_case == "missing":
        del job["steps"]
    elif step_metadata_case == "multiple-active":
        job["steps"] = [_step("one", "in_progress", "", 1), _step("two", "in_progress", "", 2)]
    elif step_metadata_case == "missing-name":
        job["steps"] = [_step(None, "in_progress", "", 1)]
    _path, args, _calls, _payload = _sources(tmp_path, monkeypatch, [job])
    assert pipeline_status.main(args) == 2
    row = _progress(capsys.readouterr().out)[0]
    assert " current=unknown " in row
    assert (" latest_success=unknown " if step_metadata_case == "missing" else " latest_success=none ") in row
    active_count = 2 if step_metadata_case == "multiple-active" else 1 if step_metadata_case == "missing-name" else 0
    assert f" active_steps={active_count}" in row


@pytest.mark.parametrize("step_number_case", ("shuffled", "duplicate", "missing", "boolean"))
def test_latest_success_uses_unambiguous_native_step_numbers_not_array_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    step_number_case: str,
) -> None:
    first = _step("later success", "completed", "success", 5)
    second = _step("earlier success", "completed", "success", 2)
    if step_number_case == "duplicate":
        second["number"] = 5
    elif step_number_case == "missing":
        del second["number"]
    elif step_number_case == "boolean":
        second["number"] = True
    job = _job("running")
    job["steps"] = [first, second, _step("current", "in_progress", "", 6)]
    _path, args, _calls, _payload = _sources(tmp_path, monkeypatch, [job])
    assert pipeline_status.main(args) == 2
    row = _progress(capsys.readouterr().out)[0]
    expected = '"later success"' if step_number_case == "shuffled" else "unknown"
    assert f" latest_success={expected} " in row
    assert ' current="current" ' in row


def test_failed_job_progress_identifies_failure_and_retains_prior_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    job = _job("broken", "completed", "failure")
    job["steps"] = [_step("install", "completed", "success", 2), _step("build", "completed", "failure", 3)]
    _path, args, _calls, _payload = _sources(tmp_path, monkeypatch, [job])
    assert pipeline_status.main(args) == 2
    row = _progress(capsys.readouterr().out)[0]
    assert ' state=failure job="broken" current="build" latest_success="install" active_steps=0' in row


@pytest.mark.parametrize("no_progress_mode", ("no-workflows", "validate-only", "no-snapshot"))
def test_modes_without_validated_job_data_emit_no_progress_and_make_no_detail_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    no_progress_mode: str,
) -> None:
    path, args, calls, _payload = _sources(tmp_path, monkeypatch, [_job("not fetched")])
    expected_rc = 2
    if no_progress_mode == "no-workflows":
        def empty(sha: str, *, repo: str) -> list[dict[str, object]]:
            calls.append(("runs", (sha, repo)))
            return []
        monkeypatch.setattr(pipeline_status, "fetch_runs", empty)
    elif no_progress_mode == "validate-only":
        args.append("--validate-only")
        path.write_text('{"manual": true}\n', encoding="utf-8")
        expected_rc = 0
    else:
        index = args.index("--snapshot-output")
        del args[index:index + 2]
    assert pipeline_status.main(args) == expected_rc
    output = capsys.readouterr().out
    assert "PIPELINE-JOBS " not in output and "PIPELINE-JOB-PROGRESS " not in output
    assert calls == ([] if no_progress_mode == "validate-only" else [("runs", (SHA, "sandboxcom/gludd"))])
    if no_progress_mode == "validate-only":
        assert json.loads(path.read_text()) == {"manual": True}
    elif no_progress_mode == "no-snapshot":
        assert not path.exists()


def test_progress_preserves_separate_ledger_phase_and_exact_detail_query_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _path, args, calls, _payload = _sources(tmp_path, monkeypatch, [_job("actual activity")])

    def ledger(argv: Sequence[str]) -> int:
        calls.append(("ledger", tuple(argv)))
        return 0

    monkeypatch.setattr(ci_failure_ledger, "main", ledger)
    assert pipeline_status.main([*args, "--observe-ledger"]) == 2
    assert [kind for kind, _value in calls] == ["ledger", "runs", "detail", "detail"]
    assert calls[0] == ("ledger", (
        "observe-sha", "--repo", "sandboxcom/gludd", "--branch", "development",
        "--remote", "sandboxcom", "--ledger", str(tmp_path / "missing-ledger.json"), "--sha", SHA,
    ))
    assert len(_progress(capsys.readouterr().out)) == 1


def test_missing_job_metadata_is_explicit_unknown_and_remains_accepted_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    path, args, _calls, _payload = _sources(tmp_path, monkeypatch, [{}])
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    _assert_counts(_header(output), total=1, nonzero={"unknown": 1})
    assert _progress(output) == [
        f"PIPELINE-JOB-PROGRESS run={RUN_ID} state=unknown job=unknown "
        "current=unknown latest_success=unknown active_steps=0",
    ]
    record = _mapping(json.loads(path.read_text()))
    assert record["state"] == "pending" and _mapping(record["evidence"])["errors"] == []
    assert _rows(_mapping(_mapping(record["evidence"])["hosted"])["runs"])[0]["jobs"] == [{}]


@pytest.mark.parametrize("job_name_case", ("missing", "empty", "object"))
def test_missing_or_non_text_job_names_are_unknown_instead_of_active_terminal_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], job_name_case: str,
) -> None:
    job = _job("")
    if job_name_case == "missing":
        del job["name"]
    elif job_name_case == "object":
        job["name"] = {"untrusted": "metadata"}
    path, args, _calls, _payload = _sources(tmp_path, monkeypatch, [job])
    original = deepcopy(job)
    assert pipeline_status.main(args) == 2
    output = capsys.readouterr().out
    assert " state=in_progress job=unknown current=unknown " in _progress(output)[0]
    record = _mapping(json.loads(path.read_text()))
    assert _rows(_mapping(_mapping(record["evidence"])["hosted"])["runs"])[0]["jobs"] == [original]


def test_progress_preserves_full_native_job_step_order_and_unrelated_payload_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    active = _job("second in payload")
    active["steps"] = [
        _step("success-number-9", "completed", "success", 9),
        _step("success-number-2", "completed", "success", 2),
        _step("current-number-10", "in_progress", "", 10),
    ]
    active["native_extra"] = {"unknown": ["retained", 7]}
    jobs = [_job("first in payload", "completed", "success"), active, _job("last", "completed", "cancelled")]
    path, args, calls, payload = _sources(tmp_path, monkeypatch, jobs)
    original = deepcopy(payload)
    assert pipeline_status.main(args) == 2
    assert 'job="second in payload"' in _progress(capsys.readouterr().out)[0]
    record = _mapping(json.loads(path.read_text()))
    stored = _rows(_mapping(_mapping(record["evidence"])["hosted"])["runs"])[0]
    assert stored["jobs"] == original["jobs"] and payload == original
    assert len(calls) == 3
