"""Behavioral contract for the repository-owned CSS lint target."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS_FILE = "docs/presentation/deck/presentation.css"
CONFIG = "config/stylelint.config.mjs"
STYLELINT_VERSION = "17.16.0"


def _run_make(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )


def test_lint_css_runs_locked_cli_against_explicit_file() -> None:
    result = _run_make(
        "lint-css",
        f"CSS_FILES={CSS_FILE}",
        f"STYLELINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output


def test_lint_css_requires_explicit_files() -> None:
    result = _run_make(
        "lint-css",
        "CSS_FILES=",
        f"STYLELINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Usage: make lint-css" in output


def test_lint_css_dependency_config_and_contract_are_exactly_pinned() -> None:
    package = json.loads((ROOT / ".opencode" / "package.json").read_text())
    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text())
    assert package["devDependencies"]["stylelint"] == STYLELINT_VERSION
    assert package["overrides"] == {
        "katex": "0.18.10",
        "smol-toml": "1.9.0",
    }
    assert lock["packages"][""]["devDependencies"]["stylelint"] == STYLELINT_VERSION
    assert lock["packages"]["node_modules/stylelint"]["version"] == STYLELINT_VERSION
    assert lock["packages"]["node_modules/katex"]["version"] == "0.18.10"
    assert lock["packages"]["node_modules/smol-toml"]["version"] == "1.9.0"

    config = (ROOT / CONFIG).read_text()
    assert '"block-no-empty": true' in config
    assert '"color-no-invalid-hex": true' in config
    assert '"declaration-block-no-duplicate-properties": [' in config
    assert '"consecutive-duplicates-with-different-values"' in config

    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text()
    )
    entry = next(item for item in contract["targets"] if item["name"] == "lint-css")
    assert entry["make_variables"] == ["CSS_FILES", "STYLELINT_CONFIG"]
    assert entry["behavior"] == (
        "make lint-css "
        "CSS_FILES=docs/presentation/deck/presentation.css "
        "STYLELINT_CONFIG=config/stylelint.config.mjs"
    )


def test_hosted_css_lint_calls_repository_target_with_explicit_variables() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
    assert "npx stylelint" not in workflow
    assert (
        "make lint-css "
        "CSS_FILES='\"**/*.css\" \"**/*.scss\" \"**/*.less\"' "
        "STYLELINT_CONFIG=config/stylelint.config.mjs"
    ) in workflow
