"""Test that run_gate.sh includes coverage flags (S.20 gate fix)."""

from __future__ import annotations

import os
import re
from pathlib import Path


class TestRunGateIncludesCoverageFlags:
    """S.20: run_gate.sh must pass --cov to pytest so coverage floor binds."""

    def test_run_gate_sh_exists_and_is_readable(self) -> None:
        path = Path("scripts/run_gate.sh")
        assert path.exists(), "scripts/run_gate.sh missing"
        assert os.access(path, os.R_OK), "run_gate.sh not readable"
        assert path.stat().st_size > 0, "run_gate.sh is empty"

    def test_run_gate_sh_includes_cov_flag(self) -> None:
        content = Path("scripts/run_gate.sh").read_text()
        assert "--cov=general_ludd" in content, (
            "run_gate.sh missing --cov=general_ludd flag"
        )
        assert "--cov-report=term-missing" in content, (
            "run_gate.sh missing --cov-report=term-missing flag"
        )

    def test_coverage_comes_before_basetemp(self) -> None:
        """Coverage flags must precede --basetemp so pytest sees them."""
        content = Path("scripts/run_gate.sh").read_text()
        m = re.search(
            r"adaptive_test\.py\s+tests/\s+-q\s+(.*?)--basetemp",
            content,
            re.DOTALL,
        )
        assert m is not None, "Could not find adaptive_test.py call in run_gate.sh"
        args_block = m.group(1)
        assert "--cov" in args_block, (
            "--cov not found before --basetemp in run_gate.sh"
        )

    def test_pyproject_toml_coverage_fail_under_set(self) -> None:
        import tomllib

        with open("pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        fail_under = config["tool"]["coverage"]["report"]["fail_under"]
        assert fail_under == 70, (
            f"Expected fail_under=70, got {fail_under}"
        )
