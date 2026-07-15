"""Unit tests for verify_coverage role script — enhanced coverage gap analysis.

Tests the enhanced verify_coverage pipeline that:
  * parses coverage XML and JSON reports,
  * identifies uncovered code paths by cross-referencing module symbols,
  * cross-references generated scenario coverage_targets with measured coverage,
  * produces a structured gap report.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = (
    ROOT
    / "collections/ansible_collections/general_ludd/e2e_test_gen"
    / "roles/verify_coverage/files/verify_coverage.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_coverage", str(SCRIPT_PATH))
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load_module()


# ── Synthetic fixtures ─────────────────────────────────────────────────────

COV_JSON_FULL = {
    "totals": {"percent_covered": 100.0, "covered_lines": 5, "missing_lines": 0},
    "files": {
        "src/sample.py": {
            "executed_lines": [1, 2, 3, 4, 5],
            "missing_lines": [],
            "summary": {"percent_covered": 100.0, "covered_lines": 5, "missing_lines": 0},
        }
    },
}

COV_JSON_PARTIAL = {
    "totals": {"percent_covered": 50.0, "covered_lines": 3, "missing_lines": 3},
    "files": {
        "src/sample.py": {
            "executed_lines": [1, 2, 3],
            "missing_lines": [4, 5, 6],
            "summary": {"percent_covered": 50.0, "covered_lines": 3, "missing_lines": 3},
        }
    },
}

COV_XML_PARTIAL = """<?xml version="1.0" ?>
<coverage version="7.0" timestamp="0">
  <packages>
    <package name=".">
      <classes>
        <class filename="src/sample.py" name="sample">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="1"/>
            <line number="3" hits="1"/>
            <line number="4" hits="0"/>
            <line number="5" hits="0"/>
            <line number="6" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""

SYMBOLS = {
    "name": "sample",
    "functions": [
        {"name": "covered_fn", "line_start": 1, "line_end": 3, "is_public": True, "calls": []},
        {"name": "uncovered_fn", "line_start": 4, "line_end": 6, "is_public": True, "calls": []},
    ],
    "classes": [
        {
            "name": "Worker",
            "line_start": 8,
            "line_end": 20,
            "is_public": True,
            "methods": [
                {"name": "start", "line_start": 9, "line_end": 11, "is_public": True, "calls": []},
                {"name": "stop", "line_start": 12, "line_end": 14, "is_public": True, "calls": []},
            ],
        }
    ],
    "testable_paths": [
        {"target": "covered_fn", "type": "function", "line_range": [1, 3]},
        {"target": "uncovered_fn", "type": "function", "line_range": [4, 6]},
        {"target": "Worker.start", "type": "method", "line_range": [9, 11]},
        {"target": "Worker.stop", "type": "method", "line_range": [12, 14]},
    ],
}

SCENARIOS = {
    "scenarios": [
        {"name": "s1", "steps": [], "coverage_targets": ["covered_fn"]},
        {"name": "s2", "steps": [], "coverage_targets": ["uncovered_fn"]},
    ],
    "coverage_targets": ["covered_fn", "uncovered_fn", "nonexistent_target"],
}


# ── JSON parser ────────────────────────────────────────────────────────────

class TestParseCoverageJson:
    def test_returns_default_when_missing(self, mod, tmp_path: Path):
        result = mod._parse_coverage_json(tmp_path / "does_not_exist.json")
        assert result["totals"]["percent_covered"] == 0.0
        assert result["files"] == {}

    def test_reads_existing_file(self, mod, tmp_path: Path):
        p = tmp_path / "cov.json"
        p.write_text(json.dumps(COV_JSON_PARTIAL))
        result = mod._parse_coverage_json(p)
        assert result["totals"]["percent_covered"] == 50.0
        assert "src/sample.py" in result["files"]


# ── XML parser ─────────────────────────────────────────────────────────────

class TestParseCoverageXml:
    def test_parses_executed_and_missing(self, mod, tmp_path: Path):
        p = tmp_path / "cov.xml"
        p.write_text(COV_XML_PARTIAL)
        result = mod._parse_coverage_xml(p)
        assert "src/sample.py" in result["files"]
        info = result["files"]["src/sample.py"]
        assert set(info["executed_lines"]) == {1, 2, 3}
        assert set(info["missing_lines"]) == {4, 5, 6}

    def test_returns_empty_when_missing(self, mod, tmp_path: Path):
        result = mod._parse_coverage_xml(tmp_path / "no.xml")
        assert result["files"] == {}
        assert result["totals"]["percent_covered"] == 0.0

    def test_malformed_xml_returns_empty(self, mod, tmp_path: Path):
        p = tmp_path / "bad.xml"
        p.write_text("not <xml")
        result = mod._parse_coverage_xml(p)
        assert result["files"] == {}


