"""Structural tests for the CI precheck guardrail.

Verifies scripts/ci_precheck.py exists, the Makefile target is wired,
all 5 gate checks are present, and the script follows the exit-code +
error-summary contract.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci_precheck.py"
MAKEFILE_PATH = REPO_ROOT / "Makefile"

EXPECTED_CHECKS = (
    "lint",
    "typecheck",
    "test-count",
    "node-v26-compat",
    "readme-status",
)


def test_script_exists() -> None:
    assert SCRIPT_PATH.is_file(), f"{SCRIPT_PATH} does not exist"


def test_make_target_exists() -> None:
    content = MAKEFILE_PATH.read_text()
    assert "ci-precheck" in content, "make ci-precheck target not found in Makefile"


def test_all_five_checks_in_script() -> None:
    content = SCRIPT_PATH.read_text()
    for check in EXPECTED_CHECKS:
        assert check in content, (
            f"'{check}' not found in {SCRIPT_PATH} — "
            "expected all 5 checks (lint, typecheck, test-count, "
            "node-v26-compat, readme-status)"
        )


def test_script_is_executable() -> None:
    assert os.access(SCRIPT_PATH, os.X_OK), f"{SCRIPT_PATH} is not executable"


def test_exit_code_and_error_summary_format() -> None:
    """Script must use sys.exit with a non-zero code and print an error
    summary listing failed checks."""
    content = SCRIPT_PATH.read_text()
    assert "sys.exit" in content, f"{SCRIPT_PATH} must call sys.exit with exit code"
    assert any(phrase in content for phrase in ("FAILED", "failed", "ERROR", "error")), (
        f"{SCRIPT_PATH} must contain an error summary (FAILED/failed/ERROR/error)"
    )
