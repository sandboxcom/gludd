"""Test that run_gate.sh includes coverage flags + floor enforcement (S.20)."""

from __future__ import annotations

import os
import re
from pathlib import Path

RUNNER = Path("scripts/run_ci_shards_serial.py")


class TestRunGateIncludesCoverageFlags:
    """S.20: run_gate.sh must pass --cov + --cov-fail-under so coverage floor binds."""

    def test_run_gate_sh_exists_and_is_readable(self) -> None:
        path = Path("scripts/run_gate.sh")
        assert path.exists(), "scripts/run_gate.sh missing"
        assert os.access(path, os.R_OK), "run_gate.sh not readable"
        assert path.stat().st_size > 0, "run_gate.sh is empty"

    def test_run_gate_sh_includes_cov_flag(self) -> None:
        content = RUNNER.read_text()
        assert "--cov=general_ludd" in content, (
            "serial shard runner missing --cov=general_ludd flag"
        )

    def test_run_gate_sh_includes_cov_report_flags(self) -> None:
        content = RUNNER.read_text()
        assert "--show-missing" in content, (
            "serial shard runner must show missing lines"
        )
        assert '"xml"' in content, (
            "serial shard runner must emit coverage.xml"
        )

    def test_run_gate_sh_includes_cov_fail_under(self) -> None:
        """S.20 core fix: aggregate coverage enforces the floor via exit code."""
        content = RUNNER.read_text()
        assert "--fail-under=85" in content, (
            "serial shard runner missing aggregate --fail-under=85"
        )

    def test_cov_fail_under_comes_before_basetemp(self) -> None:
        content = RUNNER.read_text()
        assert content.index("--cov=general_ludd") < content.index("--basetemp="), (
            "--cov must precede --basetemp in each shard command"
        )

    def test_pyproject_toml_coverage_fail_under_set(self) -> None:
        import tomllib
        with open("pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        fail_under = config["tool"]["coverage"]["report"]["fail_under"]
        assert fail_under == 85, (
            f"Expected fail_under=85, got {fail_under}"
        )

    def test_fail_under_consistency(self) -> None:
        """S.20: script's --cov-fail-under must match pyproject.toml fail_under."""
        content = RUNNER.read_text()
        m = re.search(r"--fail-under=(\d+)", content)
        assert m is not None, "Could not find aggregate --fail-under=N"
        script_threshold = int(m.group(1))

        import tomllib
        with open("pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        pyproject_threshold = config["tool"]["coverage"]["report"]["fail_under"]
        assert script_threshold == pyproject_threshold, (
            f"--fail-under={script_threshold} in the serial shard runner but "
            f"pyproject.toml fail_under={pyproject_threshold}; must match"
        )

    def test_cov_fail_under_not_commented_out(self) -> None:
        content = RUNNER.read_text()
        for line in content.splitlines():
            line = line.strip()
            if "--fail-under=85" in line:
                assert not line.lstrip().startswith("#"), (
                    "aggregate --fail-under is commented out"
                )
