"""A.6: Coverage --fail-under=0 workaround removal — verify fail_under=70 is the only gate."""

from __future__ import annotations

import re
from pathlib import Path

import tomllib


class TestPyprojectCoverageThreshold:
    def test_fail_under_is_at_least_70(self) -> None:
        content = Path("pyproject.toml").read_text()
        data = tomllib.loads(content)
        cc = data.get("tool", {}).get("coverage", {}).get("report", {})
        fail_under = cc.get("fail_under")
        assert fail_under is not None, (
            "pyproject.toml missing [tool.coverage.report].fail_under"
        )
        assert fail_under >= 70, (
            f"fail_under={fail_under} is below the 70 target; "
            "E1 coverage lift requires fail_under >= 70"
        )

    def test_fail_under_is_exactly_70(self) -> None:
        content = Path("pyproject.toml").read_text()
        data = tomllib.loads(content)
        cc = data.get("tool", {}).get("coverage", {}).get("report", {})
        fail_under = cc.get("fail_under")
        assert fail_under == 70, (
            f"fail_under={fail_under}; expected exactly 70 (the E1 target)"
        )


class TestBuildYmlNoFailUnderZeroWorkaround:
    def test_build_yml_exists_and_readable(self) -> None:
        path = Path(".github/workflows/build.yml")
        assert path.exists()
        assert path.stat().st_size > 0

    def test_no_fail_under_zero_on_coverage_report(self) -> None:
        content = Path(".github/workflows/build.yml").read_text()
        assert "--fail-under=0" not in content, (
            "build.yml still contains --fail-under=0 workaround on the coverage report; "
            "see A.6 — workaround must be removed now that fail_under=70 is the target"
        )

    def test_no_cov_fail_under_zero_on_pytest_shards(self) -> None:
        content = Path(".github/workflows/build.yml").read_text()
        assert "--cov-fail-under=0" not in content, (
            "build.yml still contains --cov-fail-under=0 workaround on test shards; "
            "see A.6 — workaround must be removed now that fail_under=70 is the target"
        )

    def test_coverage_report_step_no_longer_nongating(self) -> None:
        content = Path(".github/workflows/build.yml").read_text()
        assert re.search(r"NON-GATING", content) is None, (
            "build.yml still claims coverage is NON-GATING; "
            "the workaround removal makes it GATING via pyproject.toml fail_under=70"
        )

    def test_ci_gate_exact_still_references_fail_under_70(self) -> None:
        content = Path("Makefile").read_text()
        assert "fail_under=70" in content, (
            "Makefile ci-gate-exact comment must still reference fail_under=70"
        )
