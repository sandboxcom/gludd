"""A.6: Coverage threshold verification for sharded CI and aggregate gate."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


class TestPyprojectCoverageThreshold:
    def test_fail_under_is_at_least_85(self) -> None:
        content = Path("pyproject.toml").read_text()
        data = tomllib.loads(content)
        cc = data.get("tool", {}).get("coverage", {}).get("report", {})
        fail_under = cc.get("fail_under")
        assert fail_under is not None, (
            "pyproject.toml missing [tool.coverage.report].fail_under"
        )
        assert fail_under >= 85, (
            f"fail_under={fail_under} is below the 85 target; "
            "E1 coverage lift requires fail_under >= 85"
        )

    def test_fail_under_is_exactly_85(self) -> None:
        content = Path("pyproject.toml").read_text()
        data = tomllib.loads(content)
        cc = data.get("tool", {}).get("coverage", {}).get("report", {})
        fail_under = cc.get("fail_under")
        assert fail_under == 85, (
            f"fail_under={fail_under}; expected exactly 85 (the E1 target)"
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
            "see A.6 — workaround must be removed now that fail_under=85 is the target"
        )

    def test_shared_shard_runner_defers_threshold_to_aggregate(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text()
        runner = Path("scripts/run_ci_shards_serial.py").read_text()
        assert "scripts/run_ci_shards_serial.py" in workflow
        assert '"--cov-fail-under=0"' in runner, (
            "the shared local/hosted runner must defer the threshold for each "
            "partial batch; aggregate coverage enforces fail_under=85"
        )
        assert '"--cov"' in runner
        assert "--cov=general_ludd" not in runner
        assert "src/general_ludd" in Path(".coveragerc-greenlet").read_text()
        assert "_aggregate_coverage" in runner

    def test_coverage_report_step_no_longer_nongating(self) -> None:
        content = Path(".github/workflows/build.yml").read_text()
        assert re.search(r"NON-GATING", content) is None, (
            "build.yml still claims coverage is NON-GATING; "
            "the workaround removal makes it GATING via pyproject.toml fail_under=85"
        )

    def test_ci_gate_exact_still_references_fail_under_85(self) -> None:
        content = Path("Makefile").read_text()
        assert "fail_under=85" in content, (
            "Makefile ci-gate-exact comment must still reference fail_under=85"
        )
