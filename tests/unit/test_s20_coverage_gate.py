"""Test that run_gate.sh includes coverage flags + floor enforcement (S.20)."""

from __future__ import annotations

import os
import re
from pathlib import Path


class TestRunGateIncludesCoverageFlags:
    """S.20: run_gate.sh must pass --cov + --cov-fail-under so coverage floor binds."""

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

    def test_run_gate_sh_includes_cov_report_flags(self) -> None:
        content = Path("scripts/run_gate.sh").read_text()
        assert "--cov-report=term-missing" in content, (
            "run_gate.sh missing --cov-report=term-missing flag"
        )
        assert "--cov-report=xml" in content, (
            "run_gate.sh missing --cov-report=xml flag"
        )

    def test_run_gate_sh_includes_cov_fail_under(self) -> None:
        """S.20 core fix: --cov-fail-under enforces coverage floor via exit code."""
        content = Path("scripts/run_gate.sh").read_text()
        assert "--cov-fail-under=70" in content, (
            "run_gate.sh missing --cov-fail-under=70 flag; coverage floor never binds"
        )

    def test_cov_fail_under_comes_before_basetemp(self) -> None:
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
        assert "--cov-fail-under" in args_block, (
            "--cov-fail-under not found before --basetemp in run_gate.sh"
        )

    def test_pyproject_toml_coverage_fail_under_set(self) -> None:
        import tomllib
        with open("pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        fail_under = config["tool"]["coverage"]["report"]["fail_under"]
        assert fail_under == 70, (
            f"Expected fail_under=70, got {fail_under}"
        )

    def test_fail_under_consistency(self) -> None:
        """S.20: script's --cov-fail-under must match pyproject.toml fail_under."""
        content = Path("scripts/run_gate.sh").read_text()
        m = re.search(r"--cov-fail-under=(\d+)", content)
        assert m is not None, "Could not find --cov-fail-under=N in run_gate.sh"
        script_threshold = int(m.group(1))

        import tomllib
        with open("pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        pyproject_threshold = config["tool"]["coverage"]["report"]["fail_under"]
        assert script_threshold == pyproject_threshold, (
            f"--cov-fail-under={script_threshold} in run_gate.sh but "
            f"pyproject.toml fail_under={pyproject_threshold}; must match"
        )

    def test_cov_fail_under_not_commented_out(self) -> None:
        content = Path("scripts/run_gate.sh").read_text()
        for line in content.splitlines():
            line = line.strip()
            if "--cov-fail-under" in line:
                assert not line.lstrip().startswith("#"), (
                    "--cov-fail-under is commented out in run_gate.sh"
                )
