"""Behavioral contract for the repository-owned Markdown lint target."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
FEATURE_DOC = "docs/features/XMSS_BACKEND_SAFETY.md"
CONFIG = "config/rumdl.toml"
RUMDL_VERSION = "0.2.73"


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
        f"RUMDL_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output


def test_lint_markdown_requires_explicit_files() -> None:
    result = _run_make(
        "lint-markdown",
        "MARKDOWN_FILES=",
        f"RUMDL_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Usage: make lint-markdown" in output


def test_lint_markdown_keeps_legacy_config_alias() -> None:
    result = _run_make(
        "lint-markdown",
        f"MARKDOWN_FILES={FEATURE_DOC}",
        f"MARKDOWNLINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output


def test_lint_markdown_rejects_conflicting_config_aliases() -> None:
    result = _run_make(
        "lint-markdown",
        f"MARKDOWN_FILES={FEATURE_DOC}",
        f"RUMDL_CONFIG={CONFIG}",
        "MARKDOWNLINT_CONFIG=config/not-the-rumdl-policy.toml",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "RUMDL_CONFIG and MARKDOWNLINT_CONFIG disagree" in output


def test_lint_markdown_rejects_inline_suppression_directives() -> None:
    directives = (
        "<!-- rumdl-disable-file -->",
        "<!-- markdownlint-disable MD009 -->",
        "<!-- prettier-ignore -->",
    )
    with TemporaryDirectory(prefix=".markdown-lint-test-", dir=ROOT) as directory:
        for index, directive in enumerate(directives):
            markdown_file = Path(directory) / f"directive-{index}.md"
            markdown_file.write_text(f"# Heading\n\n{directive}\n")
            result = _run_make(
                "lint-markdown",
                f"MARKDOWN_FILES={markdown_file}",
                f"RUMDL_CONFIG={CONFIG}",
            )
            output = result.stdout + result.stderr
            assert result.returncode == 2, output
            assert "inline Markdown lint directives are forbidden" in output


def test_lint_markdown_dependency_and_contract_are_exactly_pinned() -> None:
    package = json.loads((ROOT / ".opencode" / "package.json").read_text())
    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text())
    profile = tomllib.loads(
        (ROOT / "requirements" / "profiles" / "dev-quality" / "pyproject.toml")
        .read_text()
    )
    profile_lock = tomllib.loads(
        (ROOT / "requirements" / "profiles" / "dev-quality" / "uv.lock").read_text()
    )

    assert f"rumdl=={RUMDL_VERSION}" in profile["project"]["dependencies"]
    locked_rumdl = next(
        item for item in profile_lock["package"] if item["name"] == "rumdl"
    )
    assert locked_rumdl["version"] == RUMDL_VERSION
    assert "markdownlint-cli" not in package["devDependencies"]
    assert "markdownlint-cli2" not in package["devDependencies"]
    assert "node_modules/markdownlint-cli" not in lock["packages"]
    assert "node_modules/markdownlint-cli2" not in lock["packages"]

    makefile = (ROOT / "make" / "00-foundation.mk").read_text()
    stanza = makefile.split("\nlint-markdown:", 1)[1].split("\n\nlint-css:", 1)[0]
    assert "$(MAKE) --no-print-directory sync" not in stanza
    assert "Run: make sync DEPENDENCY_PROFILE_SET=development" in stanza

    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text()
    )
    entry = next(
        item for item in contract["targets"] if item["name"] == "lint-markdown"
    )
    assert entry["make_variables"] == [
        "MARKDOWN_FILES",
        "RUMDL_CONFIG",
        "MARKDOWNLINT_CONFIG",
    ]
    assert entry["behavior"] == (
        "make lint-markdown "
        "MARKDOWN_FILES=docs/features/XMSS_BACKEND_SAFETY.md "
        "RUMDL_CONFIG=config/rumdl.toml "
        "MARKDOWNLINT_CONFIG=config/rumdl.toml"
    )
