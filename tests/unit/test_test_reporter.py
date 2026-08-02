"""Tests for TestReporter — scores test results into TestReport."""

from __future__ import annotations

from general_ludd.agents.test_generation.test_reporter import TestReporter


class TestReporterParsing:
    def test_parses_all_pass_output(self) -> None:
        output = (
            "============================= test session starts =============================\n"
            "collected 3 items\n"
            "test_foo.py::test_one PASSED                                              [ 33%]\n"
            "test_foo.py::test_two PASSED                                              [ 66%]\n"
            "test_foo.py::test_three PASSED                                            [100%]\n"
            "============================== 3 passed in 0.05s ==============================="
        )
        report = TestReporter.score(
            returncode=0,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.verdict == "pass"
        assert len(report.generated_files) == 1

    def test_parses_all_fail_output(self) -> None:
        output = (
            "collected 2 items\n"
            "test_foo.py::test_bad FAILED                                             [ 50%]\n"
            "test_foo.py::test_worse FAILED                                           [100%]\n"
            "=========================== 2 failed in 0.03s ============================"
        )
        report = TestReporter.score(
            returncode=1,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.verdict == "fail"

    def test_parses_mixed_pass_fail_output(self) -> None:
        output = (
            "collected 3 items\n"
            "test_foo.py::test_good PASSED                                            [ 33%]\n"
            "test_foo.py::test_bad FAILED                                             [ 66%]\n"
            "test_foo.py::test_ugly FAILED                                            [100%]\n"
            "========================= 1 passed, 2 failed in 0.04s ====================="
        )
        report = TestReporter.score(
            returncode=1,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.verdict == "fail"

    def test_parses_skip_output(self) -> None:
        output = (
            "collected 2 items\n"
            "test_foo.py::test_one SKIPPED                                            [ 50%]\n"
            "test_foo.py::test_two PASSED                                             [100%]\n"
            "========================= 1 passed, 1 skipped in 0.01s ===================="
        )
        report = TestReporter.score(
            returncode=0,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.verdict == "pass"

    def test_error_output_returns_error_verdict(self) -> None:
        output = "ImportError: No module named 'nonexistent'\n"
        report = TestReporter.score(
            returncode=1,
            stdout="",
            stderr=output,
            generated_files=[],
        )
        assert report.verdict == "error"

    def test_duration_extracted_from_output(self) -> None:
        output = (
            "collected 1 items\n"
            "test_foo.py::test_one PASSED                                             [100%]\n"
            "============================== 1 passed in 2.35s ==============================="
        )
        report = TestReporter.score(
            returncode=0,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.duration_seconds > 0


class TestReporterCoverage:
    def test_coverage_percent_extracted(self) -> None:
        output = (
            "collected 1 items\n"
            "test_foo.py::test_one PASSED                                             [100%]\n"
            "============================== 1 passed in 0.10s ===============================\n"
            "Name               Stmts   Miss  Cover\n"
            "----------------------------------------\n"
            "my_module.py          20      2    90%\n"
            "----------------------------------------\n"
            "TOTAL                 20      2    90%"
        )
        report = TestReporter.score(
            returncode=0,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.coverage_percent == 90.0

    def test_missing_coverage_defaults_zero(self) -> None:
        output = "test_foo.py::test_one PASSED\n= 1 passed in 0.01s ="
        report = TestReporter.score(
            returncode=0,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.coverage_percent == 0.0


class TestReporterEdgeCases:
    def test_empty_output_returns_error(self) -> None:
        report = TestReporter.score(
            returncode=1,
            stdout="",
            stderr="",
            generated_files=[],
        )
        assert report.verdict == "error"

    def test_no_collected_tests(self) -> None:
        output = "collected 0 items\n=========================== no tests ran in 0.01s ==========================="
        report = TestReporter.score(
            returncode=0,
            stdout=output,
            stderr="",
            generated_files=["test_foo.py"],
        )
        assert report.verdict == "partial"

    def test_errors_list_populated(self) -> None:
        stderr_output = "ERROR collecting test_foo.py\n"
        report = TestReporter.score(
            returncode=1,
            stdout="",
            stderr=stderr_output,
            generated_files=["test_foo.py"],
        )
        assert len(report.errors) > 0
