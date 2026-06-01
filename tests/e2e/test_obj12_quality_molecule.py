"""E2E: Objective 12 — Quality gates, Molecule coverage, and enforcement."""

from __future__ import annotations

import os
import tempfile

from general_ludd.quality import (
    MoleculeCoverageChecker,
    MoleculeCoverageReport,
    QualityGateChecker,
    QualityGateResult,
    check_molecule_coverage,
    check_python_coverage,
    check_quality_gates,
)
from general_ludd.quality.config import (
    EnforcementGate,
    MoleculeQualityGate,
    PythonQualityGate,
    QualityGateConfig,
)


class TestMoleculeCoverageCheckerImportAndInit:
    def test_import_from_quality_package(self):
        checker = MoleculeCoverageChecker(playbook_registry={}, scenario_roots=[])
        assert checker.playbook_registry == {}
        assert checker.scenario_roots == []

    def test_import_with_registry(self):
        registry = {"noop.yml": "playbooks/noop.yml"}
        checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=["/tmp/molecule"])
        assert "noop.yml" in checker.playbook_registry

    def test_get_registered_playbooks_sorted(self):
        registry = {"b.yml": "playbooks/b.yml", "a.yml": "playbooks/a.yml"}
        checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[])
        assert checker.get_registered_playbooks() == ["a.yml", "b.yml"]

    def test_empty_registry_returns_empty_list(self):
        checker = MoleculeCoverageChecker(playbook_registry={}, scenario_roots=[])
        assert checker.get_registered_playbooks() == []


class TestCoverageThresholdChecking:
    def test_python_coverage_passes_threshold(self):
        config = {"python": {"line_coverage_min_percent": 80.0, "branch_coverage_min_percent": 70.0}}
        result = check_python_coverage(config, line_coverage=90.0, branch_coverage=80.0)
        assert result.passes
        assert result.line_coverage == 90.0
        assert result.branch_coverage == 80.0

    def test_python_coverage_fails_line_threshold(self):
        config = {"python": {"line_coverage_min_percent": 90.0, "branch_coverage_min_percent": 80.0}}
        result = check_python_coverage(config, line_coverage=50.0, branch_coverage=95.0)
        assert not result.passes

    def test_python_coverage_fails_branch_threshold(self):
        config = {"python": {"line_coverage_min_percent": 80.0, "branch_coverage_min_percent": 80.0}}
        result = check_python_coverage(config, line_coverage=95.0, branch_coverage=50.0)
        assert not result.passes

    def test_python_coverage_defaults_when_config_empty(self):
        result = check_python_coverage({}, line_coverage=95.0, branch_coverage=85.0)
        assert result.passes
        assert result.target_line == 90.0
        assert result.target_branch == 80.0

    def test_quality_gate_checker_python_passes(self):
        cfg = QualityGateConfig(
            python=PythonQualityGate(
                line_coverage_min_percent=50.0,
                branch_coverage_min_percent=40.0,
            )
        )
        checker = QualityGateChecker(config=cfg)
        result = checker.check_python_coverage(coverage_percent=60.0, branch_percent=50.0)
        assert result["passed"]

    def test_quality_gate_checker_python_fails(self):
        cfg = QualityGateConfig(python=PythonQualityGate(line_coverage_min_percent=90.0))
        checker = QualityGateChecker(config=cfg)
        result = checker.check_python_coverage(coverage_percent=50.0)
        assert not result["passed"]

    def test_quality_gate_checker_python_disabled(self):
        cfg = QualityGateConfig(python=PythonQualityGate(enabled=False))
        checker = QualityGateChecker(config=cfg)
        result = checker.check_python_coverage(coverage_percent=10.0)
        assert result["passed"]
        assert result["checks"][0]["skipped"]

    def test_quality_gate_checker_molecule_passes(self):
        checker = QualityGateChecker()
        result = checker.check_molecule_coverage(covered=5, required=5)
        assert result["passed"]

    def test_quality_gate_checker_molecule_fails(self):
        cfg = QualityGateConfig(molecule=MoleculeQualityGate(coverage_min_percent=100.0))
        checker = QualityGateChecker(config=cfg)
        result = checker.check_molecule_coverage(covered=3, required=5)
        assert not result["passed"]

    def test_quality_gate_checker_molecule_disabled(self):
        cfg = QualityGateConfig(molecule=MoleculeQualityGate(enabled=False))
        checker = QualityGateChecker(config=cfg)
        result = checker.check_molecule_coverage(covered=0, required=5)
        assert result["passed"]
        assert result["skipped"]

    def test_quality_gate_checker_molecule_zero_required(self):
        checker = QualityGateChecker()
        result = checker.check_molecule_coverage(covered=0, required=0)
        assert result["passed"]

    def test_enforce_all_passed(self):
        checker = QualityGateChecker()
        gates = [{"passed": True}, {"passed": True}]
        result = checker.enforce(gates)
        assert result["all_passed"]
        assert not result["blocks_completion"]

    def test_enforce_one_failed(self):
        cfg = QualityGateConfig(enforcement=EnforcementGate(block_todo_complete=True))
        checker = QualityGateChecker(config=cfg)
        gates = [{"passed": True}, {"passed": False}]
        result = checker.enforce(gates)
        assert not result["all_passed"]
        assert result["blocks_completion"]

    def test_enforce_blocks_commit_and_merge(self):
        cfg = QualityGateConfig(enforcement=EnforcementGate(block_commit=True, block_merge=True))
        checker = QualityGateChecker(config=cfg)
        result = checker.enforce([{"passed": False}])
        assert result["blocks_commit"]
        assert result["blocks_merge"]


