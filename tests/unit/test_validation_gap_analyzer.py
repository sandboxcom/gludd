"""Unit tests for validation/gap_analyzer.py — test and molecule coverage gap detection."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from general_ludd.validation.gap_analyzer import (
    GapAnalyzer,
    GapItem,
    GapReport,
    _find_impl_without_tests,
    _find_missing_molecule,
    _test_exists,
)


class TestGapItem:
    def test_construction(self) -> None:
        item = GapItem(
            category="missing_tests",
            description="No test for foo.py",
            severity="high",
            suggested_action="Create test_foo.py",
        )
        assert item.category == "missing_tests"
        assert item.severity == "high"
        assert item.description == "No test for foo.py"


class TestGapReport:
    def test_construction(self) -> None:
        report = GapReport(total_gaps=3)
        assert report.total_gaps == 3
        assert report.gaps == []

    def test_total_matches_gaps_length(self) -> None:
        items = [
            GapItem(category="x", description="d1", severity="low", suggested_action="a1"),
            GapItem(category="y", description="d2", severity="med", suggested_action="a2"),
        ]
        report = GapReport(total_gaps=len(items), gaps=items)
        assert report.total_gaps == 2
        assert len(report.gaps) == 2


class TestTestExists:
    def test_finds_matching_test_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            tests_dir = os.path.join(root, "tests", "unit")
            os.makedirs(tests_dir)
            Path(os.path.join(tests_dir, "test_sanitize.py")).touch()
            assert _test_exists(root, "test_sanitize.py") is True

    def test_returns_false_when_no_tests_dir(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            assert _test_exists(root, "test_nonexistent.py") is False

    def test_returns_false_when_test_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            tests_dir = os.path.join(root, "tests")
            os.makedirs(tests_dir)
            Path(os.path.join(tests_dir, "test_other.py")).touch()
            assert _test_exists(root, "test_missing.py") is False


class TestFindImplWithoutTests:
    def test_detects_untested_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src")
            os.makedirs(src_dir)
            Path(os.path.join(src_dir, "untested_module.py")).touch()
            Path(os.path.join(src_dir, "__init__.py")).touch()
            gaps = _find_impl_without_tests(root)
            assert len(gaps) >= 1
            assert any("untested_module" in g.description for g in gaps)

    def test_skips_init_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src")
            os.makedirs(src_dir)
            Path(os.path.join(src_dir, "__init__.py")).touch()
            gaps = _find_impl_without_tests(root)
            init_gaps = [g for g in gaps if "__init__" in g.description]
            assert len(init_gaps) == 0

    def test_skips_tested_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src")
            tests_dir = os.path.join(root, "tests")
            os.makedirs(src_dir)
            os.makedirs(tests_dir)
            Path(os.path.join(src_dir, "covered.py")).touch()
            Path(os.path.join(tests_dir, "test_covered.py")).touch()
            gaps = _find_impl_without_tests(root)
            covered = [g for g in gaps if "covered" in g.description]
            assert len(covered) == 0

    def test_no_src_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            gaps = _find_impl_without_tests(root)
            assert gaps == []


class TestFindMissingMolecule:
    def test_detects_playbook_without_molecule(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            pb_dir = os.path.join(root, "playbooks")
            os.makedirs(pb_dir)
            Path(os.path.join(pb_dir, "deploy.yml")).touch()
            gaps = _find_missing_molecule(root)
            assert len(gaps) >= 1
            assert any("deploy.yml" in g.description for g in gaps)

    def test_playbook_with_scenario_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            pb_dir = os.path.join(root, "playbooks")
            mol_dir = os.path.join(root, "molecule", "deploy")
            os.makedirs(pb_dir)
            os.makedirs(mol_dir)
            Path(os.path.join(pb_dir, "deploy.yml")).touch()
            gaps = _find_missing_molecule(root)
            assert all("deploy.yml" not in g.description for g in gaps)

    def test_no_playbook_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            gaps = _find_missing_molecule(root)
            assert gaps == []

    def test_skips_non_yml_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            pb_dir = os.path.join(root, "playbooks")
            os.makedirs(pb_dir)
            Path(os.path.join(pb_dir, "README.md")).touch()
            gaps = _find_missing_molecule(root)
            assert all("README.md" not in g.description for g in gaps)


class TestGapAnalyzer:
    def test_analyze_returns_gap_report(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src")
            os.makedirs(src_dir)
            Path(os.path.join(src_dir, "untested.py")).touch()
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="", repo_root=root)
            assert isinstance(report, GapReport)
            assert report.total_gaps >= 1

    def test_analyze_with_tested_code_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src")
            tests_dir = os.path.join(root, "tests")
            os.makedirs(src_dir)
            os.makedirs(tests_dir)
            Path(os.path.join(src_dir, "covered.py")).touch()
            Path(os.path.join(tests_dir, "test_covered.py")).touch()
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="", repo_root=root)
            untested = [g for g in report.gaps if "covered" in g.description]
            assert len(untested) == 0
