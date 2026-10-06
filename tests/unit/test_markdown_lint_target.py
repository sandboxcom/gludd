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
    assert f"MARKDOWNLINT_INPUTS files={FEATURE_DOC}" in output
    assert "ERROR:" not in output


def test_lint_markdown_requires_explicit_files() -> None:
    result = _run_make(
        "lint-markdown",
        "MARKDOWN_FILES=",
        f"MARKDOWNLINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Usage: make lint-markdown" in output


def test_lint_markdown_accepts_one_literal_recursive_glob() -> None:
    result = _run_make(
        "lint-markdown",
        'MARKDOWN_FILES="**/*.md"',
        f"MARKDOWNLINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "too many arguments" not in output
    assert "MARKDOWNLINT_INPUTS files=**/*.md" in output


def test_lint_markdown_dependency_and_contract_are_exactly_pinned() -> None:
    package = json.loads((ROOT / ".opencode" / "package.json").read_text())
    assert package["devDependencies"]["markdownlint-cli"] == "0.49.1"
    assert "markdownlint-cli2" not in package["devDependencies"]
    assert package["overrides"] == {
        "js-yaml": "5.4.1",
        "katex": "0.18.2",
        "smol-toml": "1.9.0",
    }

    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text())
    packages = lock["packages"]
    assert "node_modules/braces" not in packages
    assert packages["node_modules/js-yaml"]["version"] == "5.4.1"
    assert packages["node_modules/katex"]["version"] == "0.18.2"
    assert packages["node_modules/smol-toml"]["version"] == "1.9.0"

    makefile = (ROOT / "Makefile").read_text()
    target = makefile.split("\nlint-markdown:\n", maxsplit=1)[1].split(
        "\n\nlint-fix:", maxsplit=1
    )[0]
    assert ".opencode/node_modules/.bin/markdownlint " in target
    assert ".opencode/node_modules/.bin/markdownlint-cli2" not in target
    assert '--configPointer "/config"' in target

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
