"""Behavioral coverage for observable, secret-safe security audits."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import security_audit_observability as observer
from scripts import summarize_sast as sast_summarizer

ROOT = Path(__file__).resolve().parents[2]
OBSERVER = ROOT / "scripts" / "security_audit_observability.py"
SAST_SUMMARIZER = ROOT / "scripts" / "summarize_sast.py"
MAKEFILE = ROOT / "Makefile"
CONTRACT = ROOT / "config" / "make_target_contract.json"
DOC = ROOT / "docs" / "security" / "audit-observability.md"


def _bandit_result(
    *, severity: str, rule: str, filename: str, line: int, marker: str
) -> dict[str, object]:
    return {
        "issue_severity": severity,
        "test_id": rule,
        "filename": filename,
        "line_number": line,
        "issue_text": marker,
        "code": marker,
    }


def test_sensitive_phase_emits_heartbeat_but_never_child_output() -> None:
    marker = "credential-value-must-never-escape"
    child = (
        "import sys,time; "
        f"print({marker!r}); print({marker!r}, file=sys.stderr); "
        "time.sleep(0.12)"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(OBSERVER),
            "phase",
            "--name",
            "secrets-scan",
            "--heartbeat-seconds",
            "0.03",
            "--timeout-seconds",
            "2",
            "--sensitive",
            "--",
            sys.executable,
            "-c",
            child,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert marker not in completed.stdout
    assert marker not in completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert events[0]["status"] == "started"
    assert any(event["status"] == "running" for event in events)
    assert events[-1]["status"] == "passed"
    assert events[-1]["phase"] == "secrets-scan"
    assert events[-1]["elapsed_seconds"] >= 0.1


def test_phase_preserves_child_failure_and_reports_compact_timing() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(OBSERVER),
            "phase",
            "--name",
            "sast",
            "--heartbeat-seconds",
            "0.02",
            "--timeout-seconds",
            "2",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.exit(7)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 7
    final = json.loads(completed.stdout.splitlines()[-1])
    assert final == {
        "elapsed_seconds": final["elapsed_seconds"],
        "event": "security_audit_phase",
        "exit_code": 7,
        "phase": "sast",
        "schema_version": 1,
        "status": "failed",
    }


def test_phase_main_parses_command_separator() -> None:
    assert (
        observer.main(
            [
                "phase",
                "--name",
                "cli-phase",
                "--heartbeat-seconds",
                "1",
                "--timeout-seconds",
                "2",
                "--",
                sys.executable,
                "-c",
                "pass",
            ]
        )
        == 0
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": "bad phase"}, "invalid phase name"),
        ({"command": []}, "command must not be empty"),
        ({"heartbeat_seconds": 0}, "heartbeat_seconds must be positive"),
        ({"timeout_seconds": 0}, "timeout_seconds must be positive"),
    ],
)
def test_phase_rejects_unsafe_bounds(
    overrides: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "name": "safe-phase",
        "command": [sys.executable, "-c", "pass"],
        "heartbeat_seconds": 1.0,
        "timeout_seconds": 2.0,
        "sensitive": True,
        "stream": io.StringIO(),
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        observer.run_phase(**arguments)  # type: ignore[arg-type]


def test_missing_phase_executable_is_reported_without_exception() -> None:
    events = io.StringIO()

    result = observer.run_phase(
        name="missing-tool",
        command=["/definitely/not/a/real/executable"],
        heartbeat_seconds=1,
        timeout_seconds=2,
        sensitive=True,
        stream=events,
    )

    assert result.exit_code == 127
    assert json.loads(events.getvalue().splitlines()[-1])["status"] == "failed"


def test_phase_timeout_is_bounded_and_terminates_the_child() -> None:
    events = io.StringIO()

    result = observer.run_phase(
        name="bounded-phase",
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        heartbeat_seconds=0.02,
        timeout_seconds=0.06,
        sensitive=True,
        stream=events,
    )

    assert result.status == "timed_out"
    assert result.exit_code == 124
    assert any(
        json.loads(line)["status"] == "running" for line in events.getvalue().splitlines()
    )
    assert json.loads(events.getvalue().splitlines()[-1])["status"] == "timed_out"


def test_validate_only_audit_writes_all_phase_timings(tmp_path: Path) -> None:
    summary_path = tmp_path / "audit-summary.json"

    assert (
        observer.main(
            [
                "audit",
                "--heartbeat-seconds",
                "1",
                "--timeout-seconds",
                "2",
                "--summary",
                str(summary_path),
                "--sast-report",
                str(tmp_path / "report.json"),
                "--sast-summary",
                str(tmp_path / "sast-summary.json"),
                "--validate-only",
            ]
        )
        == 0
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "passed"
    assert [phase["name"] for phase in summary["phases"]] == [
        "secrets-scan",
        "sast",
        "pip-audit",
        "npm-audit",
        "security-backlog",
    ]
    assert all(phase["elapsed_seconds"] >= 0 for phase in summary["phases"])


def test_real_audit_plan_reuses_existing_scanner_targets(tmp_path: Path) -> None:
    args = argparse.Namespace(
        validate_only=False,
        make_command="make",
        sast_report=tmp_path / "report.json",
        sast_summary=tmp_path / "summary.json",
        sast_baseline=tmp_path / "baseline.json",
    )

    commands = observer._audit_commands(args)

    assert [phase for phase, _, _ in commands] == [
        "secrets-scan",
        "sast",
        "pip-audit",
        "npm-audit",
        "security-backlog",
    ]
    assert any("node-deps-audit" in command for _, command, _ in commands)
    assert commands[0][2] is True
    assert all(sensitive is False for _, _, sensitive in commands[1:])


def test_security_audit_rejects_non_boolean_validate_only(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            "make",
            "security-audit",
            "SECURITY_AUDIT_HEARTBEAT_SECS=5",
            "SECURITY_AUDIT_PHASE_TIMEOUT_SECS=60",
            "SECURITY_AUDIT_VALIDATE_ONLY=maybe",
            f"SECURITY_AUDIT_SUMMARY={tmp_path / 'audit.json'}",
            "SAST_REPORT=tests/fixtures/security/sast-report.json",
            f"SAST_SUMMARY={tmp_path / 'sast.json'}",
            "SAST_BASELINE=tests/fixtures/security/sast-baseline.json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "SECURITY_AUDIT_VALIDATE_ONLY must be 0 or 1" in completed.stderr


def test_sast_summary_groups_findings_and_computes_baseline_delta(
    tmp_path: Path,
) -> None:
    marker = "source snippet must not enter summary"
    baseline = {
        "errors": [],
        "results": [
            _bandit_result(
                severity="LOW",
                rule="B101",
                filename="src/a.py",
                line=1,
                marker=marker,
            )
        ],
    }
    current = {
        "errors": [],
        "results": [
            _bandit_result(
                severity="LOW",
                rule="B101",
                filename="src/a.py",
                line=3,
                marker=marker,
            ),
            _bandit_result(
                severity="HIGH",
                rule="B602",
                filename="src/b.py",
                line=9,
                marker=marker,
            ),
        ],
    }
    report_path = tmp_path / "report.json"
    baseline_path = tmp_path / "baseline.json"
    summary_path = tmp_path / "summary.json"
    report_path.write_text(json.dumps(current), encoding="utf-8")
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SAST_SUMMARIZER),
            "--report",
            str(report_path),
            "--baseline",
            str(baseline_path),
            "--output",
            str(summary_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["totals"] == {"baseline": 1, "current": 2, "delta": 1}
    assert summary["by_severity"] == {
        "HIGH": {"baseline": 0, "current": 1, "delta": 1},
        "LOW": {"baseline": 1, "current": 1, "delta": 0},
    }
    assert summary["by_rule"]["B602"] == {
        "baseline": 0,
        "current": 1,
        "delta": 1,
    }
    assert summary["by_file"]["src/b.py"] == {
        "baseline": 0,
        "current": 1,
        "delta": 1,
    }
    assert marker not in summary_path.read_text(encoding="utf-8")
    assert "SAST_SUMMARY current=2 baseline=1 delta=+1" in completed.stdout
    assert sast_summarizer.summarize(current, baseline)["totals"] == summary["totals"]


def test_sast_summary_accepts_a_prior_summary_as_baseline(tmp_path: Path) -> None:
    report = {"errors": [], "results": []}
    baseline = {
        "schema_version": 1,
        "totals": {"current": 2},
        "by_severity": {"MEDIUM": {"current": 2}},
        "by_rule": {"B104": {"current": 2}},
        "by_file": {"src/server.py": {"current": 2}},
    }
    report_path = tmp_path / "report.json"
    baseline_path = tmp_path / "baseline-summary.json"
    output_path = tmp_path / "summary.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SAST_SUMMARIZER),
            "--report",
            str(report_path),
            "--baseline",
            str(baseline_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    summary = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["totals"] == {"baseline": 2, "current": 0, "delta": -2}
    assert summary["by_rule"]["B104"]["delta"] == -2
    assert sast_summarizer.summarize(report, baseline)["by_rule"]["B104"][
        "delta"
    ] == -2


def test_sast_main_writes_summary_without_source_text(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    output_path = tmp_path / "summary.json"
    report_path.write_text(json.dumps({"errors": [], "results": []}), encoding="utf-8")

    assert (
        sast_summarizer.main(
            ["--report", str(report_path), "--output", str(output_path)]
        )
        == 0
    )
    assert json.loads(output_path.read_text(encoding="utf-8"))["totals"] == {
        "baseline": 0,
        "current": 0,
        "delta": 0,
    }


def test_sast_make_target_accepts_configurable_paths_with_spaces(tmp_path: Path) -> None:
    report_path = tmp_path / "current report.json"
    baseline_path = tmp_path / "prior summary.json"
    output_path = tmp_path / "new summary.json"
    report_path.write_text(json.dumps({"errors": [], "results": []}), encoding="utf-8")
    baseline_path.write_text(
        json.dumps(
            {
                "by_file": {},
                "by_rule": {},
                "by_severity": {},
                "totals": {"current": 0},
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "make",
            "sast-summary",
            f"SAST_REPORT={report_path}",
            f"SAST_SUMMARY={output_path}",
            f"SAST_BASELINE={baseline_path}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()


def test_sast_summary_only_retains_scanner_error_count() -> None:
    marker = "sensitive parser error detail"

    summary = sast_summarizer.summarize(
        {"errors": [{"detail": marker}], "results": []}, baseline=None
    )

    assert summary["scanner_error_count"] == 1
    assert marker not in json.dumps(summary)


def test_make_surface_contract_and_operator_research_are_documented() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "scripts/security_audit_observability.py audit" in makefile
    assert "scripts/summarize_sast.py" in makefile
    assert "bandit -q --ignore-nosec -r src/ -f json" in makefile
    assert "SECURITY_AUDIT_HEARTBEAT_SECS" in makefile
    assert "SECURITY_AUDIT_PHASE_TIMEOUT_SECS" in makefile
    assert "SAST_BASELINE" in makefile
    assert "sast-summary" in makefile

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in contract["targets"]}
    assert "security-audit" in entries
    assert "sast-summary" in entries
    assert "SECURITY_AUDIT_VALIDATE_ONLY" in entries["security-audit"][
        "make_variables"
    ]

    docs = DOC.read_text(encoding="utf-8")
    assert "github.com/PyCQA/bandit/issues/696" in docs
    assert "github.com/Yelp/detect-secrets" in docs
    assert "credential values" in docs