# ── Symbol classification ──────────────────────────────────────────────────

class TestIdentifyUncoveredSymbols:
    def test_fully_uncovered_function(self, mod):
        cov = {"files": {"src/sample.py": {"missing_lines": [1, 2, 3], "executed_lines": []}}}
        symbols = {"functions": [{"name": "fn_a", "line_start": 1, "line_end": 3}], "classes": []}
        result = mod._identify_uncovered_symbols(symbols, cov, "src/sample.py")
        assert "fn_a" in result
        assert result["fn_a"]["coverage_state"] == "missing"

    def test_fully_covered_function(self, mod):
        cov = {"files": {"src/sample.py": {"missing_lines": [], "executed_lines": [1, 2, 3]}}}
        symbols = {"functions": [{"name": "fn_a", "line_start": 1, "line_end": 3}], "classes": []}
        result = mod._identify_uncovered_symbols(symbols, cov, "src/sample.py")
        assert "fn_a" not in result

    def test_partially_covered_function(self, mod):
        cov = {"files": {"src/sample.py": {"missing_lines": [3], "executed_lines": [1, 2]}}}
        symbols = {"functions": [{"name": "fn_a", "line_start": 1, "line_end": 4}], "classes": []}
        result = mod._identify_uncovered_symbols(symbols, cov, "src/sample.py")
        assert "fn_a" in result
        assert result["fn_a"]["coverage_state"] == "partial"
        assert result["fn_a"]["missing_lines"] == [3]

    def test_methods_under_classes(self, mod):
        cov = {"files": {"src/sample.py": {"missing_lines": [12, 13, 14], "executed_lines": [9, 10, 11]}}}
        symbols = {
            "functions": [],
            "classes": [
                {
                    "name": "Worker",
                    "methods": [
                        {"name": "start", "line_start": 9, "line_end": 11},
                        {"name": "stop", "line_start": 12, "line_end": 14},
                    ],
                }
            ],
        }
        result = mod._identify_uncovered_symbols(symbols, cov, "src/sample.py")
        assert "Worker.stop" in result
        assert result["Worker.stop"]["coverage_state"] == "missing"
        assert "Worker.start" not in result

    def test_no_file_in_coverage(self, mod):
        cov = {"files": {}}
        symbols = {"functions": [{"name": "fn_a", "line_start": 1, "line_end": 3}], "classes": []}
        result = mod._identify_uncovered_symbols(symbols, cov, "src/sample.py")
        # No coverage info at all → symbol treated as missing.
        assert "fn_a" in result
        assert result["fn_a"]["coverage_state"] == "missing"


# ── Coverage-target cross-reference ────────────────────────────────────────

class TestCrossReferenceTargets:
    def test_classifies_into_covered_uncovered_unresolved(self, mod):
        uncovered = {
            "uncovered_fn": {"coverage_state": "missing"},
            "partial_fn": {"coverage_state": "partial"},
        }
        known = {"covered_fn", "uncovered_fn", "partial_fn"}
        result = mod._cross_reference_targets(
            ["covered_fn", "uncovered_fn", "partial_fn", "nonexistent"],
            uncovered,
            known_symbols=known,
        )
        assert result["covered"] == ["covered_fn"]
        assert sorted(result["uncovered"]) == ["partial_fn", "uncovered_fn"]
        assert result["unresolved"] == ["nonexistent"]

    def test_empty_targets(self, mod):
        result = mod._cross_reference_targets([], {})
        assert result == {"covered": [], "uncovered": [], "unresolved": []}


# ── Gap report builder ─────────────────────────────────────────────────────

