"""Behavioral contract for the repository-owned Markdown lint target."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURE_DOC = "docs/features/XMSS_BACKEND_SAFETY.md"
CONFIG = "config/markdownlint-cli2.jsonc"


def _run_make(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def test_lint_markdown_runs_locked_cli_against_explicit_file() -> None:
    result = _run_make(
        "lint-markdown",
        f"MARKDOWN_FILES={FEATURE_DOC}",
        f"MARKDOWNLINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Linting: 1 file" in output
    assert "Summary: 0 issues in 0 files" in output


def test_lint_markdown_requires_explicit_files() -> None:
    result = _run_make(
        "lint-markdown",
        "MARKDOWN_FILES=",
        f"MARKDOWNLINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Usage: make lint-markdown" in output


def test_lint_markdown_dependency_and_contract_are_exactly_pinned() -> None:
    package = json.loads((ROOT / ".opencode" / "package.json").read_text())
    assert package["devDependencies"]["markdownlint-cli2"] == "0.23.2"

    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text()
    )
    entry = next(
        item for item in contract["targets"] if item["name"] == "lint-markdown"
    )
    assert entry["make_variables"] == [
        "MARKDOWN_FILES",
        "MARKDOWNLINT_CONFIG",
    ]
    assert entry["behavior"] == (
        "make lint-markdown "
        "MARKDOWN_FILES=docs/features/XMSS_BACKEND_SAFETY.md "
        "MARKDOWNLINT_CONFIG=config/markdownlint-cli2.jsonc"
    )
