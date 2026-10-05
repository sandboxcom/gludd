"""E2E tests for branch coverage reporting and diagnostics.

Verifies that branch coverage reports include expected fields:
per-file branch %, JSON structure, e2e_branch_totals,
e2e_branch_coverage, and actionable missing_branches.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, AUDIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# JSON report structure — required fields
# ---------------------------------------------------------------------------


class TestBranchReportStructure:
    """The audit JSON report includes all branch-related fields."""

    def test_report_includes_e2e_branch_totals(self, tmp_path):
        module = _load_module("branch_rpt_struct_totals")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 9,
                                "num_branches": 4,
                                "covered_branches": 3,
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
        totals = report["e2e_branch_totals"]
        assert totals["scope"] == "tests/e2e"
        assert totals["total"] == 4
        assert totals["covered"] == 3
        assert totals["missing"] == 1
        assert "coverage_percent" in totals
        assert "total_branches" in totals
        assert "covered_branches" in totals

    def test_report_includes_e2e_branch_coverage(self, tmp_path):
        module = _load_module("branch_rpt_struct_e2e_cov")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 8,
                                "num_branches": 8,
                                "covered_branches": 6,
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
        assert report["e2e_branch_coverage"] == 75.0

    def test_report_includes_per_file_branch_percentages(self, tmp_path):
        module = _load_module("branch_rpt_struct_per_file")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/m1.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 4,
                            },
                        },
                        "src/general_ludd/m2.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 2,
                            },
                        },
                        "src/general_ludd/m3.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 4,
                                "covered_branches": 1,
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
        pf = report["per_file_results"]
        assert pf["general_ludd/m1.py"]["branch_coverage"] == 100.0
        assert pf["general_ludd/m2.py"]["branch_coverage"] == 50.0
        assert pf["general_ludd/m3.py"]["branch_coverage"] == 25.0

    def test_report_includes_line_and_branch_coverage(self, tmp_path):
        module = _load_module("branch_rpt_struct_dual")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 20,
                                "covered_lines": 18,
                                "num_branches": 10,
                                "covered_branches": 7,
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
        assert report["line_coverage"] == 90.0
        assert report["branch_coverage"] == 70.0
        assert report["e2e_branch_coverage"] == 70.0

    def test_report_includes_per_file_thresholds(self, tmp_path):
        module = _load_module("branch_rpt_struct_thresh")
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
            per_file_threshold=75,
        )
        assert report["per_file_thresholds"]["line"] == 75
        assert report["per_file_thresholds"]["branch"] == 75
        assert "branch_threshold" in report
        assert "per_file_threshold" in report


# ---------------------------------------------------------------------------
# Per-file branch percentage accuracy
# ---------------------------------------------------------------------------


class TestPerFileBranchPercentage:
    """Per-file branch percentages are computed accurately."""

    def test_exact_fraction_rounds_correctly(self, tmp_path):
        module = _load_module("branch_rpt_pct_round")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/x.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 3,
                                "covered_branches": 2,
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
        pf = report["per_file_results"]["general_ludd/x.py"]
        assert pf["branch_coverage"] == round(100.0 * 2 / 3, 1)

    def test_multiple_files_aggregate_weighted_by_branch_counts(self, tmp_path):
        module = _load_module("branch_rpt_pct_weighted")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/small.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 2,
                                "covered_branches": 2,
                            },
                        },
                        "src/general_ludd/large.py": {
                            "summary": {
                                "num_statements": 100,
                                "covered_lines": 100,
                                "num_branches": 98,
                                "covered_branches": 80,
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
        expected_agg = round(100.0 * (2 + 80) / (2 + 98), 1)
        assert report["branch_coverage"] == expected_agg
        assert report["e2e_branch_coverage"] == expected_agg

    def test_per_file_branch_in_per_file_dict(self, tmp_path):
        module = _load_module("branch_rpt_pct_dict")
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
                                "covered_branches": 2,
                            },
                        },
                        "src/general_ludd/b.py": {
                            "summary": {
                                "num_statements": 5,
                                "covered_lines": 5,
                                "num_branches": 4,
                                "covered_branches": 1,
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
        assert report["per_file_branch"]["general_ludd/a.py"] == 100.0
        assert report["per_file_branch"]["general_ludd/b.py"] == 25.0


# ---------------------------------------------------------------------------
# Missing branches — actionable with file + line numbers
# ---------------------------------------------------------------------------


class TestMissingBranchesActionable:
    """Missing branches are actionable: file path, line numbers, and traceability."""

    def test_missing_branches_include_line_ranges(self, tmp_path):
        module = _load_module("branch_rpt_arcs_line")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/foo.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 10,
                                "num_branches": 3,
                                "covered_branches": 1,
                            },
                            "missing_branches": [[5, 7], [12, 15]],
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
        arcs = report["missing_arcs"]["general_ludd/foo.py"]
        assert len(arcs) == 2
        assert [5, 7] in arcs
        assert [12, 15] in arcs

    def test_missing_branches_keyed_by_relative_path(self, tmp_path):
        module = _load_module("branch_rpt_arcs_rel")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/sub/mod.py": {
                            "summary": {
                                "num_statements": 5,
                                "covered_lines": 5,
                                "num_branches": 2,
                                "covered_branches": 1,
                            },
                            "missing_branches": [[99, 101]],
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
        assert "general_ludd/sub/mod.py" in report["missing_arcs"]

    def test_missing_branches_sorted(self, tmp_path):
        module = _load_module("branch_rpt_arcs_sorted")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/z.py": {
                            "summary": {
                                "num_statements": 5,
                                "covered_lines": 5,
                                "num_branches": 2,
                                "covered_branches": 1,
                            },
                            "missing_branches": [[2, 3]],
                        },
                        "src/general_ludd/a.py": {
                            "summary": {
                                "num_statements": 5,
                                "covered_lines": 5,
                                "num_branches": 2,
                                "covered_branches": 1,
                            },
                            "missing_branches": [[1, 2]],
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
        keys = list(report["missing_arcs"].keys())
        assert keys == sorted(keys)

    def test_missing_branches_context_included(self, tmp_path):
        module = _load_module("branch_rpt_arcs_ctx")
        json_path = tmp_path / "coverage.json"
        json_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/general_ludd/ctx.py": {
                            "summary": {
                                "num_statements": 5,
                                "covered_lines": 5,
                                "num_branches": 4,
                                "covered_branches": 2,
                            },
                            "missing_branches": [[10, 12]],
                            "contexts": {"5": ["test_a"], "10": ["test_b"]},
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
        assert "general_ludd/ctx.py" in report["contexts"]
        assert report["contexts"]["general_ludd/ctx.py"] == ["5", "10"]


# ---------------------------------------------------------------------------
# Full report generated_at and metadata
# ---------------------------------------------------------------------------


class TestBranchReportMetadata:
    """The audit report carries timestamps, thresholds, and source path."""

    def test_report_metadata_fields(self, tmp_path):
        module = _load_module("branch_rpt_meta")
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
            per_file_threshold=75,
        )
        assert "generated_at" in report
        assert report["threshold"] == 85
        assert report["source"] == "src/general_ludd"
        assert report["total_files"] == 1
        assert report["files_below_threshold"] == 0
        assert report["files_above_threshold"] == 1
        assert report["passed"] is True

    def test_report_files_under_threshold_list(self, tmp_path):
        module = _load_module("branch_rpt_meta_under")
        json_path = tmp_path / "coverage.json"
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
                        "src/general_ludd/bad.py": {
                            "summary": {
                                "num_statements": 10,
                                "covered_lines": 5,
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
        assert "general_ludd/bad.py" in under
        assert report["files_below_threshold"] == 1
        assert report["files_under_threshold"] == ["general_ludd/bad.py"]

    def test_report_shards_and_failed_shards_lists(self, tmp_path):
        module = _load_module("branch_rpt_meta_shards")
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
        assert "shards" in report
        assert "failed_shards" in report
        assert report["shards"] == []
        assert report["failed_shards"] == []
