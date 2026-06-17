"""Unit tests for quality gate tools."""

from __future__ import annotations

import os
import tempfile

from general_ludd.quality.gate import QualityGateChecker
from general_ludd.quality.tools import (
    CoverageResult,
    QualityGateResult,
    check_molecule_coverage,
    check_python_coverage,
    check_quality_gates,
)
from general_ludd.schemas.quality_gate import PythonQualityGate, QualityGateConfig


class TestCheckPythonCoverage:
    def test_check_python_coverage_passes(self):
        config = {
            "python": {
                "enabled": True,
                "line_coverage_min_percent": 80.0,
                "branch_coverage_min_percent": 70.0,
            }
        }
        result = check_python_coverage(config, line_coverage=85.0, branch_coverage=75.0)
        assert isinstance(result, CoverageResult)
        assert result.passes is True
        assert result.line_coverage == 85.0
        assert result.branch_coverage == 75.0

    def test_check_python_coverage_fails_below_threshold(self):
        config = {
            "python": {
                "enabled": True,
                "line_coverage_min_percent": 90.0,
                "branch_coverage_min_percent": 80.0,
            }
        }
        result = check_python_coverage(config, line_coverage=70.0, branch_coverage=60.0)
        assert result.passes is False
        assert result.line_coverage == 70.0
        assert result.target_line == 90.0
        assert result.target_branch == 80.0


class TestPythonCoverageShimDelegation:
    """The dict-based tools shim and the canonical pydantic-backed method must
    agree on pass/fail for the same line/branch inputs and thresholds."""

    def _params(self):
        return [
            (85.0, 75.0, 80.0, 70.0),  # both pass
            (70.0, 60.0, 90.0, 80.0),  # line fails
            (95.0, 60.0, 90.0, 80.0),  # branch fails
            (90.0, 80.0, 90.0, 80.0),  # exactly at threshold
            (89.99, 80.0, 90.0, 80.0),  # just below line
        ]

    def test_shim_agrees_with_method(self):
        for line, branch, target_line, target_branch in self._params():
            config = {
                "python": {
                    "enabled": True,
                    "line_coverage_min_percent": target_line,
                    "branch_coverage_min_percent": target_branch,
                }
            }
            shim = check_python_coverage(config, line_coverage=line, branch_coverage=branch)

            checker = QualityGateChecker(
                QualityGateConfig(
                    python=PythonQualityGate(
                        enabled=True,
                        line_coverage_min_percent=target_line,
                        branch_coverage_min_percent=target_branch,
                    )
                )
            )
            method = checker.check_python_coverage(
                coverage_percent=line, branch_percent=branch
            )

            assert shim.passes is method["passed"], (
                f"shim/method disagree for line={line} branch={branch} "
                f"target_line={target_line} target_branch={target_branch}: "
                f"shim={shim.passes} method={method['passed']}"
            )

    def test_shim_uses_default_thresholds(self):
        # No python config -> defaults (line 90, branch 80) from the canonical path.
        result = check_python_coverage({}, line_coverage=95.0, branch_coverage=85.0)
        assert result.target_line == 90.0
        assert result.target_branch == 80.0
        assert result.passes is True


class TestCheckMoleculeCoverageTools:
    def test_check_molecule_coverage_from_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"noop.yml": os.path.join(tmp, "noop.yml")}
            scenario_root = os.path.join(tmp, "molecule", "playbooks", "noop", "default")
            os.makedirs(scenario_root)
            with open(os.path.join(scenario_root, "molecule.yml"), "w") as f:
                f.write("---\n")

            config = {
                "molecule": {"enabled": True},
                "playbook_registry": registry,
                "scenario_roots": [os.path.join(tmp, "molecule", "playbooks")],
            }
            report = check_molecule_coverage(config)
            assert report.total_registered == 1
            assert report.total_covered == 1


class TestCheckQualityGates:
    def test_quality_gate_passes_when_above_threshold(self):
        config = {
            "python": {
                "enabled": True,
                "line_coverage_min_percent": 80.0,
                "branch_coverage_min_percent": 70.0,
            },
            "molecule": {"enabled": True},
            "playbook_registry": {},
            "scenario_roots": [],
        }
        result = check_quality_gates(config, line_coverage=90.0, branch_coverage=80.0)
        assert isinstance(result, QualityGateResult)
        assert result.passes is True

    def test_quality_gate_fails_when_below_threshold(self):
        config = {
            "python": {
                "enabled": True,
                "line_coverage_min_percent": 90.0,
                "branch_coverage_min_percent": 80.0,
            },
            "molecule": {"enabled": True},
            "playbook_registry": {},
            "scenario_roots": [],
        }
        result = check_quality_gates(config, line_coverage=50.0, branch_coverage=40.0)
        assert result.passes is False


class TestNoopMoleculeScenario:
    def test_noop_molecule_scenario_exists(self):
        scenario_dir = os.path.join("molecule", "playbooks", "noop", "default")
        assert os.path.isdir(scenario_dir), f"Scenario dir {scenario_dir} missing"
        assert os.path.isfile(os.path.join(scenario_dir, "molecule.yml"))
        assert os.path.isfile(os.path.join(scenario_dir, "converge.yml"))
        assert os.path.isfile(os.path.join(scenario_dir, "verify.yml"))


class TestPlaybooksExist:
    def test_playbooks_exist(self):
        for name in ("molecule_test.yml", "molecule_coverage_audit.yml", "quality_gate_validate.yml"):
            path = os.path.join("playbooks", name)
            assert os.path.isfile(path), f"Playbook {path} missing"


class TestToolsScripts:
    def test_tools_scripts_exist(self):
        for name in ("check_molecule_coverage.py", "check_quality_gates.py"):
            path = os.path.join("tools", name)
            assert os.path.isfile(path), f"Script {path} missing"
