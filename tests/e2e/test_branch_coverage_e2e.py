"""E2E tests for the branch coverage pipeline.

Tests the end-to-end flow: running coverage with --cov-branch,
parsing branch data, enforcing thresholds, identifying missing
branches, durable progress sidecars, and concurrent merging.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


def _load_module(name: str):
    """Load audit_coverage.py as an importable module."""
    spec = importlib.util.spec_from_file_location(name, AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Branch coverage data parsing — basic scenarios
# ---------------------------------------------------------------------------


class TestParseBranchCoverageBasic:
    """Parse coverage JSON and extract branch-specific metrics."""

    def test_branch_totals_counted_correctly(self, tmp_path):
        module = _load_module("branch_e2e_basic_totals")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/mod.py": {
                            "summary": {
                                "num_statements": 50,
                                "covered_lines": 45,
                                "num_branches": 10,
                                "covered_branches": 8,
                            },
                        },
                    },
                }
            )
        )
        report, _under, _passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert report["total_branches"] == 10
        assert report["covered_branches"] == 8
        assert report["branch_coverage"] == 80.0
        assert report["e2e_branch_coverage"] == 80.0
        assert report["e2e_branch_totals"]["total"] == 10
        assert report["e2e_branch_totals"]["covered"] == 8
        assert report["e2e_branch_totals"]["missing"] == 2
        assert report["e2e_branch_totals"]["coverage_percent"] == 80.0

    def test_branch_coverage_full(self, tmp_path):
        module = _load_module("branch_e2e_full")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/mod.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 6,
                                "covered_branches": 6,
                            },
                        },
                    },
                }
            )
        )
        _, _, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert passed

    def test_branch_zero_coverage(self, tmp_path):
        module = _load_module("branch_e2e_zero")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/mod.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 0,
                                "num_branches": 4,
                                "covered_branches": 0,
                            },
                        },
                    },
                }
            )
        )
        report, under, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert not passed
        assert report["branch_coverage"] == 0.0
        assert "general_ludd/mod.py" in under

    def test_missing_branches_key_used_when_available(self, tmp_path):
        module = _load_module("branch_e2e_fallback")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/mod.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 9,
                                "num_branches": 4,
                                "covered_branches": 3,
                            },
                            "missing_branches": [[5, 7]],
                        },
                    },
                }
            )
        )
        report, _, _ = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        assert report["missing_arcs"]["general_ludd/mod.py"] == [[5, 7]]

    def test_legacy_json_without_branch_fields_falls_back_to_line_pct(self, tmp_path):
        module = _load_module("branch_e2e_legacy")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/mod.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 8,
                            },
                        },
                    },
                }
            )
        )
        report, _, _passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert report["total_branches"] == 0
        assert report["covered_branches"] == 0
        assert report["per_file_results"]["general_ludd/mod.py"]["branch_coverage"] == 80.0

    def test_per_file_branch_percentage_in_results(self, tmp_path):
        module = _load_module("branch_e2e_per_file_pct")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 2,
                                "covered_branches": 2,
                            },
                        },
                        "src/general_ludd/b.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 10,
                                "covered_branches": 5,
                            },
                        },
                    },
                }
            )
        )
        report, _, _ = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert report["per_file_results"]["general_ludd/a.py"]["branch_coverage"] == 100.0
        assert report["per_file_results"]["general_ludd/b.py"]["branch_coverage"] == 50.0

    def test_per_file_branch_passed_field(self, tmp_path):
        module = _load_module("branch_e2e_per_file_passed")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/high.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 10,
                                "covered_branches": 10,
                            },
                        },
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
        report, under, _ = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert report["per_file_results"]["general_ludd/high.py"]["passed"] is True
        assert report["per_file_results"]["general_ludd/low.py"]["passed"] is False
        assert "general_ludd/low.py" in under


# ---------------------------------------------------------------------------
# Aggregate branch coverage threshold enforcement
# ---------------------------------------------------------------------------


class TestBranchAggregateThresholds:
    """Enforce the E2E_COVERAGE_AUDIT_CONTRACT aggregate ≥85% rule."""

    def test_aggregate_branch_above_85_passes(self, tmp_path):
        module = _load_module("branch_e2e_agg_above")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 100,
                                "covered_branches": 90,
                            },
                        },
                    },
                }
            )
        )
        _, _, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert passed

    def test_aggregate_branch_below_85_fails(self, tmp_path):
        module = _load_module("branch_e2e_agg_below")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 100,
                                "covered_branches": 80,
                            },
                        },
                    },
                }
            )
        )
        _, _, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert not passed

    def test_aggregate_branch_exactly_85_passes(self, tmp_path):
        module = _load_module("branch_e2e_agg_exact")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 100,
                                "covered_branches": 85,
                            },
                        },
                    },
                }
            )
        )
        _, _, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert passed

    def test_per_file_branch_at_75_passes(self, tmp_path):
        module = _load_module("branch_e2e_pf_at75")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 3,
                            },
                        },
                    },
                }
            )
        )
        report, _, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert passed
        assert report["per_file_results"]["general_ludd/x.py"]["passed"] is True

    def test_per_file_branch_below_75_fails(self, tmp_path):
        module = _load_module("branch_e2e_pf_below75")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
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
        report, under, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert not passed
        assert report["per_file_results"]["general_ludd/x.py"]["passed"] is False
        assert "general_ludd/x.py" in under


# ---------------------------------------------------------------------------
# Per-file branch threshold edge cases
# ---------------------------------------------------------------------------


class TestPerFileBranchEdges:
    """Edge cases for per-file threshold enforcement."""

    def test_zero_statements_with_branches_uses_branch_pct(self, tmp_path):
        module = _load_module("branch_e2e_zero_stmts")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 0,
                                "covered_lines": 0,
                                "num_branches": 4,
                                "covered_branches": 4,
                            },
                        },
                    },
                }
            )
        )
        report, _, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert passed
        assert report["per_file_results"]["general_ludd/x.py"]["branch_coverage"] == 100.0

    def test_zero_statements_zero_branches_skipped(self, tmp_path):
        module = _load_module("branch_e2e_zero_zero")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
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
        report, _, passed = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
            per_file_threshold=75,
        )
        assert passed
        assert report["total_files"] == 0


# ---------------------------------------------------------------------------
# Missing branches identification
# ---------------------------------------------------------------------------


class TestMissingBranchesIdentification:
    """Missing branches are correctly extracted and associated with files."""

    def test_single_missing_branch(self, tmp_path):
        module = _load_module("branch_e2e_miss_single")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/foo.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 2,
                                "covered_branches": 1,
                            },
                            "missing_branches": [[15, 17]],
                        },
                    },
                }
            )
        )
        report, _, _ = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        assert "general_ludd/foo.py" in report["missing_arcs"]
        assert report["missing_arcs"]["general_ludd/foo.py"] == [[15, 17]]

    def test_multiple_missing_branches(self, tmp_path):
        module = _load_module("branch_e2e_miss_multi")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/bar.py": {
                            "summary": {
                                "num_statements": 20,
                                "covered_lines": 20,
                                "num_branches": 6,
                                "covered_branches": 3,
                            },
                            "missing_branches": [[10, 12], [20, 22], [30, 34]],
                        },
                    },
                }
            )
        )
        report, _, _ = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        missing = report["missing_arcs"]["general_ludd/bar.py"]
        assert len(missing) == 3
        assert [10, 12] in missing
        assert [20, 22] in missing
        assert [30, 34] in missing

    def test_no_missing_branches_means_empty_dict(self, tmp_path):
        module = _load_module("branch_e2e_miss_none")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/full.py": {
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
        report, _, _ = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        assert report["missing_arcs"] == {}

    def test_missing_branches_with_multiple_sources(self, tmp_path):
        module = _load_module("branch_e2e_miss_multi_src")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 5,
                                "covered_lines": 5,
                                "num_branches": 2,
                                "covered_branches": 1,
                            },
                            "missing_branches": [[5, 7]],
                        },
                        "src/general_ludd/b.py": {
                            "summary": {
                                "num_statements": 5,
                                "covered_lines": 5,
                                "num_branches": 2,
                                "covered_branches": 1,
                            },
                            "missing_branches": [[12, 14]],
                        },
                    },
                }
            )
        )
        report, _, _ = module.parse_coverage_json(
            str(json_path),
            85,
            "src/general_ludd",
        )
        assert len(report["missing_arcs"]) == 2
        assert report["missing_arcs"]["general_ludd/a.py"] == [[5, 7]]
        assert report["missing_arcs"]["general_ludd/b.py"] == [[12, 14]]


# ---------------------------------------------------------------------------
# Durable progress sidecar for branch coverage
# ---------------------------------------------------------------------------


class TestBranchCoverageProgressSidecar:
    """Durable progress sidecar includes branch-tracking metadata."""

    def test_progress_snapshot_includes_counts(self, tmp_path):
        module = _load_module("branch_e2e_progress_snap")
        snapshot = module._progress_snapshot(
            run_id="test-run-1",
            started_at="2026-01-01T00:00:00Z",
            files=["a.py", "b.py"],
            states=[
                {"path": "a.py", "status": "passed"},
                {"path": "b.py", "status": "running"},
            ],
            current_index=1,
            status="running",
            complete=False,
            environment_namespace="test-ns",
        )
        assert snapshot["kind"] == "coverage_audit_progress"
        assert snapshot["run_id"] == "test-run-1"
        assert snapshot["complete"] is False
        assert snapshot["current_index"] == 1
        assert snapshot["total"] == 2
        assert snapshot["counts"]["passed"] == 1
        assert snapshot["counts"]["attempted"] == 2

    def test_progress_complete_true(self, tmp_path):
        module = _load_module("branch_e2e_progress_done")
        snapshot = module._progress_snapshot(
            run_id="done-run",
            started_at="2026-01-01T00:00:00Z",
            files=["a.py"],
            states=[{"path": "a.py", "status": "passed"}],
            current_index=1,
            status="completed",
            complete=True,
            environment_namespace="test-ns",
        )
        assert snapshot["complete"] is True
        assert snapshot["status"] == "completed"

    def test_progress_includes_error_when_provided(self, tmp_path):
        module = _load_module("branch_e2e_progress_error")
        snapshot = module._progress_snapshot(
            run_id="err-run",
            started_at="2026-01-01T00:00:00Z",
            files=["a.py"],
            states=[{"path": "a.py", "status": "failed"}],
            current_index=0,
            status="failed",
            complete=False,
            environment_namespace="test-ns",
            error="OOM killed shard",
        )
        assert snapshot["error"] == "OOM killed shard"

    def test_progress_atomic_writes_to_sidecar(self, tmp_path):
        module = _load_module("branch_e2e_progress_atomic")
        progress_path = tmp_path / "report.progress.json"
        module._publish_progress(
            path=progress_path,
            run_id="atomic-run",
            started_at="2026-01-01T00:00:00Z",
            files=["test.py"],
            states=[{"path": "test.py", "status": "running"}],
            current_index=0,
            status="running",
            complete=False,
            environment_namespace="test-ns",
        )
        assert progress_path.exists()
        data = json.loads(progress_path.read_text())
        assert data["run_id"] == "atomic-run"
        assert data["status"] == "running"


# ---------------------------------------------------------------------------
# Coverage environment and concurrent merging
# ---------------------------------------------------------------------------


class TestCoverageEnvironment:
    """Concurrent coverage uses isolated .coverage files per process."""

    def test_coverage_environment_sets_audit_flag(self, monkeypatch):
        module = _load_module("branch_e2e_env")
        env = module._coverage_environment()
        assert env["GLUDD_COVERAGE_AUDIT"] == "1"
        assert env["GLUDD_E2E_ACTIVE"] == "1"

    def test_coverage_environment_overrides_caller(self, monkeypatch):
        module = _load_module("branch_e2e_env_override")

        monkeypatch.setenv("GLUDD_E2E_ACTIVE", "0")
        env = module._coverage_environment()
        assert env["GLUDD_E2E_ACTIVE"] == "1"


class TestCoverageRunPytestCoverage:
    """run_pytest_coverage uses --cov-branch and configures merging."""

    def test_run_pytest_coverage_uses_cov_branch_flag(self, tmp_path, monkeypatch):
        module = _load_module("branch_e2e_run_flag")
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            import subprocess as _sp  # noqa: F401

            result = type("Result", (), {})()
            result.returncode = 0
            return result

        monkeypatch.setattr(module.subprocess, "run", fake_run)

        shards = []
        assert (
            module.run_pytest_coverage(
                "src/general_ludd",
                str(tmp_path / "coverage.json"),
                shards,
            )
            == 0
        )

        assert calls, "run_pytest_coverage must invoke subprocess.run"
        args = calls[0][0]
        assert "--cov-branch" in args
        assert "--cov-fail-under=0" in args
        assert "--cov-append" in args

    def test_run_pytest_coverage_sets_isolated_coverage_file(self, tmp_path, monkeypatch):
        module = _load_module("branch_e2e_run_iso")
        calls = []

        def fake_run(args, **kwargs):
            calls.append(kwargs)
            result = type("Result", (), {})()
            result.returncode = 0
            return result

        monkeypatch.setattr(module.subprocess, "run", fake_run)

        shards = []
        module.run_pytest_coverage(
            "src/general_ludd",
            str(tmp_path / "coverage.json"),
            shards,
        )
        assert calls, "Expected subprocess.run to be called"
        env = calls[0]["env"]
        assert "COVERAGE_FILE" in env
        assert ".coverage.audit." in env["COVERAGE_FILE"]

    def test_run_pytest_coverage_records_shard_on_failure(self, tmp_path, monkeypatch):
        module = _load_module("branch_e2e_run_fail")
        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            result = type("Result", (), {})()
            result.returncode = 3 if call_count[0] == 1 else 0
            return result

        monkeypatch.setattr(module.subprocess, "run", fake_run)

        shards = []
        rc = module.run_pytest_coverage(
            "src/general_ludd",
            str(tmp_path / "coverage.json"),
            shards,
        )
        assert rc == 3
        assert len(shards) == 1
        assert shards[0]["status"] == "failed"
        assert shards[0]["returncode"] == 3
        assert shards[0]["path"].startswith("tests/e2e/")