class TestBuildGapReport:
    def test_below_threshold(self, mod):
        uncovered = {
            "uncovered_fn": {"coverage_state": "missing", "line_range": [4, 6]},
            "partial_fn": {"coverage_state": "partial", "line_range": [9, 11]},
        }
        cross = {
            "covered": ["covered_fn"],
            "uncovered": ["uncovered_fn", "partial_fn"],
            "unresolved": ["mystery_target"],
        }
        report = mod._build_gap_report(
            coverage_pct=40.0,
            threshold=85,
            uncovered_symbols=uncovered,
            cross_reference=cross,
        )
        assert report["overall_verdict"] == "below_threshold"
        assert report["coverage_gap_pp"] == 45.0
        assert "uncovered_fn" in report["missing_symbols"]
        assert "partial_fn" in report["partial_symbols"]
        assert report["unresolved_targets"] == ["mystery_target"]

    def test_meets_threshold(self, mod):
        report = mod._build_gap_report(
            coverage_pct=90.0,
            threshold=85,
            uncovered_symbols={},
            cross_reference={"covered": ["a"], "uncovered": [], "unresolved": []},
        )
        assert report["overall_verdict"] == "meets_threshold"
        assert report["coverage_gap_pp"] == 0.0
        assert report["missing_symbols"] == []

    def test_suggested_scenarios_for_missing(self, mod):
        uncovered = {
            "uncovered_fn": {"coverage_state": "missing", "line_range": [4, 6]},
        }
        report = mod._build_gap_report(
            coverage_pct=50.0,
            threshold=85,
            uncovered_symbols=uncovered,
            cross_reference={"covered": [], "uncovered": ["uncovered_fn"], "unresolved": []},
        )
        assert len(report["suggested_scenarios"]) >= 1
        suggestion = report["suggested_scenarios"][0]
        assert "target" in suggestion
        assert suggestion["target"] == "uncovered_fn"
        assert "rationale" in suggestion


# ── CLI ────────────────────────────────────────────────────────────────────

class TestCLI:
    def test_help_lists_new_flags(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--scenarios-file" in result.stdout
        assert "--symbols-file" in result.stdout

    def test_end_to_end_produces_gap_report(self, tmp_path: Path):
        """Real pytest-cov run against a tiny module — verify the gap_report
        section is populated in the emitted coverage_report.json."""
        src = tmp_path / "sample_src.py"
        src.write_text(
            textwrap.dedent(
                """
                def covered():
                    return 1


                def uncovered():
                    return 2
                """
            ).lstrip()
        )

        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_e2e_generated_smoke.py").write_text(
            textwrap.dedent(
                """
                import sys, os
                sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
                from sample_src import covered


                def test_covered():
                    assert covered() == 1
                """
            ).lstrip()
        )

        symbols_file = tmp_path / "symbols.json"
        symbols_file.write_text(json.dumps({
            "name": "sample_src",
            "functions": [
                {"name": "covered", "line_start": 1, "line_end": 2, "is_public": True, "calls": []},
                {"name": "uncovered", "line_start": 4, "line_end": 5, "is_public": True, "calls": []},
            ],
            "classes": [],
            "testable_paths": [
                {"target": "covered", "type": "function", "line_range": [1, 2]},
                {"target": "uncovered", "type": "function", "line_range": [4, 5]},
            ],
        }))

        scenarios_file = tmp_path / "scenarios.json"
        scenarios_file.write_text(json.dumps({
            "scenarios": [
                {"name": "smoke", "steps": [], "coverage_targets": ["covered"]},
            ],
            "coverage_targets": ["covered", "uncovered"],
        }))

        out = tmp_path / "coverage_report.json"
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH),
                "--test-dir", str(test_dir),
                "--source-module", str(src),
                "--output", str(out),
                "--scenarios-file", str(scenarios_file),
                "--symbols-file", str(symbols_file),
                "--threshold", "0",
            ],
            capture_output=True, text=True,
        )
        assert out.exists(), f"no report emitted; stderr=\n{result.stderr}\nstdout=\n{result.stdout}"
        with open(out) as f:
            report = json.load(f)
        assert "gap_report" in report
        gr = report["gap_report"]
        assert "covered_targets" in gr
        assert "uncovered_targets" in gr
        assert "uncovered_targets" in gr
        # `uncovered` should be flagged — its only line is never hit by test.
        assert "uncovered" in gr["uncovered_targets"]


# ── Task-ansys contract: structure-level pin ──────────────────────────────

class TestReportShape:
    def test_top_level_keys(self, mod):
        """Coverage report must carry the enhanced keys."""
        # Build the structured report shape via the public helpers so the
        # shape contract is enforced without invoking pytest.
        uncovered = mod._identify_uncovered_symbols(
            SYMBOLS, COV_JSON_PARTIAL, "src/sample.py"
        )
        cross = mod._cross_reference_targets(
            ["covered_fn", "uncovered_fn", "nonexistent_target"], uncovered
        )
        gap = mod._build_gap_report(
            coverage_pct=50.0,
            threshold=85,
            uncovered_symbols=uncovered,
            cross_reference=cross,
        )
        for key in (
            "overall_verdict",
            "coverage_gap_pp",
            "missing_symbols",
            "partial_symbols",
            "unresolved_targets",
            "covered_targets",
            "uncovered_targets",
            "suggested_scenarios",
        ):
            assert key in gap, f"gap_report missing key: {key}"
