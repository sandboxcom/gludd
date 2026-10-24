"""E2E tests for branch coverage threshold enforcement.

Tests the behavior when aggregate or per-file branch coverage
falls below required thresholds. Validates error messages,
fail_under mechanism, and the exit codes from audit_coverage.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Literal

import pytest

from tests.unit.test_audit_coverage_source_scope import _RAW_FLOOR_CASES, _write_raw_floor_input

ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


def _load_module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Aggregate below threshold
# ---------------------------------------------------------------------------


class TestAggregateBelowThreshold:
    """Behavior when aggregate branch coverage is below the 85% requirement."""

    def test_aggregate_below_85_returns_all_ok_false(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_agg_false")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 100,
                                "covered_lines": 100,
                                "num_branches": 100,
                                "covered_branches": 70,
                            },
                        },
                    },
                }
            )
        )
        _report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        assert not all_ok

    def test_aggregate_above_85_returns_all_ok_true(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_agg_true")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 100,
                                "covered_lines": 100,
                                "num_branches": 100,
                                "covered_branches": 90,
                            },
                        },
                    },
                }
            )
        )
        _report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        assert all_ok

    def test_aggregate_below_but_per_file_above_still_fails(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_agg_below_pf_ok")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 10,
                                "covered_branches": 10,
                            },
                        },
                        "src/general_ludd/b.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 90,
                                "covered_branches": 30,
                            },
                        },
                    },
                }
            )
        )
        report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert not all_ok
        agg_branch = report["branch_coverage"]
        assert agg_branch < 85

    def test_empty_files_dont_affect_aggregate(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_agg_empty")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/real.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 4,
                            },
                        },
                        "src/general_ludd/empty.py": {
                            "summary": {
                                "num_statements": 0,
                                "covered_lines": 0,
                                "num_branches": 0,
                                "covered_branches": 0,
                            },
                        },
                    },
                }
            )
        )
        _report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        assert all_ok


# ---------------------------------------------------------------------------
# Per-file below threshold
# ---------------------------------------------------------------------------


class TestPerFileBelowThreshold:
    """Behavior when per-file branch coverage is below the 75% requirement."""

    def test_per_file_below_75_makes_all_ok_false(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_pf_false")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/low.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 2,
                            },
                        },
                    },
                }
            )
        )
        _report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert not all_ok

    def test_per_file_at_75_makes_all_ok_true(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_pf_at75_true")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/ok.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 3,
                            },
                        },
                        "src/general_ludd/_padding.py": {
                            "summary": {
                                "num_statements": 1,
                                "covered_lines": 1,
                                "num_branches": 100,
                                "covered_branches": 100,
                            },
                        },
                    },
                }
            )
        )
        _report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert all_ok

    def test_per_file_above_75_passes(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_pf_above")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/high.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 20,
                                "covered_branches": 20,
                            },
                        },
                    },
                }
            )
        )
        _report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert all_ok

    def test_one_file_below_fails_even_if_rest_pass(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_pf_mixed")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 4,
                            },
                        },
                        "src/general_ludd/b.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 4,
                            },
                        },
                        "src/general_ludd/c.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 2,
                            },
                        },
                    },
                }
            )
        )
        _report, under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert not all_ok
        assert "general_ludd/c.py" in under
        assert len(under) == 1

    def test_custom_per_file_threshold(self, tmp_path: Path) -> None:
        module = _load_module("branch_thr_pf_custom")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 10,
                                "covered_branches": 6,
                            },
                        },
                        "src/general_ludd/_padding.py": {
                            "summary": {
                                "num_statements": 1,
                                "covered_lines": 1,
                                "num_branches": 100,
                                "covered_branches": 100,
                            },
                        },
                    },
                }
            )
        )
        _report, _under, all_ok = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=60,
        )
        assert all_ok


# ---------------------------------------------------------------------------
# Error messages and exit codes
# ---------------------------------------------------------------------------


class TestThresholdErrorMessages:
    """Threshold violations produce clear, actionable error output."""

    def test_exit_one_with_files_below_threshold(self, tmp_path: Path) -> None:
        json_path = tmp_path / "coverage.json"
        json_out = tmp_path / "report.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/bad.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 2,
                                "num_branches": 4,
                                "covered_branches": 1,
                            },
                        },
                    },
                }
            )
        )
        import subprocess

        result = subprocess.run(
            [
                "python3",
                str(AUDIT_SCRIPT),
                "--json-file=" + str(json_path),
                "--threshold=85",
                "--json-out=" + str(json_out),
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 1
        assert "Files below per-file threshold" in result.stdout
        assert "bad.py" in result.stdout

    def test_exit_zero_when_all_above_threshold(self, tmp_path: Path) -> None:
        json_path = tmp_path / "coverage.json"
        json_out = tmp_path / "report.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/good.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 4,
                            },
                        },
                    },
                }
            )
        )
        import subprocess

        result = subprocess.run(
            [
                "python3",
                str(AUDIT_SCRIPT),
                "--json-file=" + str(json_path),
                "--threshold=85",
                "--json-out=" + str(json_out),
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0
        assert "All" in result.stdout
        assert "meet the" in result.stdout

    def test_exit_two_missing_coverage_json(self, tmp_path: Path) -> None:
        import subprocess

        missing = tmp_path / "does_not_exist.json"
        result = subprocess.run(
            ["python3", str(AUDIT_SCRIPT), "--json-file=" + str(missing), "--threshold=85"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 2

    def test_files_under_list_printed_sorted(self, tmp_path: Path) -> None:
        json_path = tmp_path / "coverage.json"
        json_out = tmp_path / "report.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/z.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 2,
                            },
                        },
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 2,
                            },
                        },
                    },
                }
            )
        )
        import subprocess

        result = subprocess.run(
            [
                "python3",
                str(AUDIT_SCRIPT),
                "--json-file=" + str(json_path),
                "--threshold=85",
                "--json-out=" + str(json_out),
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 1
        a_idx = result.stdout.index("a.py")
        z_idx = result.stdout.index("z.py")
        assert a_idx < z_idx, "Files should be printed sorted"


# ---------------------------------------------------------------------------
# fail_under mechanism (from pyproject.toml)
# ---------------------------------------------------------------------------


class TestFailUnderMechanism:
    """Validate the fail_under configuration and its integration."""

    def test_pyproject_has_fail_under_in_coverage_report(self) -> None:
        pyproject_path = ROOT / "pyproject.toml"
        content = pyproject_path.read_text()
        assert "fail_under = 85" in content, "pyproject.toml [tool.coverage.report] must set fail_under = 85"

    def test_pyproject_has_show_missing(self) -> None:
        pyproject_path = ROOT / "pyproject.toml"
        content = pyproject_path.read_text()
        assert "show_missing = true" in content, "pyproject.toml must enable show_missing for actionable reports"

    def test_run_pytest_coverage_uses_fail_under_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module("branch_thr_fail_under")
        calls: list[list[str]] = []

        def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(args)
            result = SimpleNamespace(returncode=0)
            return result

        monkeypatch.setattr(module.subprocess, "run", fake_run)
        shards: list[dict[str, object]] = []
        assert (
            module.run_pytest_coverage(
                "src/general_ludd",
                str(tmp_path / "coverage.json"),
                shards,
            )
            == 0
        )
        shard_args = calls[0]
        assert "--cov-fail-under=0" in shard_args, (
            "When collecting coverage per-shard, fail_under must be 0 so no single file blocks the aggregate audit."
        )

    def test_per_shard_fail_under_zero_allows_collection_with_low_coverage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_module("branch_thr_fail_under_collect")
        calls: list[list[str]] = []

        def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(args)
            result = SimpleNamespace(returncode=0)
            return result

        monkeypatch.setattr(module.subprocess, "run", fake_run)
        shards: list[dict[str, object]] = []
        module.run_pytest_coverage(
            "src/general_ludd",
            str(tmp_path / "coverage.json"),
            shards,
        )
        for args in calls:
            if "--cov-fail-under=0" in args:
                return
        raise AssertionError(
            "Every shard must use --cov-fail-under=0 to allow collection even when per-shard coverage is low"
        )

    def test_main_exits_one_when_files_below_threshold_and_pytest_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_module("branch_thr_main_below")
        json_out = tmp_path / "report.json"
        coverage_json = tmp_path / "sub" / "data.json"
        coverage_json.parent.mkdir()
        coverage_json.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/low.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 1,
                                "num_branches": 4,
                                "covered_branches": 0,
                            },
                        },
                    },
                }
            )
        )
        monkeypatch.setattr(
            module.sys,
            "argv",
            [
                "audit_coverage.py",
                "--json-file=" + str(coverage_json),
                "--json-out=" + str(json_out),
                "--threshold=85",
            ],
        )
        try:
            module.main()
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("below-threshold files must exit 1")


@pytest.mark.parametrize("raw_floor_cli_format", ["json", "xml"])
@pytest.mark.parametrize("raw_floor_cli_case", list(_RAW_FLOOR_CASES))
def test_native_import_cli_preserves_raw_floor_verdict_and_actual_metric(
    tmp_path: Path, raw_floor_cli_format: Literal["json", "xml"], raw_floor_cli_case: str,
) -> None:
    source, path, aggregate_min, per_file_min, should_pass = _write_raw_floor_input(
        tmp_path, raw_floor_cli_format, raw_floor_cli_case,
    )
    before = path.read_bytes()
    output = tmp_path / "owned-audit.json"
    result = subprocess.run(
        [
            sys.executable, str(AUDIT_SCRIPT),
            f"--{raw_floor_cli_format}-file={path}", f"--source={source}",
            f"--threshold={aggregate_min}", f"--per-file-threshold={per_file_min}",
            f"--json-out={output}",
        ],
        cwd=tmp_path, env=dict(os.environ), capture_output=True, text=True,
        check=False, timeout=15,
    )
    assert result.returncode == (0 if should_pass else 1), result.stdout + result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is should_pass and report["pytest_exit_code"] == 0
    assert path.read_bytes() == before
    assert "e2e_branch_coverage" not in report and "e2e_branch_totals" not in report
    assert report["shards"] == [] and report["failed_shards"] == []
    if should_pass:
        assert "All " in result.stdout and "meet" in result.stdout
    else:
        metric = "branch" if raw_floor_cli_case == "aggregate-branch" else "line"
        limit = per_file_min if raw_floor_cli_case == "file-line" else aggregate_min
        assert f"{metric}<{limit:.1f}%" in result.stdout
        assert "raw=" in result.stdout
        other = "line" if metric == "branch" else "branch"
        assert f"{other}<{limit:.1f}%" not in result.stdout
