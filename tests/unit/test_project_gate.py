"""Slice 3: run_project_gate aggregates a target project's checks into a verdict.

Separate entrypoint from gludd's own self-hosting preflight/gate. Uses only
ubiquitous coreutils (true/false) so it runs anywhere without a real toolchain.
"""

from __future__ import annotations

from pathlib import Path

from general_ludd.quality.project_gate import DEFAULT_CHECKS, run_project_gate


def _write_project_yml(root: Path, body: str) -> None:
    (root / "project.yml").write_text(body, encoding="utf-8")


def _passing_project(root: Path) -> None:
    _write_project_yml(
        root,
        'name: demo\n'
        'allowed_exec: ["true", "false"]\n'
        'commands:\n'
        '  lint: "true"\n'
        '  test: "true"\n',
    )


def test_gate_passes_when_all_required_checks_pass(tmp_path: Path) -> None:
    _passing_project(tmp_path)
    report = run_project_gate(tmp_path, checks=("lint", "test"))
    assert report["passed"] is True
    assert report["overall"] == "PASS"
    assert report["project"] == "demo"
    assert report["passed_count"] == 2
    assert report["failed_count"] == 0
    assert report["missing"] == []


def test_gate_fails_when_a_check_fails(tmp_path: Path) -> None:
    _write_project_yml(
        tmp_path,
        'name: demo\n'
        'allowed_exec: ["true", "false"]\n'
        'commands:\n'
        '  lint: "true"\n'
        '  test: "false"\n',
    )
    report = run_project_gate(tmp_path, checks=("lint", "test"))
    assert report["passed"] is False
    assert report["overall"] == "FAIL"
    assert report["passed_count"] == 1
    assert report["failed_count"] == 1
    # The failing check is the 'test' one.
    failing = [c for c in report["checks"] if not c["passed"]]
    assert [c["name"] for c in failing] == ["test"]
    assert failing[0]["exit_code"] != 0


def test_gate_fails_on_missing_required_check(tmp_path: Path) -> None:
    # Only 'lint' declared; 'test' is required but undeclared → fail-closed.
    _write_project_yml(
        tmp_path,
        'name: demo\n'
        'allowed_exec: ["true"]\n'
        'commands:\n'
        '  lint: "true"\n',
    )
    report = run_project_gate(tmp_path, checks=("lint", "test"))
    assert report["passed"] is False
    assert report["overall"] == "FAIL"
    assert report["missing"] == ["test"]
    missing_check = next(c for c in report["checks"] if c["name"] == "test")
    assert missing_check["declared"] is False
    assert missing_check["passed"] is False
    assert "declared" in missing_check["error"]


def test_report_structure(tmp_path: Path) -> None:
    _passing_project(tmp_path)
    report = run_project_gate(tmp_path, checks=("lint", "test"))
    # Top-level shape.
    for key in (
        "passed", "overall", "project", "workspace", "requested",
        "required", "checks", "missing", "passed_count", "failed_count",
    ):
        assert key in report, f"missing top-level key {key!r}"
    assert report["requested"] == ["lint", "test"]
    assert report["workspace"] == str(tmp_path)
    assert len(report["checks"]) == 2
    # Per-check shape.
    for c in report["checks"]:
        for key in (
            "name", "declared", "required", "passed", "exit_code",
            "duration_s", "timed_out", "error", "summary",
        ):
            assert key in c, f"missing per-check key {key!r}"
        assert c["required"] is True
        assert "PASS" in c["summary"]


def test_advisory_check_failure_does_not_fail_gate(tmp_path: Path) -> None:
    # 'test' required (passes), 'lint' run but NOT required (fails) → gate PASS.
    _write_project_yml(
        tmp_path,
        'name: demo\n'
        'allowed_exec: ["true", "false"]\n'
        'commands:\n'
        '  lint: "false"\n'
        '  test: "true"\n',
    )
    report = run_project_gate(tmp_path, checks=("lint", "test"), required=("test",))
    assert report["passed"] is True
    assert report["overall"] == "PASS"
    lint = next(c for c in report["checks"] if c["name"] == "lint")
    assert lint["required"] is False
    assert lint["passed"] is False


def test_missing_project_yml_fails_closed(tmp_path: Path) -> None:
    # No project.yml → config error surfaced as a FAIL verdict, not a raise.
    report = run_project_gate(tmp_path, checks=("test",))
    assert report["passed"] is False
    assert report["overall"] == "FAIL"
    assert "error" in report
    assert report["missing"] == ["test"]


def test_default_checks_constant() -> None:
    assert DEFAULT_CHECKS == ("lint", "test")
