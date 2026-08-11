"""Unit tests for molecule coverage checker."""

from __future__ import annotations

import os
import tempfile

from general_ludd.quality.molecule_coverage import (
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


class TestMoleculeCoverageEdgeCases:
    def test_empty_registry_returns_zero_coverage(self):
        checker = MoleculeCoverageChecker(
            playbook_registry={},
            scenario_roots=["/nonexistent"],
        )
        report = checker.compute_coverage()
        assert report.total_registered == 0
        assert report.total_covered == 0
        assert report.coverage_percent == 0.0
        assert report.covered == []
        assert report.uncovered == []
        assert checker.get_registered_playbooks() == []
        assert checker.get_covered_playbooks() == []
        assert checker.get_uncovered_playbooks() == []

    def test_nonexistent_scenario_roots_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"play.yml": os.path.join(tmp, "playbooks", "play.yml")}
            checker = MoleculeCoverageChecker(
                playbook_registry=registry,
                scenario_roots=[
                    "/definitely/does/not/exist",
                    os.path.join(tmp, "molecule", "also", "not", "present"),
                ],
            )
            report = checker.compute_coverage()
            assert report.total_covered == 0
            assert report.uncovered == ["play.yml"]
            assert checker.get_covered_playbooks() == []

    def test_partial_scenario_roots_one_valid_one_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"play.yml": os.path.join(tmp, "playbooks", "play.yml")}
            valid_root = os.path.join(tmp, "molecule", "playbooks")
            scenario_dir = os.path.join(valid_root, "play", "default")
            os.makedirs(scenario_dir)
            with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                f.write("---\n")

            checker = MoleculeCoverageChecker(
                playbook_registry=registry,
                scenario_roots=["/nonexistent", valid_root],
            )
            covered = checker.get_covered_playbooks()
            assert "play.yml" in covered

    def test_multiple_roots_scenario_in_second_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"play.yml": os.path.join(tmp, "playbooks", "play.yml")}
            root1 = os.path.join(tmp, "molecule", "empty")
            os.makedirs(root1)
            root2 = os.path.join(tmp, "molecule", "playbooks")
            scenario_dir = os.path.join(root2, "play", "default")
            os.makedirs(scenario_dir)
            with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                f.write("---\n")

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root1, root2])
            assert checker.get_covered_playbooks() == ["play.yml"]

    def test_scenario_dir_with_no_molecule_yml_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"play.yml": os.path.join(tmp, "playbooks", "play.yml")}
            root = os.path.join(tmp, "molecule", "playbooks")
            scenario_dir = os.path.join(root, "play", "default")
            os.makedirs(scenario_dir)
            # No molecule.yml created

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            assert checker.get_covered_playbooks() == []

    def test_molecule_yml_is_directory_not_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"play.yml": os.path.join(tmp, "playbooks", "play.yml")}
            root = os.path.join(tmp, "molecule", "playbooks")
            molecule_yml_dir = os.path.join(root, "play", "default", "molecule.yml")
            os.makedirs(molecule_yml_dir)
            # molecule.yml is a directory, not a file → isfile → False

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            assert checker.get_covered_playbooks() == []

    def test_duplicate_cover_across_multiple_roots_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"play.yml": os.path.join(tmp, "playbooks", "play.yml")}
            roots: list[str] = []
            for suffix in ["a", "b"]:
                root = os.path.join(tmp, f"molecule{suffix}")
                scenario_dir = os.path.join(root, "play", "default")
                os.makedirs(scenario_dir)
                with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                    f.write("---\n")
                roots.append(root)

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=roots)
            covered = checker.get_covered_playbooks()
            assert covered == ["play.yml"]

    def test_all_covered_all_uncovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {
                "a.yml": os.path.join(tmp, "playbooks", "a.yml"),
                "b.yml": os.path.join(tmp, "playbooks", "b.yml"),
            }
            root = os.path.join(tmp, "molecule", "playbooks")
            for name in ["a", "b"]:
                scenario_dir = os.path.join(root, name, "default")
                os.makedirs(scenario_dir)
                with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                    f.write("---\n")

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            report = checker.compute_coverage()
            assert report.total_registered == 2
            assert report.total_covered == 2
            assert report.coverage_percent == 100.0
            assert report.uncovered == []
            assert checker.get_uncovered_playbooks() == []

    def test_coverage_percent_rounding(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {}
            for i in range(1, 4):
                registry[f"p{i}.yml"] = os.path.join(tmp, "playbooks", f"p{i}.yml")
            root = os.path.join(tmp, "molecule", "playbooks")
            scenario_dir = os.path.join(root, "p1", "default")
            os.makedirs(scenario_dir)
            with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                f.write("---\n")

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            report = checker.compute_coverage()
            assert report.coverage_percent == round(1 / 3 * 100, 2)

    def test_invariant_covered_plus_uncovered_equals_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {
                "a.yml": os.path.join(tmp, "playbooks", "a.yml"),
                "b.yml": os.path.join(tmp, "playbooks", "b.yml"),
                "c.yml": os.path.join(tmp, "playbooks", "c.yml"),
            }
            root = os.path.join(tmp, "molecule", "playbooks")
            for name in ["a", "b"]:
                d = os.path.join(root, name, "default")
                os.makedirs(d)
                with open(os.path.join(d, "molecule.yml"), "w") as f:
                    f.write("---\n")

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            report = checker.compute_coverage()
            assert len(report.covered) + len(report.uncovered) == report.total_registered
            assert set(report.covered) | set(report.uncovered) == set(registry.keys())

    def test_playbook_without_yml_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"deploy": os.path.join(tmp, "playbooks", "deploy")}
            root = os.path.join(tmp, "molecule", "playbooks")
            scenario_dir = os.path.join(root, "deploy", "default")
            os.makedirs(scenario_dir)
            with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                f.write("---\n")

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            covered = checker.get_covered_playbooks()
            assert "deploy" in covered

    def test_playbook_with_multiple_dots_in_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"my.playbook.yml": os.path.join(tmp, "playbooks", "my.playbook.yml")}
            root = os.path.join(tmp, "molecule", "playbooks")
            scenario_dir = os.path.join(root, "my.playbook", "default")
            os.makedirs(scenario_dir)
            with open(os.path.join(scenario_dir, "molecule.yml"), "w") as f:
                f.write("---\n")

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            covered = checker.get_covered_playbooks()
            assert "my.playbook.yml" in covered

    def test_scenario_subdir_has_no_molecule_yml_but_sibling_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = {"play.yml": os.path.join(tmp, "playbooks", "play.yml")}
            root = os.path.join(tmp, "molecule", "playbooks")
            default_dir = os.path.join(root, "play", "default")
            init_dir = os.path.join(root, "play", "init")
            os.makedirs(default_dir)
            os.makedirs(init_dir)
            with open(os.path.join(default_dir, "molecule.yml"), "w") as f:
                f.write("---\n")
            # init_dir has no molecule.yml, but default_dir does → covered

            checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[root])
            assert checker.get_covered_playbooks() == ["play.yml"]

    def test_get_registered_playbooks_sorted(self):
        registry = {"c.yml": "/p/c.yml", "a.yml": "/p/a.yml", "b.yml": "/p/b.yml"}
        checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=[])
        assert checker.get_registered_playbooks() == ["a.yml", "b.yml", "c.yml"]


class TestMoleculeCoverageReportInvariants:
    def test_report_fields_independent_of_constructor_args(self):
        report = MoleculeCoverageReport(
            total_registered=10,
            total_covered=4,
            coverage_percent=40.0,
            covered=["a.yml", "b.yml", "c.yml", "d.yml"],
            uncovered=[],  # mismatch with totals — fields store what they're given
        )
        assert report.total_registered == 10
        assert report.total_covered == 4
        assert report.coverage_percent == 40.0
        assert len(report.covered) == 4
        assert report.uncovered == []

    def test_default_factories_produce_empty_lists(self):
        report = MoleculeCoverageReport(total_registered=0, total_covered=0, coverage_percent=0.0)
        assert report.covered == []
        assert report.uncovered == []
