"""Exercise actual Make-to-native-CLI budget and plan argument forwarding."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("budget", ("1", "120"))
def test_make_plan_forwards_selected_budget_without_native_work(budget: str) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "check-molecule-yaml",
         f"MOLECULE_YAML_LINT_TIMEOUT_SECONDS={budget}", "MOLECULE_YAML_VALIDATE_ONLY=1"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"lint_timeout_seconds={budget}" in result.stdout
    assert "proof=none" in result.stdout
    assert "MOLECULE_VALIDATION_CONFIG_START" not in result.stdout


def test_make_plan_refuses_out_of_range_budget_before_work() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "check-molecule-yaml",
         "MOLECULE_YAML_LINT_TIMEOUT_SECONDS=121", "MOLECULE_YAML_VALIDATE_ONLY=1"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode != 0
    assert "integer from 1 through 120" in result.stderr
    assert "MOLECULE_VALIDATION_CONFIG_START" not in result.stdout