class TestMoleculeCoverageCompute:
    def test_compute_with_no_root_dirs(self):
        checker = MoleculeCoverageChecker(
            playbook_registry={"noop.yml": "playbooks/noop.yml"},
            scenario_roots=["/nonexistent/path"],
        )
        report = checker.compute_coverage()
        assert report.total_registered == 1
        assert report.total_covered == 0
        assert report.coverage_percent == 0.0
        assert report.uncovered == ["noop.yml"]

    def test_compute_with_scenario_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = os.path.join(tmpdir, "noop", "default")
            os.makedirs(scenario_dir)
            with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                f.write("---\n")
            checker = MoleculeCoverageChecker(
                playbook_registry={"noop.yml": "playbooks/noop.yml"},
                scenario_roots=[tmpdir],
            )
            report = checker.compute_coverage()
            assert report.total_registered == 1
            assert report.total_covered == 1
            assert report.coverage_percent == 100.0
            assert "noop.yml" in report.covered

    def test_compute_empty_registry(self):
        checker = MoleculeCoverageChecker(playbook_registry={}, scenario_roots=[])
        report = checker.compute_coverage()
        assert report.total_registered == 0
        assert report.total_covered == 0
        assert report.coverage_percent == 0.0

    def test_get_covered_playbooks_returns_sorted_unique(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for scenario in ["default", "edge"]:
                scenario_dir = os.path.join(tmpdir, "noop", scenario)
                os.makedirs(scenario_dir)
                with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                    f.write("---\n")
            checker = MoleculeCoverageChecker(
                playbook_registry={"noop.yml": "playbooks/noop.yml"},
                scenario_roots=[tmpdir],
            )
            covered = checker.get_covered_playbooks()
            assert covered == ["noop.yml"]

    def test_get_uncovered_playbooks(self):
        checker = MoleculeCoverageChecker(
            playbook_registry={"a.yml": "playbooks/a.yml", "b.yml": "playbooks/b.yml"},
            scenario_roots=["/nonexistent"],
        )
        uncovered = checker.get_uncovered_playbooks()
        assert "a.yml" in uncovered
        assert "b.yml" in uncovered


class TestCheckMoleculeCoverageTool:
    def test_check_molecule_coverage_function(self):
        config = {"playbook_registry": {"noop.yml": "playbooks/noop.yml"}, "scenario_roots": ["/nonexistent"]}
        report = check_molecule_coverage(config)
        assert isinstance(report, MoleculeCoverageReport)
        assert report.total_registered == 1

    def test_check_quality_gates_integration(self):
        config = {
            "python": {"line_coverage_min_percent": 50.0, "branch_coverage_min_percent": 40.0},
            "playbook_registry": {},
            "scenario_roots": [],
        }
        result = check_quality_gates(config, line_coverage=60.0, branch_coverage=50.0)
        assert isinstance(result, QualityGateResult)
        assert result.passes


class TestQualityGateCheckScriptExistence:
    def test_quality_gate_validate_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "playbooks", "quality_gate_validate.yml"))

    def test_molecule_coverage_audit_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "playbooks", "molecule_coverage_audit.yml"))

    def test_molecule_test_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "playbooks", "molecule_test.yml"))


class TestPlaybookStubsForMoleculeScenarios:
    def test_noop_molecule_scenario_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        scenario_dir = os.path.join(repo_root, "molecule", "playbooks", "noop", "default")
        assert os.path.isdir(scenario_dir)

    def test_noop_molecule_has_molecule_yml(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "molecule", "playbooks", "noop", "default", "molecule.yml"))

    def test_noop_molecule_has_converge_yml(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "molecule", "playbooks", "noop", "default", "converge.yml"))

    def test_noop_molecule_has_verify_yml(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "molecule", "playbooks", "noop", "default", "verify.yml"))

    def test_molecule_scenario_detects_noop_from_real_root(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        mol_root = os.path.join(repo_root, "molecule", "playbooks")
        checker = MoleculeCoverageChecker(
            playbook_registry={"noop.yml": "playbooks/noop.yml"},
            scenario_roots=[mol_root],
        )
        report = checker.compute_coverage()
        assert report.total_covered >= 1
        assert "noop.yml" in report.covered
