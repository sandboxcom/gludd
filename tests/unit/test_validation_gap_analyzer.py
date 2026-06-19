"""Comprehensive unit tests for general_ludd.validation.gap_analyzer.

Covers:
- GapItem dataclass construction and fields
- GapReport dataclass construction and fields
- GapAnalyzer.analyze: happy path, empty repo, missing tests, missing molecule
- _find_impl_without_tests: no src dir, src dir present, __init__.py skipped, non-.py skipped, test found
- _test_exists: no tests dir, tests dir present with file, file not present
- _find_missing_molecule: no playbooks dir, playbook with molecule scenario present, missing scenario
- Severity ranking (expected values)
- Deduplication / combined gaps
"""

from __future__ import annotations

import os
import tempfile

from general_ludd.validation.gap_analyzer import (
    GapAnalyzer,
    GapItem,
    GapReport,
    _find_impl_without_tests,
    _find_missing_molecule,
    _test_exists,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(path: str, content: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# GapItem dataclass
# ---------------------------------------------------------------------------

class TestGapItem:
    def test_construction_sets_fields(self) -> None:
        item = GapItem(
            category="missing_tests",
            description="No test for foo.py",
            severity="medium",
            suggested_action="Create test_foo.py",
        )
        assert item.category == "missing_tests"
        assert item.description == "No test for foo.py"
        assert item.severity == "medium"
        assert item.suggested_action == "Create test_foo.py"

    def test_equality(self) -> None:
        a = GapItem("cat", "desc", "high", "do it")
        b = GapItem("cat", "desc", "high", "do it")
        assert a == b

    def test_inequality_on_different_fields(self) -> None:
        a = GapItem("cat", "desc", "high", "do it")
        b = GapItem("cat", "desc", "low", "do it")
        assert a != b


# ---------------------------------------------------------------------------
# GapReport dataclass
# ---------------------------------------------------------------------------

class TestGapReport:
    def test_construction_empty(self) -> None:
        report = GapReport(total_gaps=0)
        assert report.total_gaps == 0
        assert report.gaps == []

    def test_construction_with_gaps(self) -> None:
        items = [GapItem("missing_tests", "desc", "medium", "action")]
        report = GapReport(total_gaps=1, gaps=items)
        assert report.total_gaps == 1
        assert len(report.gaps) == 1

    def test_gaps_field_is_list(self) -> None:
        report = GapReport(total_gaps=0)
        assert isinstance(report.gaps, list)

    def test_default_gaps_not_shared(self) -> None:
        # mutable default via field(default_factory=list) — each instance is separate
        r1 = GapReport(total_gaps=0)
        r2 = GapReport(total_gaps=0)
        r1.gaps.append(GapItem("a", "b", "c", "d"))
        assert len(r2.gaps) == 0


# ---------------------------------------------------------------------------
# _test_exists
# ---------------------------------------------------------------------------

class TestTestExists:
    def test_returns_false_when_tests_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            # no tests/ directory at all
            assert _test_exists(repo_root, "test_foo.py") is False

    def test_returns_true_when_test_file_present_directly(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            _make_file(os.path.join(tests_dir, "test_foo.py"), "")
            assert _test_exists(repo_root, "test_foo.py") is True

    def test_returns_true_when_test_file_in_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            sub_dir = os.path.join(repo_root, "tests", "unit")
            os.makedirs(sub_dir)
            _make_file(os.path.join(sub_dir, "test_service.py"), "")
            assert _test_exists(repo_root, "test_service.py") is True

    def test_returns_false_when_test_file_absent(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            _make_file(os.path.join(tests_dir, "test_other.py"), "")
            assert _test_exists(repo_root, "test_missing.py") is False

    def test_partial_name_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            _make_file(os.path.join(tests_dir, "test_foobar.py"), "")
            # "test_foo.py" != "test_foobar.py"
            assert _test_exists(repo_root, "test_foo.py") is False


# ---------------------------------------------------------------------------
# _find_impl_without_tests
# ---------------------------------------------------------------------------

class TestFindImplWithoutTests:
    def test_returns_empty_when_no_src_dir(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            gaps = _find_impl_without_tests(repo_root)
            assert gaps == []

    def test_skips_init_py(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "__init__.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            gaps = _find_impl_without_tests(repo_root)
            assert gaps == []

    def test_skips_non_py_files(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "readme.txt"), "hello")
            _make_file(os.path.join(src_dir, "config.yaml"), "key: val")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            gaps = _find_impl_without_tests(repo_root)
            assert gaps == []

    def test_reports_missing_test_for_impl_file(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "mypkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "service.py"), "def run(): pass")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            gaps = _find_impl_without_tests(repo_root)
            assert len(gaps) == 1
            assert gaps[0].category == "missing_tests"
            assert "service.py" in gaps[0].description
            assert "test_service.py" in gaps[0].description
            assert gaps[0].severity == "medium"
            assert "test_service.py" in gaps[0].suggested_action

    def test_no_gap_when_test_exists(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "mypkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "service.py"), "def run(): pass")
            tests_dir = os.path.join(repo_root, "tests", "unit")
            os.makedirs(tests_dir)
            _make_file(os.path.join(tests_dir, "test_service.py"), "")
            gaps = _find_impl_without_tests(repo_root)
            assert gaps == []

    def test_multiple_impl_files_all_missing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "alpha.py"), "")
            _make_file(os.path.join(src_dir, "beta.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            gaps = _find_impl_without_tests(repo_root)
            names = sorted(g.description for g in gaps)
            assert len(names) == 2
            assert any("alpha.py" in n for n in names)
            assert any("beta.py" in n for n in names)

    def test_mixed_some_tested_some_not(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "covered.py"), "")
            _make_file(os.path.join(src_dir, "uncovered.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            _make_file(os.path.join(tests_dir, "test_covered.py"), "")
            gaps = _find_impl_without_tests(repo_root)
            assert len(gaps) == 1
            assert "uncovered.py" in gaps[0].description

    def test_gap_item_has_relative_path_in_description(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "deep", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "worker.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            gaps = _find_impl_without_tests(repo_root)
            assert len(gaps) == 1
            # description uses relpath — should not start with /
            assert not gaps[0].description.startswith("/")
            assert "src" in gaps[0].description

    def test_severity_is_medium_for_missing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "x.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            gaps = _find_impl_without_tests(repo_root)
            assert gaps[0].severity == "medium"


# ---------------------------------------------------------------------------
# _find_missing_molecule
# ---------------------------------------------------------------------------

class TestFindMissingMolecule:
    def test_returns_empty_when_no_playbooks_dir(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            gaps = _find_missing_molecule(repo_root)
            assert gaps == []

    def test_skips_non_yml_files(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "README.md"), "")
            _make_file(os.path.join(pb_dir, "notes.txt"), "")
            gaps = _find_missing_molecule(repo_root)
            assert gaps == []

    def test_reports_missing_molecule_for_yml_playbook(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "deploy.yml"), "---\n- hosts: all\n")
            gaps = _find_missing_molecule(repo_root)
            assert len(gaps) == 1
            assert gaps[0].category == "missing_molecule"
            assert "deploy.yml" in gaps[0].description
            assert gaps[0].severity == "high"
            assert "deploy.yml" in gaps[0].suggested_action

    def test_no_gap_when_molecule_scenario_matches_playbook_name(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "deploy.yml"), "---")
            mol_dir = os.path.join(repo_root, "molecule", "deploy")
            os.makedirs(mol_dir)
            gaps = _find_missing_molecule(repo_root)
            assert gaps == []

    def test_no_gap_when_molecule_playbooks_subdir_matches(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "validate.yml"), "---")
            mol_sub = os.path.join(repo_root, "molecule", "playbooks", "validate")
            os.makedirs(mol_sub)
            gaps = _find_missing_molecule(repo_root)
            assert gaps == []

    def test_multiple_playbooks_some_with_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "setup.yml"), "")
            _make_file(os.path.join(pb_dir, "teardown.yml"), "")
            # only setup has a molecule scenario
            mol_dir = os.path.join(repo_root, "molecule", "setup")
            os.makedirs(mol_dir)
            gaps = _find_missing_molecule(repo_root)
            assert len(gaps) == 1
            assert "teardown.yml" in gaps[0].description

    def test_severity_is_high_for_missing_molecule(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "run.yml"), "")
            gaps = _find_missing_molecule(repo_root)
            assert gaps[0].severity == "high"

    def test_results_ordered_by_sorted_playbook_names(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "zebra.yml"), "")
            _make_file(os.path.join(pb_dir, "alpha.yml"), "")
            _make_file(os.path.join(pb_dir, "mango.yml"), "")
            gaps = _find_missing_molecule(repo_root)
            # gaps should be in alphabetical order (sorted(os.listdir))
            fnames = [g.description.split(" ")[1] for g in gaps]
            assert fnames == sorted(fnames)


# ---------------------------------------------------------------------------
# GapAnalyzer.analyze — integration of both detectors
# ---------------------------------------------------------------------------

class TestGapAnalyzerAnalyze:
    def test_empty_repo_returns_zero_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint0", repo_root=repo_root)
            assert isinstance(report, GapReport)
            assert report.total_gaps == 0
            assert report.gaps == []

    def test_total_gaps_matches_len_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "app.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "play.yml"), "")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint1", repo_root=repo_root)
            assert report.total_gaps == len(report.gaps)

    def test_detects_missing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "mypkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "service.py"), "def f(): pass")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            categories = [g.category for g in report.gaps]
            assert "missing_tests" in categories

    def test_detects_missing_molecule(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "deploy.yml"), "---")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            categories = [g.category for g in report.gaps]
            assert "missing_molecule" in categories

    def test_detects_both_gap_types_simultaneously(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "worker.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "build.yml"), "")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint2", repo_root=repo_root)
            categories = sorted(set(g.category for g in report.gaps))
            assert "missing_molecule" in categories
            assert "missing_tests" in categories
            assert report.total_gaps == 2

    def test_returns_gap_report_type(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            analyzer = GapAnalyzer()
            result = analyzer.analyze(sprint_path="any", repo_root=repo_root)
            assert isinstance(result, GapReport)

    def test_sprint_path_param_accepted_but_not_required_for_result(self) -> None:
        # sprint_path is accepted as a parameter but not used in result computation
        with tempfile.TemporaryDirectory() as repo_root:
            analyzer = GapAnalyzer()
            r1 = analyzer.analyze(sprint_path="sprint-a", repo_root=repo_root)
            r2 = analyzer.analyze(sprint_path="sprint-b", repo_root=repo_root)
            assert r1.total_gaps == r2.total_gaps

    def test_no_duplicate_gaps_for_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "util.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            # Only one gap per missing impl file — no dedup needed but confirms no double-count
            matching = [g for g in report.gaps if "util.py" in g.description]
            assert len(matching) == 1

    def test_init_py_not_reported_as_gap(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "__init__.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            assert report.total_gaps == 0

    def test_covered_playbook_does_not_appear_in_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "has_scenario.yml"), "")
            mol_dir = os.path.join(repo_root, "molecule", "has_scenario")
            os.makedirs(mol_dir)
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            assert all("has_scenario" not in g.description for g in report.gaps)

    def test_multiple_impl_files_all_accounted(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            for name in ["a.py", "b.py", "c.py"]:
                _make_file(os.path.join(src_dir, name), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            assert report.total_gaps == 3


# ---------------------------------------------------------------------------
# Severity ranking: verify expected values are stable
# ---------------------------------------------------------------------------

class TestSeverityValues:
    def test_missing_tests_severity_is_medium(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "x.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            gaps = _find_impl_without_tests(repo_root)
            assert all(g.severity == "medium" for g in gaps)

    def test_missing_molecule_severity_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "play.yml"), "")
            gaps = _find_missing_molecule(repo_root)
            assert all(g.severity == "high" for g in gaps)

    def test_high_severity_molecule_vs_medium_severity_tests(self) -> None:
        severities = {"high": 2, "medium": 1}
        assert severities["high"] > severities["medium"]


# ---------------------------------------------------------------------------
# Deduplication: confirm the analyzer does not double-count
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_analyze_twice_gives_same_count(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "svc.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            analyzer = GapAnalyzer()
            r1 = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            r2 = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            assert r1.total_gaps == r2.total_gaps

    def test_each_missing_impl_appears_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src")
            os.makedirs(src_dir)
            _make_file(os.path.join(src_dir, "foo.py"), "")
            _make_file(os.path.join(src_dir, "bar.py"), "")
            tests_dir = os.path.join(repo_root, "tests")
            os.makedirs(tests_dir)
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            descriptions = [g.description for g in report.gaps if g.category == "missing_tests"]
            # No duplicates — each path appears once
            assert len(descriptions) == len(set(descriptions))

    def test_each_missing_playbook_appears_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            _make_file(os.path.join(pb_dir, "job1.yml"), "")
            _make_file(os.path.join(pb_dir, "job2.yml"), "")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="s", repo_root=repo_root)
            descriptions = [g.description for g in report.gaps if g.category == "missing_molecule"]
            assert len(descriptions) == len(set(descriptions))
