"""Test that run_gate.sh includes coverage flags (S.20 gate fix)."""

from __future__ import annotations

import os
from pathlib import Path

RUNNER = Path("scripts/run_ci_shards_serial.py")


class TestRunGateIncludesCoverageFlags:
    """S.20: run_gate.sh must pass --cov to pytest so coverage floor binds."""

    def test_run_gate_sh_exists_and_is_readable(self) -> None:
        path = Path("scripts/run_gate.sh")
        assert path.exists(), "scripts/run_gate.sh missing"
        assert os.access(path, os.R_OK), "run_gate.sh not readable"
        assert path.stat().st_size > 0, "run_gate.sh is empty"

    def test_run_gate_sh_includes_cov_flag(self) -> None:
        content = RUNNER.read_text()
        assert '"--cov"' in content, (
            "serial shard runner missing source-configured --cov flag"
        )
        assert "--show-missing" in content, (
            "serial shard runner must show missing lines in aggregate coverage"
        )

    def test_coverage_comes_before_basetemp(self) -> None:
        """Coverage flags must precede --basetemp so pytest sees them."""
        content = RUNNER.read_text()
        assert content.index('"--cov"') < content.index("--basetemp="), (
            "--cov must precede --basetemp in the shard pytest command"
        )

    def test_pyproject_toml_coverage_fail_under_set(self) -> None:
        import tomllib

        with open("pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        fail_under = config["tool"]["coverage"]["report"]["fail_under"]
        assert fail_under == 85, (
            f"Expected fail_under=85, got {fail_under}"
        )
