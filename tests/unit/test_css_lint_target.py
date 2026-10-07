"""Behavioral contract for the repository-owned CSS lint target."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
CSS_FILE = "docs/presentation/deck/presentation.css"
ESLINT_CONFIG = "config/eslint-css.config.mjs"
ESLINT_VERSION = "10.11.0"
ESLINT_CSS_VERSION = "2.0.0"


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
        f"ESLINT_CSS_CONFIG={ESLINT_CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output


def test_lint_css_requires_explicit_files() -> None:
    result = _run_make(
        "lint-css",
        "CSS_FILES=",
        f"ESLINT_CSS_CONFIG={ESLINT_CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Usage: make lint-css" in output


def test_lint_css_rejects_each_required_defect() -> None:
    cases = {
        "empty.css": ("a {}\n", "css/no-empty-blocks"),
        "invalid-hex.css": ("a { color: #12; }\n", "no-invalid-properties"),
        "duplicate.css": (
            "a { color: red; color: red; }\n",
            "gludd-css/no-duplicate-properties",
        ),
        "separated-duplicate.css": (
            "a { color: red; background: white; color: blue; }\n",
            "gludd-css/no-duplicate-properties",
        ),
    }
    with TemporaryDirectory(prefix=".css-lint-test-", dir=ROOT) as directory:
        for name, (source, diagnostic) in cases.items():
            css_file = Path(directory) / name
            css_file.write_text(source)
            result = _run_make(
                "lint-css",
                f"CSS_FILES={css_file}",
                f"ESLINT_CSS_CONFIG={ESLINT_CONFIG}",
            )
            output = result.stdout + result.stderr
            assert result.returncode != 0, f"{name} unexpectedly passed:\n{output}"
            assert diagnostic in output, output


def test_lint_css_allows_valid_hex_and_browser_fallback() -> None:
    source = "a { color: #abc; height: 100vh; height: 100svh; }\n"
    with TemporaryDirectory(prefix=".css-lint-test-", dir=ROOT) as directory:
        css_file = Path(directory) / "valid.css"
        css_file.write_text(source)
        result = _run_make(
            "lint-css",
            f"CSS_FILES={css_file}",
            f"ESLINT_CSS_CONFIG={ESLINT_CONFIG}",
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output


def test_lint_css_dependency_config_and_contract_are_exactly_pinned() -> None:
    package = json.loads((ROOT / ".opencode" / "package.json").read_text())
    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text())
    dependencies = package["devDependencies"]
    assert dependencies["@eslint/css"] == ESLINT_CSS_VERSION
    assert dependencies["eslint"] == ESLINT_VERSION
    assert "@biomejs/biome" not in dependencies
    assert "stylelint" not in dependencies
    assert "overrides" not in package
    assert lock["packages"]["node_modules/@eslint/css"]["version"] == (
        ESLINT_CSS_VERSION
    )
    assert lock["packages"]["node_modules/eslint"]["version"] == ESLINT_VERSION
    assert "node_modules/stylelint" not in lock["packages"]

    eslint_config = (ROOT / ESLINT_CONFIG).read_text()
    assert '"css/no-empty-blocks": "error"' in eslint_config
    assert '"css/no-invalid-properties": [' in eslint_config
    assert '"gludd-css/no-duplicate-properties": "error"' in eslint_config
    assert "allowUnknownVariables: true" in eslint_config
    assert "tolerant: false" in eslint_config
    assert "noInlineConfig: true" in eslint_config
    assert 'ignores: [".venv/**", ".opencode/node_modules/**"]' in eslint_config
    assert "previous.value !== value" in eslint_config
    assert "previous.index === index - 1" in eslint_config

    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text()
    )
    entry = next(item for item in contract["targets"] if item["name"] == "lint-css")
    assert entry["make_variables"] == [
        "CSS_FILES",
        "ESLINT_CSS_CONFIG",
    ]
    assert entry["behavior"] == (
        "make lint-css "
        "CSS_FILES=docs/presentation/deck/presentation.css "
        "ESLINT_CSS_CONFIG=config/eslint-css.config.mjs"
    )


def test_hosted_css_lint_calls_repository_target_with_explicit_variables() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
    assert "stylelint" not in workflow.lower()
    assert (
        "make lint-css "
        "CSS_FILES='\"**/*.css\" \"**/*.scss\" \"**/*.less\"' "
        "ESLINT_CSS_CONFIG=config/eslint-css.config.mjs"
    ) in workflow


def test_hosted_node_dependencies_are_audited_after_locked_sync() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
    sync = (
        "make node-deps-sync "
        "NODE_DEPS_VALIDATE_ONLY=0 "
        "NODE_DEPS_NPM_USERCONFIG=/dev/null "
        "NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1 "
        "NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org "
        "NODE_DEPS_NPM_UPDATE_NOTIFIER=false"
    )
    audit = (
        "make node-deps-audit "
        "NODE_DEPS_VALIDATE_ONLY=0 "
        "NODE_DEPS_NPM_USERCONFIG=/dev/null "
        "NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1 "
        "NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org "
        "NODE_DEPS_NPM_UPDATE_NOTIFIER=false "
        "NODE_DEPS_AUDIT_LEVEL=low"
    )
    assert workflow.count(sync) == 1
    assert workflow.count(audit) == 1
    assert workflow.index(sync) < workflow.index(audit)
