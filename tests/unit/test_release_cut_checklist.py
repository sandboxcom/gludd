"""Contracts for the observable, fail-closed beta4 release checklist."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release_cut_checklist as checklist  # noqa: E402
from security_audit_observability import PhaseResult  # noqa: E402


def _result(name: str, status: str = "passed", exit_code: int = 0) -> PhaseResult:
    return PhaseResult(name, status, exit_code, 0.125)


def test_beta4_plan_reuses_canonical_release_and_state_guards() -> None:
    plan = checklist.beta4_check_plan("v0.1.0-beta.4")
    commands = [phase.command for phase in plan]

    assert commands == [
        ("make", "--no-print-directory", "release-worktree-guard"),
        ("make", "--no-print-directory", "check-gate-fresh"),
        ("make", "--no-print-directory", "lint"),
        ("make", "--no-print-directory", "typecheck"),
        ("make", "--no-print-directory", "collect-check"),
        (
            "make",
            "--no-print-directory",
            "check-readme-status",
            "TAG=v0.1.0-beta.4",
        ),
        (
            "make",
            "--no-print-directory",
            "release-dry-run",
            "TAG=v0.1.0-beta.4",
        ),
        (
            "make",
            "--no-print-directory",
            "check-tag-immutability",
            "TAG=v0.1.0-beta.4",
        ),
    ]
    assert all(0 < phase.heartbeat_seconds <= 30 for phase in plan)
    assert all(phase.timeout_seconds > phase.heartbeat_seconds for phase in plan)
    assert not any("require-ci-green" in command for command in commands)


def test_run_checklist_streams_every_phase_and_passes() -> None:
    calls: list[dict[str, object]] = []

    def run_phase(**kwargs: object) -> PhaseResult:
        calls.append(kwargs)
        return _result(str(kwargs["name"]))

    result = checklist.run_checklist("v0.1.0-beta.4", phase_runner=run_phase)

    assert result.all_passed
    assert len(result.checks) == len(checklist.beta4_check_plan(result.tag))
    assert [call["command"] for call in calls] == [
        list(phase.command) for phase in checklist.beta4_check_plan(result.tag)
    ]
    assert all(call["sensitive"] is False for call in calls)


def test_blocked_phase_is_reported_and_later_read_only_checks_still_run() -> None:
    calls: list[str] = []

    def run_phase(**kwargs: object) -> PhaseResult:
        name = str(kwargs["name"])
        calls.append(name)
        if name == "gate-fresh":
            return _result(name, "failed", 1)
        return _result(name)

    result = checklist.run_checklist("v0.1.0-beta.4", phase_runner=run_phase)

    assert not result.all_passed
    assert result.errors == []
    assert result.blockers == ["Fresh immutable gate: failed (exit 1)"]
    assert calls[-1] == "tag-immutability"


@pytest.mark.parametrize(
    ("status", "exit_code", "detail"),
    [
        ("timed_out", 124, "timed out"),
        ("failed", 127, "could not start"),
    ],
)
def test_unavailable_evidence_returns_collection_error(
    status: str,
    exit_code: int,
    detail: str,
) -> None:
    def run_phase(**kwargs: object) -> PhaseResult:
        return _result(str(kwargs["name"]), status, exit_code)

    result = checklist.run_checklist("v0.1.0-beta.4", phase_runner=run_phase)

    assert not result.all_passed
    assert result.errors
    assert detail in result.errors[0]
    assert checklist.exit_code(result) == checklist.EXIT_EVIDENCE_ERROR


def test_empty_check_plan_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checklist, "beta4_check_plan", lambda _tag: [])

    result = checklist.run_checklist("v0.1.0-beta.4", phase_runner=lambda **_: _result("x"))

    assert not result.all_passed
    assert result.errors == ["beta4 checklist contains no checks"]
    assert checklist.exit_code(result) == checklist.EXIT_EVIDENCE_ERROR


def test_runner_exception_is_evidence_error_and_does_not_claim_ready() -> None:
    def run_phase(**_kwargs: object) -> PhaseResult:
        raise OSError("runner unavailable")

    result = checklist.run_checklist("v0.1.0-beta.4", phase_runner=run_phase)
    report = checklist.print_report(result)

    assert checklist.exit_code(result) == checklist.EXIT_EVIDENCE_ERROR
    assert "READY FOR RELEASE-CUT" not in report
    assert "evidence collection failed" in report


def test_main_returns_blocked_and_emits_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocked = checklist.Checklist(
        tag="v0.1.0-beta.4",
        checks=[checklist.Check("Fresh immutable gate", False, "failed (exit 1)")],
    )
    monkeypatch.setattr(checklist, "run_checklist", lambda _tag: blocked)

    assert checklist.main(["v0.1.0-beta.4", "--human"]) == checklist.EXIT_BLOCKED
    output = capsys.readouterr().out
    assert '"event": "release_checklist_complete"' in output
    assert '"status": "blocked"' in output
    assert "BLOCKERS — fix these before release-cut" in output


def test_main_cancellation_is_explicit_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def cancel(_tag: str) -> checklist.Checklist:
        raise KeyboardInterrupt

    monkeypatch.setattr(checklist, "run_checklist", cancel)

    assert checklist.main(["v0.1.0-beta.4"]) == checklist.EXIT_CANCELLED
    assert "RELEASE-CHECKLIST-CANCELLED" in capsys.readouterr().err


def test_report_keeps_bounded_phase_details() -> None:
    result = checklist.Checklist(
        tag="v0.1.0-beta.4",
        checks=[checklist.Check("Release dry run", True, "passed in 0.125s")],
    )

    assert "v0.1.0-beta.4" in checklist.print_report(result)
    assert "[PASS] Release dry run: passed in 0.125s" in checklist.print_report(result)


def test_script_has_no_private_git_make_or_gate_status_parser() -> None:
    source = (ROOT / "scripts" / "release_cut_checklist.py").read_text(encoding="utf-8")

    assert "capture_output=True" not in source
    assert 'ROOT / ".gate-status"' not in source
    assert '["git", "status"' not in source
    assert "recipe_lines" not in source
    assert "security_audit_observability" in source
    assert "release-dry-run" in source


def test_beta4_research_zdd_and_rollback_are_documented() -> None:
    doc = (ROOT / "docs" / "features" / "BETA4_RELEASE_CHECKLIST.md").read_text(
        encoding="utf-8"
    )

    assert "2026-08-29" in doc
    assert "github.com/orgs/community/discussions/" in doc
    assert "Zero-downtime" in doc
    assert "Rollback" in doc
    assert "Resources" in doc
