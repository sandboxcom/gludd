"""Completion claims require item-scoped deployment acceptance, not implementation alone."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from general_ludd.validation.backlog_auditor import INCOMPLETE, VERIFIED_COMPLETE, BacklogAuditor


def _task(task_id: str = "CURRENT-1", status: str = "complete") -> dict[str, object]:
    return {
        "id": task_id,
        "status": status,
        "evidence_test_ids": ["tests/unit/test_feature.py::test_ok"],
        "touched_files": ["feature.py"],
    }


def _tests(nodes: list[str]) -> dict[str, bool]:
    return {node: True for node in nodes}


def _read(_path: str) -> str:
    return "def feature():\n    return 42\n"


@pytest.mark.parametrize("status", ("complete", "completed", "done"))
def test_implementation_and_passing_tests_do_not_prove_deployment(status: str, tmp_path: Path) -> None:
    auditor = BacklogAuditor(str(tmp_path), _tests, _read)
    task = _task(status=status)
    task["deployment_ready"] = True
    report = auditor.audit([task])
    assert report.verified_complete == 0
    assert report.verdicts[0].verdict == INCOMPLETE
    assert any("deployment" in reason.lower() for reason in report.verdicts[0].reasons)


def test_successful_item_deployment_acceptance_allows_completion(tmp_path: Path) -> None:
    calls: list[str] = []

    def verify(task: Mapping[str, object]) -> bool:
        calls.append(str(task["id"]))
        return True

    auditor = BacklogAuditor(str(tmp_path), _tests, _read, deployment_validator=verify)
    report = auditor.audit([_task()])
    assert calls == ["CURRENT-1"]
    assert report.verified_complete == 1
    assert report.verdicts[0].verdict == VERIFIED_COMPLETE


@pytest.mark.parametrize("result", (False, None, 0, 1, "success"))
def test_failed_missing_or_non_boolean_deployment_result_is_not_complete(
    result: object, tmp_path: Path,
) -> None:
    auditor = BacklogAuditor(
        str(tmp_path), _tests, _read, deployment_validator=lambda _task: result,
    )
    report = auditor.audit([_task()])
    assert report.verified_complete == 0
    assert report.verdicts[0].verdict == INCOMPLETE


def test_deployment_owner_error_is_visible_and_cannot_complete(tmp_path: Path) -> None:
    def verify(_task: Mapping[str, object]) -> bool:
        raise RuntimeError("deployment probe failed")

    auditor = BacklogAuditor(str(tmp_path), _tests, _read, deployment_validator=verify)
    report = auditor.audit([_task()])
    assert report.verified_complete == 0
    assert report.verdicts[0].verdict == INCOMPLETE
    assert any("deployment probe failed" in reason for reason in report.verdicts[0].reasons)


def test_unrelated_incomplete_item_does_not_hide_current_item_acceptance(tmp_path: Path) -> None:
    auditor = BacklogAuditor(
        str(tmp_path), _tests, _read,
        deployment_validator=lambda task: task["id"] == "READY-1",
    )
    report = auditor.audit([_task("READY-1"), _task("OTHER-1")])
    assert [(entry.id, entry.verdict) for entry in report.verdicts] == [
        ("READY-1", VERIFIED_COMPLETE), ("OTHER-1", INCOMPLETE),
    ]
    assert report.verified_complete == 1
    assert report.incomplete == 1


@pytest.mark.parametrize("criterion", ("serve a real request", "must export ", ""))
def test_unknown_or_empty_acceptance_criteria_cannot_be_assumed_met(
    criterion: str, tmp_path: Path,
) -> None:
    task = _task()
    task["acceptance_criteria"] = [criterion]
    auditor = BacklogAuditor(
        str(tmp_path), _tests, _read, deployment_validator=lambda _task: True,
    )
    report = auditor.audit([task])
    assert report.verified_complete == 0
    assert report.verdicts[0].verdict == INCOMPLETE
