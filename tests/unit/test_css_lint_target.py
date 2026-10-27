"""Behavioral contract for the repository-owned CSS lint target."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CSS_FILE = "docs/presentation/deck/presentation.css"
ESLINT_CONFIG = "config/eslint-css.config.mjs"
ESLINT_VERSION = "10.11.0"
ESLINT_CSS_VERSION = "2.0.0"
CSS_TREE_VERSION = "4.1.2"


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


@pytest.mark.parametrize("source", [
    "a { height: 100vh; height: 100svh; height: 100vh; }\n",
    "a { color: red; color: red !important; }\n",
])
def test_lint_css_rejects_duplicates_hidden_by_fallback_or_importance(source: str) -> None:
    """Fallback exceptions must not hide an earlier equal value or importance change."""
    with TemporaryDirectory(prefix=".css-lint-test-", dir=ROOT) as directory:
        css_file = Path(directory) / "duplicate.css"
        css_file.write_text(source)
        result = _run_make(
            "lint-css",
            f"CSS_FILES={css_file}",
            f"ESLINT_CSS_CONFIG={ESLINT_CONFIG}",
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert "gludd-css/no-duplicate-properties" in output, output


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


@pytest.mark.parametrize("source", [
    ":root { --brand: #12; }\n",
    ":root { --brand: linear-gradient(#12, #fff); }\n",
    ":root { --brand: var(--fallback, #12); }\n",
    "@font-palette-values --theme { font-family: Example; override-colors: 0 #12; }\n",
])
def test_lint_css_rejects_invalid_hex_in_every_declaration_context(source: str) -> None:
    """Custom properties, nested values and at-rule descriptors retain hex admission."""
    with TemporaryDirectory(prefix=".css-lint-test-", dir=ROOT) as directory:
        css_file = Path(directory) / "invalid-hex.css"
        css_file.write_text(source)
        result = _run_make(
            "lint-css",
            f"CSS_FILES={css_file}",
            f"ESLINT_CSS_CONFIG={ESLINT_CONFIG}",
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert "gludd-css/no-invalid-hex-colors" in output, output


def test_lint_css_allows_custom_hex_forms_urls_and_strings() -> None:
    """Tokens in strings and URLs are not colors; all four native hex lengths pass."""
    source = (
        ':root { --short: #aBc; --alpha: #AbCd; --long: #aabbcc; --long-alpha: #AABBCCDD; '
        '--fallback: var(--brand, #abc); --label: "#12"; --link: url("#12"); '
        '--raw-link: url(#12); --tokens: { color: #abc; }; }\n'
        '@font-palette-values --theme { font-family: Example; override-colors: 0 #abcd; }\n'
    )
    with TemporaryDirectory(prefix=".css-lint-test-", dir=ROOT) as directory:
        css_file = Path(directory) / "valid.css"
        css_file.write_text(source)
        result = _run_make(
            "lint-css",
            f"CSS_FILES={css_file}",
            f"ESLINT_CSS_CONFIG={ESLINT_CONFIG}",
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_lint_css_dependency_config_and_contract_are_exactly_pinned() -> None:
    package = json.loads((ROOT / ".opencode" / "package.json").read_text())
    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text())
    dependencies = package["devDependencies"]
    assert dependencies["@eslint/css"] == ESLINT_CSS_VERSION
    assert dependencies["@eslint/css-tree"] == CSS_TREE_VERSION
    assert lock["packages"]["node_modules/@eslint/css-tree"]["version"] == CSS_TREE_VERSION
    assert dependencies["eslint"] == ESLINT_VERSION
    assert "@biomejs/biome" not in dependencies
    assert "stylelint" not in dependencies
    assert package.get("overrides") == {
        "@opencode-ai/plugin": {"zod": "4.6.7", "effect": {"toml": "5.0.1"}},
    }
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
    gate = yaml.safe_load(workflow)["jobs"]["gate"]
    commands = "\n".join(step.get("run", "") for step in gate["steps"])
    assert commands.count(sync) == 1
    assert commands.count(audit) == 1
    assert commands.index(sync) < commands.index(audit)
