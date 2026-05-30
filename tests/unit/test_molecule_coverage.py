"""Unit tests for molecule coverage checker."""

from __future__ import annotations

import os
import tempfile

from agentic_harness.quality.molecule_coverage import (
    MoleculeCoverageChecker,
    MoleculeCoverageReport,
)


class TestMoleculeCoverageNoScenarios:
    def test_molecule_coverage_no_scenarios_means_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"noop.yml": os.path.join(tmp, "playbooks", "noop.yml")}
            checker = MoleculeCoverageChecker(
                playbook_registry=registry,
                scenario_roots=[os.path.join(tmp, "molecule", "playbooks")],
            )
            report = checker.compute_coverage()
            assert report.total_registered == 1
            assert report.total_covered == 0
            assert report.coverage_percent == 0.0
            assert report.uncovered == ["noop.yml"]


class TestMoleculeCoverageWithScenarios:
    def test_molecule_coverage_registered_playbook_with_scenario_is_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"noop.yml": os.path.join(tmp, "playbooks", "noop.yml")}
            scenario_root = os.path.join(tmp, "molecule", "playbooks", "noop", "default")
            os.makedirs(scenario_root)
            with open(os.path.join(scenario_root, "molecule.yml"), "w") as f:
                f.write("---\ndriver:\n  name: delegated\n")

            checker = MoleculeCoverageChecker(
                playbook_registry=registry,
                scenario_roots=[os.path.join(tmp, "molecule", "playbooks")],
            )
            covered = checker.get_covered_playbooks()
            assert "noop.yml" in covered

    def test_molecule_coverage_report_calculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {
                "noop.yml": os.path.join(tmp, "playbooks", "noop.yml"),
                "deploy.yml": os.path.join(tmp, "playbooks", "deploy.yml"),
            }
            scenario_root = os.path.join(tmp, "molecule", "playbooks", "noop", "default")
            os.makedirs(scenario_root)
            with open(os.path.join(scenario_root, "molecule.yml"), "w") as f:
                f.write("---\n")

            checker = MoleculeCoverageChecker(
                playbook_registry=registry,
                scenario_roots=[os.path.join(tmp, "molecule", "playbooks")],
            )
            report = checker.compute_coverage()
            assert report.total_registered == 2
            assert report.total_covered == 1
            assert report.coverage_percent == 50.0
            assert "noop.yml" in report.covered
            assert "deploy.yml" in report.uncovered


class TestMoleculeCoverageUncovered:
    def test_molecule_coverage_uncovered_playbooks_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {
                "noop.yml": os.path.join(tmp, "playbooks", "noop.yml"),
                "setup.yml": os.path.join(tmp, "playbooks", "setup.yml"),
                "teardown.yml": os.path.join(tmp, "playbooks", "teardown.yml"),
            }
            checker = MoleculeCoverageChecker(
                playbook_registry=registry,
                scenario_roots=[os.path.join(tmp, "molecule", "playbooks")],
            )
            uncovered = checker.get_uncovered_playbooks()
            assert set(uncovered) == {"noop.yml", "setup.yml", "teardown.yml"}


class TestMoleculeCoverageReportDataclass:
    def test_report_is_dataclass(self):
        report = MoleculeCoverageReport(
            total_registered=5,
            total_covered=3,
            coverage_percent=60.0,
            covered=["a.yml", "b.yml", "c.yml"],
            uncovered=["d.yml", "e.yml"],
        )
        assert report.total_registered == 5
        assert report.total_covered == 3
        assert report.coverage_percent == 60.0
        assert len(report.covered) == 3
        assert len(report.uncovered) == 2


class TestMoleculeCoverageHelpers:
    def test_get_registered_playbooks(self):
        registry = {"a.yml": "/p/a.yml", "b.yml": "/p/b.yml"}
        checker = MoleculeCoverageChecker(
            playbook_registry=registry,
            scenario_roots=["/molecule"],
        )
        assert set(checker.get_registered_playbooks()) == {"a.yml", "b.yml"}
