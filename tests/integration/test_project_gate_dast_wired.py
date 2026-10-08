"""Production wiring and fail-closed contracts for the external-project DAST gate."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

import general_ludd.project_runner.dast as dast_module
from general_ludd.project_runner.dast import (
    DastResult,
    is_blocked_target,
    parse_zap_baseline,
    run_dast_scan,
)
from general_ludd.project_runner.profile import (
    DastConfig,
    ProjectProfile,
    load_project_profile,
)
from general_ludd.quality.project_gate import run_project_gate

_CLEAN_REPORT = '{"site": []}'
_HIGH_REPORT = """{
  "site": [{
    "alerts": [{
      "alert": "SQL Injection",
      "riskcode": "3",
      "instances": [{
        "uri": "http://127.0.0.1:8765/search",
        "method": "GET",
        "evidence": "query",
        "solution": "parameterize"
      }]
    }]
  }]
}"""


def _profile(*, dast: DastConfig | None = None) -> ProjectProfile:
    return ProjectProfile(
        name="wired-dast",
        commands={"lint": "true", "test": "true"},
        allowed_exec=["true", "zap-baseline.py"],
        dast=dast,
    )


def _scan(
    tmp_path: Path,
    *,
    returncode: int,
    report: str = _CLEAN_REPORT,
    fail_on: str = "HIGH",
) -> tuple[DastResult, MagicMock]:
    completed = MagicMock(returncode=returncode, stdout="", stderr="")
    config = DastConfig(
        target_url="http://127.0.0.1:8765",
        fail_on=fail_on,
    )
    profile = _profile(dast=config)
    with (
        patch(
            "general_ludd.project_runner.dast.shutil.which",
            return_value="/usr/local/bin/zap-baseline.py",
        ),
        patch(
            "general_ludd.project_runner.dast.subprocess.run",
            return_value=completed,
        ) as scanner,
        patch(
            "general_ludd.project_runner.dast.Path.read_text",
            return_value=report,
        ),
    ):
        return run_dast_scan(config, profile, tmp_path), scanner


def test_project_profile_dast_block_is_typed_and_default_off(tmp_path: Path) -> None:
    (tmp_path / "project.yml").write_text(
        "name: typed-dast\n"
        'allowed_exec: ["true", "zap-baseline.py"]\n'
        "commands:\n"
        '  lint: "true"\n'
        '  test: "true"\n'
        "dast:\n"
        "  target_url: http://127.0.0.1:8765\n"
        "  fail_on: medium\n",
        encoding="utf-8",
    )

    declared = load_project_profile(tmp_path)

    assert isinstance(declared.dast, DastConfig)
    assert declared.dast.fail_on == "MEDIUM"
    assert declared.dast.required is True
    assert _profile().dast is None


def test_zap_parser_ignores_malformed_nested_entries_and_duplicates() -> None:
    report = """{
      "site": [
        null,
        {"alerts": "invalid"},
        {"alerts": [
          null,
          {"instances": "invalid"},
          {"alert": "duplicate", "riskcode": "1", "instances": [
            null,
            {"uri": "/", "method": "GET"},
            {"uri": "/", "method": "GET"}
          ]}
        ]}
      ]
    }"""

    findings = parse_zap_baseline(report)

    assert len(findings) == 1
    assert parse_zap_baseline("[]") == []
    assert is_blocked_target("::1") is False


def test_declared_dast_runs_structured_driver_once_and_is_required(
    tmp_path: Path,
) -> None:
    config = DastConfig(target_url="http://127.0.0.1:8765")
    result = DastResult(passed=True, exit_code=2, warnings=True)

    with patch(
        "general_ludd.quality.project_gate.run_dast_scan",
        return_value=result,
    ) as scanner:
        report = cast(
            dict[str, Any],
            run_project_gate(tmp_path, profile=_profile(dast=config)),
        )

    scanner.assert_called_once()
    assert report["passed"] is True
    assert report["requested"] == ["lint", "test", "dast"]
    assert "dast" in report["required"]
    dast_report = report["checks"][-1]
    assert dast_report["name"] == "dast"
    assert dast_report["exit_code"] == 2
    assert dast_report["warnings"] is True


@pytest.mark.parametrize(
    ("result", "reason_fragment"),
    [
        (
            DastResult(
                passed=False,
                exit_code=0,
                reason="DAST findings met HIGH threshold",
            ),
            "findings",
        ),
        (
            DastResult(
                passed=False,
                exit_code=3,
                reason="DAST scanner failed with exit code 3",
            ),
            "scanner",
        ),
        (
            DastResult(
                passed=False,
                exit_code=0,
                reason="DAST report is malformed",
            ),
            "report",
        ),
    ],
)
def test_required_dast_failure_blocks_project_completion(
    tmp_path: Path,
    result: DastResult,
    reason_fragment: str,
) -> None:
    config = DastConfig(target_url="http://127.0.0.1:8765")
    with patch(
        "general_ludd.quality.project_gate.run_dast_scan",
        return_value=result,
    ):
        report = cast(
            dict[str, Any],
            run_project_gate(tmp_path, profile=_profile(dast=config)),
        )

    assert report["passed"] is False
    dast_report = report["checks"][-1]
    assert dast_report["passed"] is False
    assert reason_fragment in dast_report["summary"].lower()


def test_shadow_dast_failure_is_reported_without_blocking_completion(
    tmp_path: Path,
) -> None:
    config = DastConfig(
        target_url="http://127.0.0.1:8765",
        required=False,
    )
    with patch(
        "general_ludd.quality.project_gate.run_dast_scan",
        return_value=DastResult(passed=False, reason="shadow finding"),
    ):
        report = cast(
            dict[str, Any],
            run_project_gate(tmp_path, profile=_profile(dast=config)),
        )

    assert report["passed"] is True
    assert "dast" not in report["required"]
    assert report["checks"][-1]["passed"] is False


def test_gate_converts_unexpected_driver_error_to_required_failure(
    tmp_path: Path,
) -> None:
    config = DastConfig(target_url="http://127.0.0.1:8765")
    with patch(
        "general_ludd.quality.project_gate.run_dast_scan",
        side_effect=RuntimeError("scanner broke"),
    ):
        report = cast(
            dict[str, Any],
            run_project_gate(tmp_path, profile=_profile(dast=config)),
        )

    assert report["passed"] is False
    assert "runtimeerror" in report["checks"][-1]["summary"].lower()


@pytest.mark.parametrize("returncode", [1, 3, 17])
def test_scanner_error_exit_codes_fail_closed(
    tmp_path: Path,
    returncode: int,
) -> None:
    result, _scanner = _scan(tmp_path, returncode=returncode)

    assert result.passed is False
    assert result.exit_code == returncode
    assert "exit code" in (result.reason or "")


@pytest.mark.parametrize(
    "target_url",
    [
        "ftp://127.0.0.1/resource",
        "http://user:password@127.0.0.1:8765",
        "http:///missing-host",
    ],
)
def test_unsafe_target_url_fails_before_scanner_launch(
    tmp_path: Path,
    target_url: str,
) -> None:
    config = DastConfig(target_url=target_url)
    with patch("general_ludd.project_runner.dast.subprocess.run") as scanner:
        result = run_dast_scan(config, _profile(dast=config), tmp_path)

    scanner.assert_not_called()
    assert result.passed is False
    assert result.skipped is True
    assert "failed validation" in (result.reason or "")


def test_exit_two_is_warning_but_report_threshold_remains_authoritative(
    tmp_path: Path,
) -> None:
    clean, _scanner = _scan(tmp_path, returncode=2)
    finding, _scanner = _scan(tmp_path, returncode=2, report=_HIGH_REPORT)

    assert clean.passed is True
    assert clean.warnings is True
    assert clean.exit_code == 2
    assert finding.passed is False
    assert finding.warnings is True
    assert len(finding.findings) == 1


@pytest.mark.parametrize("report", ["", "not-json", "[]"])
def test_absent_or_malformed_report_fails_closed(
    tmp_path: Path,
    report: str,
) -> None:
    result, _scanner = _scan(tmp_path, returncode=0, report=report)

    assert result.passed is False
    assert "report" in (result.reason or "").lower()


def test_report_larger_than_eight_mib_fails_closed(tmp_path: Path) -> None:
    oversized = " " * ((8 * 1024 * 1024) + 1)
    result, _scanner = _scan(tmp_path, returncode=0, report=oversized)

    assert result.passed is False
    assert "8 mib" in (result.reason or "").lower()


def test_scanner_is_bounded_namespaced_and_proxy_free(tmp_path: Path) -> None:
    result, scanner = _scan(tmp_path, returncode=0)

    assert result.passed is True
    scanner.assert_called_once()
    kwargs = scanner.call_args.kwargs
    assert kwargs["timeout"] == 900
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["NO_PROXY"] == "*"
    assert kwargs["env"]["no_proxy"] == "*"
    assert "wired-dast" in kwargs["env"]["GLUDD_PROCESS_NAMESPACE"]
    assert not any(
        key.lower() in {"http_proxy", "https_proxy", "all_proxy"}
        for key in kwargs["env"]
    )
    argv = scanner.call_args.args[0]
    assert argv.count("-t") == 1
    assert argv[argv.index("-t") + 1] == "http://127.0.0.1:8765"
    assert argv.count("-J") == 1
    assert Path(argv[argv.index("-J") + 1]).name.startswith("zap-baseline-")


def test_scanner_timeout_fails_closed(tmp_path: Path) -> None:
    config = DastConfig(target_url="http://127.0.0.1:8765", max_duration_s=1)
    profile = _profile(dast=config)
    with (
        patch(
            "general_ludd.project_runner.dast.shutil.which",
            return_value="/usr/local/bin/zap-baseline.py",
        ),
        patch(
            "general_ludd.project_runner.dast.subprocess.run",
            side_effect=subprocess.TimeoutExpired("zap-baseline.py", 1),
        ),
    ):
        result = run_dast_scan(config, profile, tmp_path)

    assert result.passed is False
    assert "timed out" in (result.reason or "").lower()


def test_scanner_launch_error_fails_closed(tmp_path: Path) -> None:
    config = DastConfig(target_url="http://127.0.0.1:8765")
    profile = _profile(dast=config)
    with (
        patch(
            "general_ludd.project_runner.dast.shutil.which",
            return_value="/usr/local/bin/zap-baseline.py",
        ),
        patch(
            "general_ludd.project_runner.dast.subprocess.run",
            side_effect=OSError("exec failed"),
        ),
    ):
        result = run_dast_scan(config, profile, tmp_path)

    assert result.passed is False
    assert "launch failed" in (result.reason or "").lower()


def test_second_concurrent_scanner_fails_closed(tmp_path: Path) -> None:
    config = DastConfig(target_url="http://127.0.0.1:8765")
    assert dast_module._SCANNER_SLOT.acquire(blocking=False)
    try:
        with patch(
            "general_ludd.project_runner.dast.shutil.which",
            return_value="/usr/local/bin/zap-baseline.py",
        ):
            result = run_dast_scan(config, _profile(dast=config), tmp_path)
    finally:
        dast_module._SCANNER_SLOT.release()

    assert result.passed is False
    assert "single scanner slot" in (result.reason or "")


def test_removing_dast_block_is_independent_rollback(tmp_path: Path) -> None:
    with patch("general_ludd.quality.project_gate.run_dast_scan") as scanner:
        report = cast(dict[str, Any], run_project_gate(tmp_path, profile=_profile()))

    scanner.assert_not_called()
    assert report["passed"] is True
    assert report["requested"] == ["lint", "test"]
    assert all(check["name"] != "dast" for check in report["checks"])
