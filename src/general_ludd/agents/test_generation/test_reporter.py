"""TestReporter — scores pytest output into a structured TestReport.

Parses the terminal output from pytest to extract:
- pass/fail/skip counts
- coverage percentage (when pytest-cov is used)
- duration
- errors from stderr

Produces a :class:`TestReport` with verdict (pass/fail/error/partial).
"""

from __future__ import annotations

import logging
import re

from general_ludd.agents.test_generation.contracts import TestReport

logger = logging.getLogger(__name__)

_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
_SKIPPED_RE = re.compile(r"(\d+)\s+skipped")
_COVERAGE_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+)%")
_DURATION_RE = re.compile(r"in\s+([\d.]+)s")
_ERROR_RE = re.compile(r"ERROR|Error", re.IGNORECASE)


class TestReporter:
    @staticmethod
    def score(
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        generated_files: list[str],
    ) -> TestReport:
        passed = _parse_count(_PASSED_RE, stdout)
        failed = _parse_count(_FAILED_RE, stdout)
        skipped = _parse_count(_SKIPPED_RE, stdout)

        coverage_pct = _parse_coverage(stdout)
        duration = _parse_duration(stdout)

        errors: list[str] = []
        if stderr.strip():
            errors.extend(line.strip() for line in stderr.strip().splitlines() if line.strip())

        verdict = _compute_verdict(passed, failed, skipped, stderr, returncode)

        return TestReport(
            verdict=verdict,
            coverage_percent=coverage_pct,
            generated_files=list(generated_files),
            errors=errors,
            duration_seconds=duration,
        )


def _parse_count(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def _parse_coverage(text: str) -> float:
    match = _COVERAGE_RE.search(text)
    if match:
        return float(match.group(1))
    return 0.0


def _parse_duration(text: str) -> float:
    match = _DURATION_RE.search(text)
    if match:
        return float(match.group(1))
    return 0.0


def _compute_verdict(
    passed: int,
    failed: int,
    skipped: int,
    stderr: str,
    returncode: int,
) -> str:
    total = passed + failed + skipped

    if _ERROR_RE.search(stderr):
        return "error"

    if total == 0:
        return "error" if returncode != 0 else "partial"

    if returncode == 0 and failed == 0:
        return "pass"

    if failed > 0:
        return "fail"

    return "error"


__all__ = ["TestReporter"]
